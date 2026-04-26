"""フキダシ関連 Operator (Phase 3 ページ単位対応).

- 各ページの ``page.balloons`` CollectionProperty にフキダシを追加/削除
- invoke ではマウス直下のページを逆引きして active に追随 (overview 対応)
- 親子連動: 子テキスト (``BNameTextEntry.parent_balloon_id`` でリンク) は
  フキダシの移動に合わせて同じ delta で追随する
- 旧 ``Scene.bname_balloons`` (グローバル) は廃止
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..io import balloon_presets
from ..utils import log

_logger = log.get_logger(__name__)

_SHAPE_FOR_ADD = (
    ("rect", "矩形", ""),
    ("ellipse", "楕円", ""),
    ("cloud", "雲", ""),
    ("spike_curve", "トゲ (曲線)", ""),
    ("spike_straight", "トゲ (直線)", ""),
    ("none", "本体なし (テキスト単体)", ""),
)


def _allocate_balloon_id(page) -> str:
    used = {b.id for b in page.balloons}
    i = 1
    while True:
        candidate = f"balloon_{i:04d}"
        if candidate not in used:
            return candidate
        i += 1


def _resolve_page_from_event(context, event):
    """event.mouse_x/y の位置からアクティブページを逆引き + local mm 座標を返す.

    戻り値: (work, page, local_x_mm, local_y_mm) or (work, page, None, None)
    VIEW_3D 領域外クリック (N パネル等) の場合は active ページのみ返し、
    mm 座標は None。overview OFF モードなら常に active ページ + None。
    """
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    from ..utils import geom, page_grid

    work = get_work(context)
    page = get_active_page(context)
    if work is None or not work.loaded or page is None:
        return work, page, None, None

    screen = getattr(context, "screen", None)
    if screen is None:
        return work, page, None, None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if not (
                region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height
            ):
                continue
            rv3d = getattr(area.spaces.active, "region_3d", None)
            if rv3d is None:
                continue
            mx = event.mouse_x - region.x
            my = event.mouse_y - region.y
            loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
            if loc is None:
                continue
            x_mm = geom.m_to_mm(loc.x)
            y_mm = geom.m_to_mm(loc.y)
            scene = context.scene
            page_idx = page_grid.page_index_at_world_mm(work, scene, x_mm, y_mm)
            if page_idx is not None and 0 <= page_idx < len(work.pages):
                work.active_page_index = page_idx
                page = work.pages[page_idx]
                cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
                gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
                cw = work.paper.canvas_width_mm
                ch = work.paper.canvas_height_mm
                start_side = getattr(work.paper, "start_side", "right")
                read_direction = getattr(work.paper, "read_direction", "left")
                ox, oy = page_grid.page_grid_offset_mm(
                    page_idx, cols, gap, cw, ch, start_side, read_direction
                )
                return work, page, x_mm - ox, y_mm - oy
            return work, page, None, None
    return work, page, None, None


def _default_position_for(work, page, local_x_mm: float | None, local_y_mm: float | None):
    """配置 mm 座標を決定.

    カーソル解決に成功すればその座標、失敗すればキャンバス中央付近を返す。
    """
    if local_x_mm is not None and local_y_mm is not None:
        return local_x_mm, local_y_mm
    paper = work.paper
    return paper.canvas_width_mm / 2.0, paper.canvas_height_mm / 2.0


class BNAME_OT_balloon_add(Operator):
    bl_idname = "bname.balloon_add"
    bl_label = "フキダシを追加"
    bl_options = {"REGISTER", "UNDO"}

    shape: EnumProperty(  # type: ignore[valid-type]
        name="形状",
        items=_SHAPE_FOR_ADD,
        default="rect",
    )
    x_mm: FloatProperty(name="X (mm)", default=0.0)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y (mm)", default=0.0)  # type: ignore[valid-type]
    width_mm: FloatProperty(name="幅 (mm)", default=40.0, min=0.1)  # type: ignore[valid-type]
    height_mm: FloatProperty(name="高さ (mm)", default=20.0, min=0.1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded and get_active_page(context) is not None

    def invoke(self, context, event):
        work, page, lx, ly = _resolve_page_from_event(context, event)
        if work is None or page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        cx, cy = _default_position_for(work, page, lx, ly)
        # 追加時はカーソル位置を左下ではなく中央と解釈し、規定サイズで周囲に広げる
        self.x_mm = cx - self.width_mm / 2.0
        self.y_mm = cy - self.height_mm / 2.0
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        entry = page.balloons.add()
        entry.id = _allocate_balloon_id(page)
        entry.shape = self.shape
        entry.x_mm = self.x_mm
        entry.y_mm = self.y_mm
        entry.width_mm = self.width_mm
        entry.height_mm = self.height_mm
        entry.rounded_corner_enabled = (self.shape == "rect")
        page.active_balloon_index = len(page.balloons) - 1
        if hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "balloon"
        self.report({"INFO"}, f"フキダシ追加: {entry.id} ({self.shape})")
        return {"FINISHED"}


class BNAME_OT_balloon_remove(Operator):
    bl_idname = "bname.balloon_remove"
    bl_label = "フキダシを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_balloon_index < len(page.balloons)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_balloon_index
        if not (0 <= idx < len(page.balloons)):
            return {"CANCELLED"}
        bid = page.balloons[idx].id
        # 子テキストの parent_balloon_id をクリア (孤立テキスト化)
        for txt in page.texts:
            if txt.parent_balloon_id == bid:
                txt.parent_balloon_id = ""
        page.balloons.remove(idx)
        if len(page.balloons) == 0:
            page.active_balloon_index = -1
        elif idx >= len(page.balloons):
            page.active_balloon_index = len(page.balloons) - 1
        if len(page.balloons) == 0 and hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "gp"
        self.report({"INFO"}, f"フキダシ削除: {bid}")
        return {"FINISHED"}


class BNAME_OT_balloon_tail_add(Operator):
    bl_idname = "bname.balloon_tail_add"
    bl_label = "尻尾を追加"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_balloon_index < len(page.balloons)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_balloon_index
        if not (0 <= idx < len(page.balloons)):
            return {"CANCELLED"}
        entry = page.balloons[idx]
        tail = entry.tails.add()
        tail.type = "straight"
        tail.length_mm = 6.0
        tail.root_width_mm = 3.0
        return {"FINISHED"}


class BNAME_OT_balloon_move(Operator):
    """アクティブフキダシを delta だけ平行移動. 子テキストも連動.

    UI の数値ドラッグではなく、親子連動を保証するための専用オペレータ。
    N パネルのフキダシ詳細 UI から x_mm/y_mm を直接編集した場合は
    連動しない (ユーザーが意図的に独立移動したとみなす)。
    """

    bl_idname = "bname.balloon_move"
    bl_label = "フキダシを平行移動"
    bl_options = {"REGISTER", "UNDO"}

    delta_x_mm: FloatProperty(name="ΔX (mm)", default=0.0)  # type: ignore[valid-type]
    delta_y_mm: FloatProperty(name="ΔY (mm)", default=0.0)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_balloon_index < len(page.balloons)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_balloon_index
        if not (0 <= idx < len(page.balloons)):
            return {"CANCELLED"}
        entry = page.balloons[idx]
        dx = float(self.delta_x_mm)
        dy = float(self.delta_y_mm)
        entry.x_mm += dx
        entry.y_mm += dy
        # 親子連動: 子テキストも同じ delta で追随
        for txt in page.texts:
            if txt.parent_balloon_id == entry.id:
                txt.x_mm += dx
                txt.y_mm += dy
        return {"FINISHED"}


class BNAME_OT_balloon_save_preset(Operator):
    """選択中フキダシの形状をカスタムプリセット JSON として保存."""

    bl_idname = "bname.balloon_save_preset"
    bl_label = "カスタム形状として保存"
    bl_options = {"REGISTER"}

    preset_name: StringProperty(name="プリセット名", default="新規フキダシ")  # type: ignore[valid-type]
    description: StringProperty(name="説明", default="")  # type: ignore[valid-type]
    absolute_coords: BoolProperty(name="絶対座標で登録", default=False)  # type: ignore[valid-type]
    to_global: BoolProperty(  # type: ignore[valid-type]
        name="グローバルに登録",
        description="ON: <addon>/presets/balloons/ に保存 / OFF: 作品ローカル",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_balloon_index < len(page.balloons)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_balloon_index
        entry = page.balloons[idx]
        # Phase 3 骨格: 矩形 4 頂点を保存。パスツール実装後は任意形状へ。
        verts = [
            (entry.x_mm, entry.y_mm),
            (entry.x_mm + entry.width_mm, entry.y_mm),
            (entry.x_mm + entry.width_mm, entry.y_mm + entry.height_mm),
            (entry.x_mm, entry.y_mm + entry.height_mm),
        ]
        try:
            if self.to_global:
                out = balloon_presets.save_global_preset(
                    self.preset_name, self.description, verts, self.absolute_coords
                )
            else:
                work = get_work(context)
                if work is None or not work.loaded or not work.work_dir:
                    self.report({"ERROR"}, "ローカル保存には作品を開く必要があります")
                    return {"CANCELLED"}
                out = balloon_presets.save_local_preset(
                    Path(work.work_dir),
                    self.preset_name,
                    self.description,
                    verts,
                    self.absolute_coords,
                )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("balloon_save_preset failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"フキダシプリセット保存: {out.name}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_balloon_add,
    BNAME_OT_balloon_remove,
    BNAME_OT_balloon_tail_add,
    BNAME_OT_balloon_move,
    BNAME_OT_balloon_save_preset,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
