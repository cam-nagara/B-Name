"""ビューポート視点制御オペレータ.

- bname.view_fit_page: 全ページ一覧モードのままアクティブページへフォーカス
- bname.view_fit_all: 全ページ一覧モードを ON にして全ページを収める

全ページ一覧モードでは、ui/overlay.py 側で ``scene.bname_overview_mode``
を参照し、全ページを grid レイアウトで並べて描画する。
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator

from ..core.mode import MODE_PAGE, get_mode
from ..core.work import get_work
from ..utils import geom, log, page_browser

_logger = log.get_logger(__name__)

_PAGE_BROWSER_DEFAULT_RATIO = 0.28
_PAGE_BROWSER_AREA_SIZES: dict[int, tuple[int, int]] = {}


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


def _find_window_region(area):
    if area is None:
        return None
    for region in getattr(area, "regions", []):
        if getattr(region, "type", "") == "WINDOW":
            return region
    return None


def _context_screen(context):
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None)
    return screen or getattr(context, "screen", None)


def _largest_view3d_area(screen, *, exclude_browser: bool = True):
    areas = []
    for area in page_browser.view3d_areas(screen):
        if exclude_browser and page_browser.is_marked_area(area):
            continue
        areas.append(area)
    if not areas:
        return None
    return max(areas, key=lambda area: int(getattr(area, "width", 0)) * int(getattr(area, "height", 0)))


def _fit_page_browser_area(context, area) -> bool:
    work = get_work(context)
    if work is None or not work.loaded or len(work.pages) == 0:
        return False
    region = _find_window_region(area)
    if region is None:
        return False
    ok = True
    if page_browser.fit_enabled(context.scene):
        fit_rect = _page_browser_fit_rect_mm(context, area, region, work)
        if fit_rect is None:
            return False
        ok = _fit_view_to_rect_mm(context, area, region, *fit_rect)
    try:
        space = area.spaces.active
        rv3d = getattr(space, "region_3d", None)
        if rv3d is not None:
            rv3d.view_perspective = "ORTHO"
        overlay = getattr(space, "overlay", None)
        if overlay is not None:
            overlay.show_overlays = False
        shading = getattr(space, "shading", None)
        if shading is not None:
            shading.type = "SOLID"
            shading.light = "FLAT"
            shading.background_type = "THEME"
    except Exception:  # noqa: BLE001
        pass
    try:
        area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass
    return ok


def _page_browser_fit_rect_mm(context, area, region, work) -> tuple[float, float, float, float] | None:
    bbox = page_browser.layout_bbox_mm(work, context.scene, area)
    if bbox is None:
        return None
    x, y, w, h = bbox
    paper = work.paper
    cw = float(paper.canvas_width_mm)
    ch = float(paper.canvas_height_mm)
    pad = max(10.0, float(getattr(context.scene, "bname_overview_gap_mm", 30.0)) * 0.5)
    aspect = max(0.01, float(getattr(region, "width", 1)) / max(1.0, float(getattr(region, "height", 1))))
    if page_browser.is_vertical_area(area):
        target_w = max(1.0, min(max(w, cw), cw * 2.0) + pad * 2.0)
        target_h = target_w / aspect
        return (x - pad, y + h + pad - target_h, target_w, target_h)
    target_h = max(1.0, ch + pad * 2.0)
    target_w = target_h * aspect
    read_direction = getattr(paper, "read_direction", "left")
    if read_direction == "left":
        target_x = x + w + pad - target_w
    else:
        target_x = x - pad
    return (target_x, y - pad, target_w, target_h)


def _workspace_name_available(name: str) -> str:
    existing = {workspace.name for workspace in bpy.data.workspaces}
    if name not in existing:
        return name
    index = 1
    while True:
        candidate = f"{name}.{index:03d}"
        if candidate not in existing:
            return candidate
        index += 1


def _activate_or_create_page_workspace(context, position: str):
    window = getattr(context, "window", None)
    if window is None:
        return getattr(context, "workspace", None)
    for workspace in bpy.data.workspaces:
        if page_browser.is_page_browser_workspace(workspace) or workspace.name == page_browser.WORKSPACE_NAME:
            window.workspace = workspace
            page_browser.mark_workspace(workspace, position)
            return workspace
    try:
        bpy.ops.workspace.duplicate()
        workspace = window.workspace
    except Exception:  # noqa: BLE001
        workspace = getattr(context, "workspace", None)
    if workspace is not None:
        try:
            workspace.name = _workspace_name_available(page_browser.WORKSPACE_NAME)
        except Exception:  # noqa: BLE001
            pass
        page_browser.mark_workspace(workspace, position)
    return workspace


def _split_area_for_page_browser(context, base_area, position: str, ratio: float):
    screen = _context_screen(context)
    if screen is None or base_area is None:
        return None
    before = {page_browser.area_key(area) for area in getattr(screen, "areas", [])}
    pos = page_browser.normalize_position(position)
    direction = "VERTICAL" if pos in {"LEFT", "RIGHT"} else "HORIZONTAL"
    browser_ratio = max(0.12, min(0.5, float(ratio)))
    factor = browser_ratio if pos in {"LEFT", "BOTTOM"} else 1.0 - browser_ratio
    try:
        with context.temp_override(screen=screen, area=base_area):
            result = bpy.ops.screen.area_split(direction=direction, factor=factor)
        if "FINISHED" not in result:
            return None
    except Exception:  # noqa: BLE001
        _logger.exception("page browser area split failed")
        return None

    candidates = [
        area
        for area in page_browser.view3d_areas(screen)
        if page_browser.area_key(area) not in before or area == base_area
    ]
    if not candidates:
        candidates = page_browser.view3d_areas(screen)
    if pos == "LEFT":
        return min(candidates, key=lambda area: getattr(area, "x", 0))
    if pos == "RIGHT":
        return max(candidates, key=lambda area: getattr(area, "x", 0) + getattr(area, "width", 0))
    if pos == "TOP":
        return max(candidates, key=lambda area: getattr(area, "y", 0) + getattr(area, "height", 0))
    return min(candidates, key=lambda area: getattr(area, "y", 0))


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

    ``start_side`` / ``read_direction`` / overview cols を反映した実配置から
    bbox を算出する。
    """
    n = len(work.pages)
    if n == 0:
        return None
    scene = bpy.context.scene
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    cw = work.paper.canvas_width_mm
    ch = work.paper.canvas_height_mm
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    from ..utils import page_grid

    min_x = None
    min_y = None
    max_x = None
    max_y = None
    for i in range(n):
        ox, oy = page_grid.page_grid_offset_mm(
            i, cols, gap, cw, ch, start_side, read_direction
        )
        add_x, add_y = page_grid.page_manual_offset_mm(work.pages[i])
        ox += add_x
        oy += add_y
        x0 = ox
        y0 = oy
        x1 = ox + cw
        y1 = oy + ch
        min_x = x0 if min_x is None else min(min_x, x0)
        min_y = y0 if min_y is None else min(min_y, y0)
        max_x = x1 if max_x is None else max(max_x, x1)
        max_y = y1 if max_y is None else max(max_y, y1)
    if min_x is None or min_y is None or max_x is None or max_y is None:
        return None
    return (min_x, min_y, max_x - min_x, max_y - min_y)


# ---------- オペレータ ----------


class BNAME_OT_view_fit_page(Operator):
    """全ページ一覧モードのままアクティブページを画面にフィット."""

    bl_idname = "bname.view_fit_page"
    bl_label = "ページに合わせる"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return (
            work is not None
            and work.loaded
            and len(work.pages) > 0
            and 0 <= int(getattr(work, "active_page_index", -1)) < len(work.pages)
            and get_mode(context) == MODE_PAGE
        )

    def execute(self, context):
        work = get_work(context)
        if (
            work is None
            or len(work.pages) == 0
            or not (0 <= int(getattr(work, "active_page_index", -1)) < len(work.pages))
        ):
            return {"CANCELLED"}
        scene = context.scene
        scene.bname_overview_mode = True
        info = _find_view3d_region(context)
        if info is None:
            self.report({"ERROR"}, "3D ビューポートが見つかりません")
            return {"CANCELLED"}
        area, region, _rv3d = info
        p = work.paper
        # アクティブページの grid 上の実位置にフィットする (master GP / 見開きペア
        # 配置で active page の world 座標は (0,0) とは限らないため)。
        from ..utils import page_grid

        cols = max(2, int(getattr(scene, "bname_overview_cols", 4)))
        gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
        start_side = getattr(p, "start_side", "right")
        read_direction = getattr(p, "read_direction", "left")
        idx = max(0, work.active_page_index) if len(work.pages) > 0 else 0
        ox, oy = page_grid.page_grid_offset_mm(
            idx, cols, gap, p.canvas_width_mm, p.canvas_height_mm,
            start_side, read_direction,
        )
        add_x, add_y = page_grid.page_manual_offset_mm(work.pages[idx])
        ox += add_x
        oy += add_y
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
        return (
            work is not None
            and work.loaded
            and len(work.pages) > 0
            and get_mode(context) == MODE_PAGE
        )

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
    """互換用: 全ページ一覧モードを ON に戻す."""

    bl_idname = "bname.view_overview_toggle"
    bl_label = "一覧モードに戻す"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded and get_mode(context) == MODE_PAGE

    def execute(self, context):
        scene = context.scene
        scene.bname_overview_mode = True
        for a in context.screen.areas:
            if a.type == "VIEW_3D":
                a.tag_redraw()
        return {"FINISHED"}


class BNAME_OT_page_browser_workspace(Operator):
    """ページ一覧専用ワークスペースを作成/表示し、3D View をページ一覧ビューにする."""

    bl_idname = "bname.page_browser_workspace"
    bl_label = "ページ一覧ワークスペースを開く"
    bl_options = {"REGISTER"}

    position: EnumProperty(  # type: ignore[valid-type]
        name="表示位置",
        items=page_browser.POSITION_ITEMS,
        default="LEFT",
    )

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work is not None and work.loaded and len(work.pages) > 0)

    def invoke(self, context, _event):
        self.position = page_browser.normalize_position(
            getattr(context.scene, "bname_page_browser_position", "LEFT")
        )
        return self.execute(context)

    def execute(self, context):
        position = page_browser.normalize_position(self.position)
        context.scene.bname_page_browser_position = position
        workspace = _activate_or_create_page_workspace(context, position)
        if workspace is not None:
            page_browser.mark_workspace(workspace, position)

        screen = _context_screen(context)
        if screen is None:
            self.report({"ERROR"}, "画面レイアウトが見つかりません")
            return {"CANCELLED"}
        ratio = float(getattr(context.scene, "bname_page_browser_size", _PAGE_BROWSER_DEFAULT_RATIO))
        view_areas = page_browser.view3d_areas(screen)
        marked = page_browser.marked_view3d_areas(screen)
        current_browser = marked[0] if marked else None
        desired_edge = page_browser.edge_view3d_area(screen, position) if len(view_areas) > 1 else None
        browser_area = current_browser if current_browser is not None and current_browser == desired_edge else None
        needs_split = len(view_areas) <= 1 or (
            current_browser is not None and current_browser != desired_edge
        )
        if browser_area is None and needs_split:
            base_area = _largest_view3d_area(screen, exclude_browser=current_browser is not None)
            if base_area is None or base_area == current_browser:
                candidates = [area for area in view_areas if area != current_browser]
                base_area = max(
                    candidates,
                    key=lambda area: int(getattr(area, "width", 0)) * int(getattr(area, "height", 0)),
                    default=current_browser,
                )
            browser_area = _split_area_for_page_browser(context, base_area, position, ratio)
        if browser_area is None:
            browser_area = desired_edge
        if browser_area is None:
            browser_area = _largest_view3d_area(screen, exclude_browser=False)
        if browser_area is None:
            self.report({"ERROR"}, "3D ビューポートが見つかりません")
            return {"CANCELLED"}

        page_browser.clear_screen_marks(screen)
        page_browser.mark_area(browser_area)
        if not _fit_page_browser_area(context, browser_area):
            self.report({"WARNING"}, "ページ一覧ビューのフィットに失敗しました")
        page_browser.tag_page_browser_redraw(context)
        labels = {identifier: label for identifier, label, _description in page_browser.POSITION_ITEMS}
        self.report({"INFO"}, f"ページ一覧ビューを{labels.get(position, position)}に表示")
        return {"FINISHED"}


class BNAME_OT_page_browser_mark_area(Operator):
    """現在の 3D View をページ一覧ビューとして扱う."""

    bl_idname = "bname.page_browser_mark_area"
    bl_label = "この3Dビューをページ一覧にする"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(
            work is not None
            and work.loaded
            and getattr(context, "area", None) is not None
            and context.area.type == "VIEW_3D"
        )

    def execute(self, context):
        area = context.area
        screen = getattr(context, "screen", None)
        position = page_browser.normalize_position(
            getattr(context.scene, "bname_page_browser_position", "LEFT")
        )
        if screen is not None:
            page_browser.clear_screen_marks(screen)
        page_browser.mark_area(area)
        page_browser.mark_workspace(getattr(context, "workspace", None), position)
        if not _fit_page_browser_area(context, area):
            self.report({"WARNING"}, "ページ一覧ビューのフィットに失敗しました")
        page_browser.tag_page_browser_redraw(context)
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_view_fit_page,
    BNAME_OT_view_fit_all,
    BNAME_OT_view_overview_toggle,
    BNAME_OT_page_browser_workspace,
    BNAME_OT_page_browser_mark_area,
)


def _on_overview_layout_changed(_self, context) -> None:
    """cols / gap_mm 変更時にページ Collection の grid 配置を追随させる."""
    try:
        from ..utils import page_grid

        work = get_work(context)
        if work is not None and work.loaded:
            page_grid.apply_page_collection_transforms(context, work)
            _fit_page_browser_areas(context)
    except Exception:  # noqa: BLE001
        pass


def _fit_page_browser_areas(context) -> None:
    if context is None:
        context = bpy.context
    if not page_browser.fit_enabled(getattr(context, "scene", None)):
        page_browser.tag_page_browser_redraw(context)
        return
    for area in page_browser.iter_page_browser_areas(context):
        try:
            _fit_page_browser_area(context, area)
        except Exception:  # noqa: BLE001
            _logger.exception("page browser refit failed")
    page_browser.tag_page_browser_redraw(context)


def _on_page_browser_fit_changed(_self, context) -> None:
    _PAGE_BROWSER_AREA_SIZES.clear()
    _fit_page_browser_areas(context)


def _page_browser_fit_watcher():
    try:
        context = bpy.context
        scene = getattr(context, "scene", None)
        if scene is None or not page_browser.fit_enabled(scene):
            _PAGE_BROWSER_AREA_SIZES.clear()
            return 0.5
        work = get_work(context)
        if work is None or not work.loaded:
            return 0.5
        for area in page_browser.iter_page_browser_areas(context):
            key = page_browser.area_key(area)
            size = (int(getattr(area, "width", 0)), int(getattr(area, "height", 0)))
            if key and _PAGE_BROWSER_AREA_SIZES.get(key) != size:
                _PAGE_BROWSER_AREA_SIZES[key] = size
                _fit_page_browser_area(context, area)
    except Exception:  # noqa: BLE001
        _logger.exception("page browser fit watcher failed")
    return 0.5


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
    bpy.types.Scene.bname_page_browser_position = EnumProperty(
        name="ページ一覧の位置",
        description="ページ一覧専用ビューを表示する位置",
        items=page_browser.POSITION_ITEMS,
        default="LEFT",
    )
    bpy.types.Scene.bname_page_browser_size = FloatProperty(
        name="ページ一覧の幅",
        description="ページ一覧専用ビューの分割比率",
        default=_PAGE_BROWSER_DEFAULT_RATIO,
        min=0.12,
        max=0.5,
        subtype="FACTOR",
    )
    bpy.types.Scene.bname_page_browser_fit = BoolProperty(
        name="フィット",
        description="ページ一覧ビューをパネルの縦横比に合わせて表示",
        default=True,
        update=_on_page_browser_fit_changed,
    )
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    if not bpy.app.timers.is_registered(_page_browser_fit_watcher):
        bpy.app.timers.register(_page_browser_fit_watcher, first_interval=0.5, persistent=True)


def unregister() -> None:
    if bpy.app.timers.is_registered(_page_browser_fit_watcher):
        try:
            bpy.app.timers.unregister(_page_browser_fit_watcher)
        except ValueError:
            pass
    _PAGE_BROWSER_AREA_SIZES.clear()
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    for prop in (
        "bname_overview_mode",
        "bname_overview_cols",
        "bname_overview_gap_mm",
        "bname_page_browser_position",
        "bname_page_browser_size",
        "bname_page_browser_fit",
    ):
        try:
            delattr(bpy.types.Scene, prop)
        except (AttributeError, RuntimeError):
            pass
