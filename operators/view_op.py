"""ビューポート視点制御オペレータ.

- bname.view_fit_page: アクティブページを画面にフィット (トップ+正投影)
- bname.view_fit_all: 全ページ一覧モードを ON にして全ページを収める
- bname.view_overview_toggle: 全ページ一覧モードの ON/OFF 切替

全ページ一覧モードでは、ui/overlay.py 側で ``scene.bname_overview_mode``
を参照し、全ページを grid レイアウトで並べて描画する。
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty
from bpy.types import Operator

from ..core.work import get_work
from ..utils import geom, log

_logger = log.get_logger(__name__)


# ---------- 共通ヘルパ ----------


def _find_view3d_region(context):
    """現在のウィンドウから VIEW_3D の (area, region, rv3d) を返す."""
    area = getattr(context, "area", None)
    if area is None or area.type != "VIEW_3D":
        screen = getattr(context, "screen", None)
        if screen is None:
            return None
        for a in screen.areas:
            if a.type == "VIEW_3D":
                area = a
                break
        else:
            return None
    region = None
    for r in area.regions:
        if r.type == "WINDOW":
            region = r
            break
    if region is None:
        return None
    space = area.spaces.active
    rv3d = getattr(space, "region_3d", None)
    if rv3d is None:
        return None
    return area, region, rv3d


def _fit_view_to_rect_mm(
    context, area, region, x_mm: float, y_mm: float, w_mm: float, h_mm: float
) -> bool:
    """ビューを指定矩形 (mm) にフィット。トップ+正投影にする。

    一時 Empty を 4 隅に配置し ``view3d.view_selected`` で Blender の
    フィット処理を利用することで、アスペクト比を正確に扱う。
    """
    scene = context.scene
    empties = []
    corners_m = [
        (geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0),
        (geom.mm_to_m(x_mm + w_mm), geom.mm_to_m(y_mm), 0.0),
        (geom.mm_to_m(x_mm + w_mm), geom.mm_to_m(y_mm + h_mm), 0.0),
        (geom.mm_to_m(x_mm), geom.mm_to_m(y_mm + h_mm), 0.0),
    ]
    for i, loc in enumerate(corners_m):
        e = bpy.data.objects.new(f"_bname_fit_{i}", None)
        e.location = loc
        e.empty_display_size = 0.0001
        e.hide_render = True
        scene.collection.objects.link(e)
        empties.append(e)

    # 選択状態の退避
    prev_active = context.view_layer.objects.active
    prev_selected_names = [obj.name for obj in context.selected_objects]

    try:
        for obj in list(context.selected_objects):
            try:
                obj.select_set(False)
            except Exception:  # noqa: BLE001
                pass
        for e in empties:
            e.select_set(True)
        context.view_layer.objects.active = empties[0]

        space = area.spaces.active
        rv3d = space.region_3d
        with context.temp_override(area=area, region=region):
            bpy.ops.view3d.view_axis(type="TOP")
            if rv3d.view_perspective != "ORTHO":
                rv3d.view_perspective = "ORTHO"
            bpy.ops.view3d.view_selected(use_all_regions=False)
        return True
    finally:
        for e in empties:
            try:
                bpy.data.objects.remove(e, do_unlink=True)
            except Exception:  # noqa: BLE001
                pass
        # 選択の復元
        for name in prev_selected_names:
            obj = bpy.data.objects.get(name)
            if obj is not None:
                try:
                    obj.select_set(True)
                except Exception:  # noqa: BLE001
                    pass
        if prev_active is not None:
            try:
                context.view_layer.objects.active = prev_active
            except Exception:  # noqa: BLE001
                pass


def _overview_layout_bbox(work) -> tuple[float, float, float, float] | None:
    """全ページ一覧時の全体 bbox (x, y, w, h) を mm で返す.

    ページは右→左 (漫画読順) に展開するため、ページ 0001 が x=0、以降は負の
    X 方向に展開される。bbox の左端 (min_x) は負、右端 (0 + cw) が max_x。
    """
    n = len(work.pages)
    if n == 0:
        return None
    scene = bpy.context.scene
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    cw = work.paper.canvas_width_mm
    ch = work.paper.canvas_height_mm
    rows = (n + cols - 1) // cols
    used_cols = min(n, cols)
    total_w = used_cols * cw + max(0, used_cols - 1) * gap
    total_h = rows * ch + max(0, rows - 1) * gap
    # 最左列の左端: -(used_cols - 1) * (cw + gap)
    min_x = -((used_cols - 1) * (cw + gap))
    # 最下行の底: -(rows - 1) * (ch + gap)
    min_y = -((rows - 1) * (ch + gap))
    return (min_x, min_y, total_w, total_h)


# ---------- オペレータ ----------


class BNAME_OT_view_fit_page(Operator):
    """アクティブページを画面にフィット (トップ+正投影)."""

    bl_idname = "bname.view_fit_page"
    bl_label = "ページに合わせる"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded

    def execute(self, context):
        work = get_work(context)
        if work is None:
            return {"CANCELLED"}
        # overview_mode を OFF にする (真正面に戻す意図と合致)
        scene = context.scene
        if getattr(scene, "bname_overview_mode", False):
            scene.bname_overview_mode = False
        info = _find_view3d_region(context)
        if info is None:
            self.report({"ERROR"}, "3D ビューポートが見つかりません")
            return {"CANCELLED"}
        area, region, _rv3d = info
        p = work.paper
        # アクティブページの grid 上の実位置にフィットする (master GP / 見開きペア
        # 配置で active page の world 座標は (0,0) とは限らないため)。
        from ..utils.page_grid import page_grid_offset_mm as _pg_offset

        cols = max(2, int(getattr(scene, "bname_overview_cols", 4)))
        gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
        start_side = getattr(p, "start_side", "right")
        read_direction = getattr(p, "read_direction", "left")
        idx = max(0, work.active_page_index) if len(work.pages) > 0 else 0
        ox, oy = _pg_offset(
            idx, cols, gap, p.canvas_width_mm, p.canvas_height_mm,
            start_side, read_direction,
        )
        ok = _fit_view_to_rect_mm(
            context, area, region, ox, oy, p.canvas_width_mm, p.canvas_height_mm
        )
        if not ok:
            self.report({"ERROR"}, "フィットに失敗しました")
            return {"CANCELLED"}
        # 画面リドロー
        for a in context.screen.areas:
            if a.type == "VIEW_3D":
                a.tag_redraw()
        self.report({"INFO"}, "ページに合わせました")
        return {"FINISHED"}


class BNAME_OT_view_fit_all(Operator):
    """全ページ一覧モードを ON にして、全ページが収まるよう画面にフィット."""

    bl_idname = "bname.view_fit_all"
    bl_label = "全ページを一覧表示"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded and len(work.pages) > 0

    def execute(self, context):
        work = get_work(context)
        if work is None or len(work.pages) == 0:
            return {"CANCELLED"}
        scene = context.scene
        scene.bname_overview_mode = True
        bbox = _overview_layout_bbox(work)
        if bbox is None:
            self.report({"ERROR"}, "ページがありません")
            return {"CANCELLED"}
        info = _find_view3d_region(context)
        if info is None:
            self.report({"ERROR"}, "3D ビューポートが見つかりません")
            return {"CANCELLED"}
        area, region, _rv3d = info
        x, y, w, h = bbox
        ok = _fit_view_to_rect_mm(context, area, region, x, y, w, h)
        if not ok:
            self.report({"ERROR"}, "フィットに失敗しました")
            return {"CANCELLED"}
        for a in context.screen.areas:
            if a.type == "VIEW_3D":
                a.tag_redraw()
        self.report({"INFO"}, f"{len(work.pages)} ページを一覧表示")
        return {"FINISHED"}


class BNAME_OT_view_overview_toggle(Operator):
    """全ページ一覧モードの ON/OFF 切替."""

    bl_idname = "bname.view_overview_toggle"
    bl_label = "一覧モード切替"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        scene = context.scene
        cur = getattr(scene, "bname_overview_mode", False)
        scene.bname_overview_mode = not cur
        for a in context.screen.areas:
            if a.type == "VIEW_3D":
                a.tag_redraw()
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_view_fit_page,
    BNAME_OT_view_fit_all,
    BNAME_OT_view_overview_toggle,
)


def _on_overview_layout_changed(_self, context) -> None:
    """cols / gap_mm 変更時にページ Collection の grid 配置を追随させる."""
    try:
        from ..utils import page_grid

        work = get_work(context)
        if work is not None and work.loaded:
            page_grid.apply_page_collection_transforms(context, work)
    except Exception:  # noqa: BLE001
        pass


def _on_overview_cols_changed(self, context) -> None:
    """列数を強制的に偶数化 (見開みかいペアが分断されないよう).

    step=2 の IntProperty は UI クリックでは 2 刻みになるが、ユーザーが
    直接数値入力した場合に奇数 (3, 5, 7...) を許してしまうため、ここで
    端数を切り上げて偶数に丸める。
    """
    try:
        cols = int(getattr(context.scene, "bname_overview_cols", 4))
        if cols < 2:
            target = 2
        elif cols % 2 != 0:
            target = cols + 1  # 奇数 → 次の偶数に切り上げ
        else:
            target = cols
        if target != cols:
            # 再帰 update を避けるため値が違うときだけ書き戻す
            context.scene.bname_overview_cols = target
            return  # 書き戻しの update で _on_overview_layout_changed が呼ばれる
    except Exception:  # noqa: BLE001
        pass
    _on_overview_layout_changed(self, context)


def register() -> None:
    # Scene プロパティ登録 (overview 用)
    bpy.types.Scene.bname_overview_mode = BoolProperty(
        name="全ページ一覧モード",
        description="ON で全ページを grid レイアウトで表示 (描画専用、保存に影響しない)",
        default=True,
    )
    bpy.types.Scene.bname_overview_cols = IntProperty(
        name="一覧の列数",
        description="全ページ一覧時の横方向ページ数 (見開みかいペアが分断されないよう偶数刻み)",
        default=4,
        min=2,
        soft_max=12,
        step=2,
        update=_on_overview_cols_changed,
    )
    bpy.types.Scene.bname_overview_gap_mm = FloatProperty(
        name="一覧のページ間隔 (mm)",
        description="全ページ一覧時のページ同士の余白",
        default=30.0,
        min=0.0,
        soft_max=200.0,
        update=_on_overview_layout_changed,
    )
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    for prop in ("bname_overview_mode", "bname_overview_cols", "bname_overview_gap_mm"):
        try:
            delattr(bpy.types.Scene, prop)
        except (AttributeError, RuntimeError):
            pass
