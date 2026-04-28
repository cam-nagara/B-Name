"""フキダシ関連 Operator (Phase 3 ページ単位対応).

- 各ページの ``page.balloons`` CollectionProperty にフキダシを追加/削除
- invoke ではマウス直下のページを逆引きして active に追随 (overview 対応)
- 親子連動: 子テキスト (``BNameTextEntry.parent_balloon_id`` でリンク) は
  フキダシの移動に合わせて同じ delta で追随する
- 旧 ``Scene.bname_balloons`` (グローバル) は廃止
"""

from __future__ import annotations

import math
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..io import balloon_presets
from ..utils import detail_popup, layer_stack as layer_stack_utils, log, object_selection
from ..utils.layer_hierarchy import page_stack_key
from . import panel_modal_state, view_event_region

_logger = log.get_logger(__name__)

_BALLOON_DEFAULT_SHAPE = "ellipse"
_BALLOON_MIN_SIZE_MM = 2.0
_BALLOON_HANDLE_HIT_MM = 2.5
_BALLOON_DRAG_EPS_MM = 0.05
_BALLOON_TAIL_MIN_LENGTH_MM = 2.0

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

    view = view_event_region.view3d_window_under_event(context, event)
    if view is None:
        return work, page, None, None
    _area, region, rv3d, mx, my = view
    loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
    if loc is None:
        return work, page, None, None
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
        add_x, add_y = page_grid.page_manual_offset_mm(page)
        ox += add_x
        oy += add_y
        return work, page, x_mm - ox, y_mm - oy
    return work, page, None, None


def _find_page_with_index_by_id(work, page_id: str):
    if work is None:
        return -1, None
    for i, page in enumerate(work.pages):
        if getattr(page, "id", "") == page_id:
            return i, page
    return -1, None


def _event_world_xy_mm(context, event) -> tuple[float | None, float | None]:
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    from ..utils import geom

    view = view_event_region.view3d_window_under_event(context, event)
    if view is None:
        return None, None
    _area, region, rv3d, mx, my = view
    loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
    if loc is None:
        return None, None
    return geom.m_to_mm(loc.x), geom.m_to_mm(loc.y)


def _resolve_local_xy_for_page_from_event(context, event, page_id: str):
    from ..utils import page_grid

    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return None, None, None, None
    page_index, page = _find_page_with_index_by_id(work, page_id)
    if page is None:
        return work, None, None, None
    world_x_mm, world_y_mm = _event_world_xy_mm(context, event)
    if world_x_mm is None or world_y_mm is None:
        return work, page, None, None
    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, page_index)
    return work, page, world_x_mm - ox_mm, world_y_mm - oy_mm


def _default_position_for(work, page, local_x_mm: float | None, local_y_mm: float | None):
    """配置 mm 座標を決定.

    カーソル解決に成功すればその座標、失敗すればキャンバス中央付近を返す。
    """
    if local_x_mm is not None and local_y_mm is not None:
        return local_x_mm, local_y_mm
    paper = work.paper
    return paper.canvas_width_mm / 2.0, paper.canvas_height_mm / 2.0


def _creation_violates_layer_scope(context, page, x_mm: float, y_mm: float, width_mm: float, height_mm: float) -> bool:
    from ..core.mode import MODE_PANEL, MODE_PAGE, get_mode
    from ..utils import layer_stack

    cx = x_mm + width_mm * 0.5
    cy = y_mm + height_mm * 0.5
    mode = get_mode(context)
    if mode == MODE_PAGE:
        return False
    if mode == MODE_PANEL:
        idx = int(getattr(page, "active_panel_index", -1))
        if not (0 <= idx < len(page.panels)):
            return False
        hit = layer_stack.panel_containing_point(page, cx, cy)
        return (
            hit is None
            or str(getattr(hit, "panel_stem", "") or "")
            != str(getattr(page.panels[idx], "panel_stem", "") or "")
        )
    return False


def _balloon_rect(entry) -> tuple[float, float, float, float]:
    x = float(getattr(entry, "x_mm", 0.0))
    y = float(getattr(entry, "y_mm", 0.0))
    w = float(getattr(entry, "width_mm", 0.0))
    h = float(getattr(entry, "height_mm", 0.0))
    return x, y, x + w, y + h


def _balloon_hit_part(entry, x_mm: float, y_mm: float) -> str:
    left, bottom, right, top = _balloon_rect(entry)
    width = max(0.0, right - left)
    height = max(0.0, top - bottom)
    threshold = min(
        _BALLOON_HANDLE_HIT_MM,
        max(0.35, min(width, height) * 0.25),
    )
    if not (
        left - threshold <= x_mm <= right + threshold
        and bottom - threshold <= y_mm <= top + threshold
    ):
        return ""
    near_left = abs(x_mm - left) <= threshold
    near_right = abs(x_mm - right) <= threshold
    near_bottom = abs(y_mm - bottom) <= threshold
    near_top = abs(y_mm - top) <= threshold
    inside_x = left <= x_mm <= right
    inside_y = bottom <= y_mm <= top
    if near_left and near_top:
        return "top_left"
    if near_right and near_top:
        return "top_right"
    if near_left and near_bottom:
        return "bottom_left"
    if near_right and near_bottom:
        return "bottom_right"
    if near_left and inside_y:
        return "left"
    if near_right and inside_y:
        return "right"
    if near_top and inside_x:
        return "top"
    if near_bottom and inside_x:
        return "bottom"
    if inside_x and inside_y:
        return "body"
    return ""


def _hit_balloon_entry(page, x_mm: float, y_mm: float):
    active_idx = int(getattr(page, "active_balloon_index", -1))
    indices: list[int] = []
    if 0 <= active_idx < len(page.balloons):
        indices.append(active_idx)
    indices.extend(i for i in reversed(range(len(page.balloons))) if i != active_idx)
    for idx in indices:
        entry = page.balloons[idx]
        if getattr(entry, "shape", "rect") == "none":
            continue
        part = _balloon_hit_part(entry, x_mm, y_mm)
        if part:
            return idx, entry, part
    return -1, None, ""


def _clear_balloon_selection(page) -> None:
    for entry in getattr(page, "balloons", []):
        if hasattr(entry, "selected"):
            entry.selected = False


def _selected_balloon_indices(page) -> list[int]:
    return [
        i for i, entry in enumerate(getattr(page, "balloons", []))
        if bool(getattr(entry, "selected", False))
    ]


def _select_balloon_index(context, work, page, index: int, *, mode: str = "single") -> bool:
    if page is None or not (0 <= index < len(page.balloons)):
        return False
    entry = page.balloons[index]
    if mode == "single":
        _clear_balloon_selection(page)
        entry.selected = True
    elif mode == "toggle":
        entry.selected = not bool(getattr(entry, "selected", False))
        if not _selected_balloon_indices(page):
            entry.selected = True
    elif mode == "add":
        entry.selected = True
    page.active_balloon_index = index
    if work is not None:
        for page_index, candidate in enumerate(work.pages):
            if candidate == page or getattr(candidate, "id", "") == getattr(page, "id", ""):
                work.active_page_index = page_index
                break
    if hasattr(context.scene, "bname_active_layer_kind"):
        context.scene.bname_active_layer_kind = "balloon"
    if hasattr(context.scene, "bname_active_gp_folder_key"):
        context.scene.bname_active_gp_folder_key = ""
    _sync_active_balloon_stack_item(context, page, entry)
    object_selection.select_key(
        context,
        object_selection.balloon_key(page, entry),
        mode=mode,
    )
    return True


def _sync_active_balloon_stack_item(context, page, entry) -> None:
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    uid = layer_stack_utils.target_uid(
        "balloon",
        f"{page_stack_key(page)}:{getattr(entry, 'id', '')}",
    )
    if stack is not None:
        for i, item in enumerate(stack):
            if layer_stack_utils.stack_item_uid(item) == uid:
                layer_stack_utils.set_active_stack_index_silently(context, i)
                break
    layer_stack_utils.remember_layer_stack_signature(context)
    layer_stack_utils.tag_view3d_redraw(context)


def _move_balloon_with_texts(page, entry, x_mm: float, y_mm: float) -> None:
    dx = float(x_mm) - float(getattr(entry, "x_mm", 0.0))
    dy = float(y_mm) - float(getattr(entry, "y_mm", 0.0))
    entry.x_mm = float(x_mm)
    entry.y_mm = float(y_mm)
    if abs(dx) <= 1.0e-9 and abs(dy) <= 1.0e-9:
        return
    bid = str(getattr(entry, "id", "") or "")
    for text in getattr(page, "texts", []):
        if getattr(text, "parent_balloon_id", "") == bid:
            text.x_mm += dx
            text.y_mm += dy


def _set_balloon_rect(page, entry, x: float, y: float, width: float, height: float) -> None:
    _move_balloon_with_texts(page, entry, x, y)
    entry.width_mm = max(_BALLOON_MIN_SIZE_MM, float(width))
    entry.height_mm = max(_BALLOON_MIN_SIZE_MM, float(height))


def _create_balloon_entry(context, page, *, shape: str, x: float, y: float, w: float, h: float):
    entry = page.balloons.add()
    entry.id = _allocate_balloon_id(page)
    entry.shape = shape
    entry.x_mm = float(x)
    entry.y_mm = float(y)
    entry.width_mm = max(_BALLOON_MIN_SIZE_MM, float(w))
    entry.height_mm = max(_BALLOON_MIN_SIZE_MM, float(h))
    entry.rounded_corner_enabled = (shape == "rect")
    page.active_balloon_index = len(page.balloons) - 1
    _clear_balloon_selection(page)
    entry.selected = True
    if hasattr(context.scene, "bname_active_layer_kind"):
        context.scene.bname_active_layer_kind = "balloon"
    layer_stack_utils.sync_layer_stack_after_data_change(context)
    return entry


def _delete_balloon_by_id(context, page_id: str, balloon_id: str) -> None:
    work = get_work(context)
    _page_index, page = _find_page_with_index_by_id(work, page_id)
    if page is None:
        return
    for i, entry in enumerate(page.balloons):
        if getattr(entry, "id", "") == balloon_id:
            page.balloons.remove(i)
            page.active_balloon_index = min(i, len(page.balloons) - 1) if len(page.balloons) else -1
            layer_stack_utils.sync_layer_stack_after_data_change(context)
            return


def _allocate_merge_group_id(page) -> str:
    used = {str(getattr(entry, "merge_group_id", "") or "") for entry in page.balloons}
    i = 1
    while True:
        candidate = f"balloon_group_{i:04d}"
        if candidate not in used:
            return candidate
        i += 1


def _selected_balloon_entries(page) -> list[object]:
    return [page.balloons[i] for i in _selected_balloon_indices(page)]


def _find_balloon_index(page, balloon_id: str) -> int:
    for i, entry in enumerate(getattr(page, "balloons", [])):
        if getattr(entry, "id", "") == balloon_id:
            return i
    return -1


def _rect_from_points(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    left = min(float(x0), float(x1))
    right = max(float(x0), float(x1))
    bottom = min(float(y0), float(y1))
    top = max(float(y0), float(y1))
    return (
        left,
        bottom,
        max(_BALLOON_MIN_SIZE_MM, right - left),
        max(_BALLOON_MIN_SIZE_MM, top - bottom),
    )


def _point_in_balloon_rect(entry, x_mm: float, y_mm: float) -> bool:
    left, bottom, right, top = _balloon_rect(entry)
    return left <= x_mm <= right and bottom <= y_mm <= top


def _add_tail_to_point(entry, tip_x: float, tip_y: float) -> bool:
    left, bottom, right, top = _balloon_rect(entry)
    cx = (left + right) * 0.5
    cy = (bottom + top) * 0.5
    rx = max((right - left) * 0.5, 0.01)
    ry = max((top - bottom) * 0.5, 0.01)
    vx = float(tip_x) - cx
    vy = float(tip_y) - cy
    distance = math.hypot(vx, vy)
    if distance <= _BALLOON_TAIL_MIN_LENGTH_MM:
        return False
    dx = vx / distance
    dy = vy / distance
    denom = math.hypot(dx / rx, dy / ry)
    base_x = cx + (dx / denom) if denom > 0 else cx
    base_y = cy + (dy / denom) if denom > 0 else cy
    length = math.hypot(float(tip_x) - base_x, float(tip_y) - base_y)
    if length <= _BALLOON_TAIL_MIN_LENGTH_MM:
        return False
    tail = entry.tails.add()
    tail.type = "straight"
    tail.direction_deg = math.degrees(math.atan2(dy, dx))
    tail.length_mm = length
    tail.root_width_mm = max(3.0, min(10.0, min(rx, ry) * 0.35))
    tail.tip_width_mm = 0.0
    return True


def _event_in_view3d_window(context, event) -> bool:
    return view_event_region.is_view3d_window_event(context, event)


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
        if _creation_violates_layer_scope(
            context, page, self.x_mm, self.y_mm, self.width_mm, self.height_mm
        ):
            self.report({"ERROR"}, "このモードではその位置にフキダシを作成できません")
            return {"CANCELLED"}
        entry = _create_balloon_entry(
            context,
            page,
            shape=self.shape,
            x=self.x_mm,
            y=self.y_mm,
            w=self.width_mm,
            h=self.height_mm,
        )
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
        layer_stack_utils.sync_layer_stack_after_data_change(context)
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
        layer_stack_utils.tag_view3d_redraw(context)
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
        _move_balloon_with_texts(page, entry, entry.x_mm + dx, entry.y_mm + dy)
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return {"FINISHED"}


class BNAME_OT_balloon_merge_selected(Operator):
    bl_idname = "bname.balloon_merge_selected"
    bl_label = "フキダシを結合"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return page is not None and len(_selected_balloon_indices(page)) >= 2

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        entries = _selected_balloon_entries(page)
        if len(entries) < 2:
            self.report({"ERROR"}, "結合するフキダシを2つ以上選択してください")
            return {"CANCELLED"}
        group_id = _allocate_merge_group_id(page)
        for entry in entries:
            entry.merge_group_id = group_id
            entry.blend_mode = "lighten"
            entry.selected = True
        first_id = str(getattr(entries[0], "id", "") or "")
        page.active_balloon_index = next(
            (i for i, item in enumerate(page.balloons) if getattr(item, "id", "") == first_id),
            page.active_balloon_index,
        )
        if hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "balloon"
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        self.report({"INFO"}, f"フキダシを結合: {group_id}")
        return {"FINISHED"}


class BNAME_OT_balloon_tool(Operator):
    bl_idname = "bname.balloon_tool"
    bl_label = "フキダシツール"
    bl_options = {"REGISTER", "UNDO"}

    _externally_finished: bool
    _cursor_modal_set: bool
    _dragging: bool
    _drag_action: str
    _drag_page_id: str
    _drag_balloon_id: str
    _drag_start_x: float
    _drag_start_y: float
    _drag_moved: bool
    _snapshots: list[tuple[str, float, float, float, float]]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded and get_active_page(context) is not None

    def invoke(self, context, _event):
        if panel_modal_state.get_active("balloon_tool") is not None:
            return {"FINISHED"}
        panel_modal_state.finish_active("panel_vertex_edit", context, keep_selection=True)
        panel_modal_state.finish_active("knife_cut", context, keep_selection=False)
        panel_modal_state.finish_active("edge_move", context, keep_selection=True)
        panel_modal_state.finish_active("layer_move", context, keep_selection=True)
        panel_modal_state.finish_active("text_tool", context, keep_selection=True)
        panel_modal_state.finish_active("effect_line_tool", context, keep_selection=True)
        self._externally_finished = False
        self._cursor_modal_set = panel_modal_state.set_modal_cursor(context, "CROSSHAIR")
        self._clear_drag_state()
        context.window_manager.modal_handler_add(self)
        panel_modal_state.set_active("balloon_tool", self, context)
        self.report({"INFO"}, "フキダシツール: ドラッグで作成")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            panel_modal_state.clear_active("balloon_tool", self, context)
            return {"FINISHED", "PASS_THROUGH"}
        if getattr(self, "_dragging", False):
            return self._modal_dragging(context, event)
        if not _event_in_view3d_window(context, event):
            return {"PASS_THROUGH"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if self._should_leave_for_tool_key(event):
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"PASS_THROUGH"}
        return self._handle_left_press(context, event)

    def _should_leave_for_tool_key(self, event) -> bool:
        return (
            event.value == "PRESS"
            and event.type in {"O", "P", "F", "G", "K", "T"}
            and not event.ctrl
            and not event.alt
        )

    def _handle_left_press(self, context, event):
        work, page, lx, ly = _resolve_page_from_event(context, event)
        if work is None or page is None or lx is None or ly is None:
            return {"PASS_THROUGH"}
        hit_index, hit_entry, hit_part = _hit_balloon_entry(page, lx, ly)
        if hit_entry is not None and hit_index >= 0:
            mode = "toggle" if event.ctrl else "add" if event.shift else "single"
            if (
                mode == "single"
                and hit_part == "body"
                and bool(getattr(hit_entry, "selected", False))
                and len(_selected_balloon_indices(page)) >= 2
            ):
                mode = "add"
            _select_balloon_index(context, work, page, hit_index, mode=mode)
            if event.alt and hit_part == "body":
                self._start_tail_drag(page, hit_entry, lx, ly)
            elif not (event.ctrl or event.shift):
                self._start_balloon_drag(page, hit_entry, hit_part, lx, ly)
            return {"RUNNING_MODAL"}
        if event.alt or event.ctrl or event.shift:
            return {"RUNNING_MODAL"}
        if _creation_violates_layer_scope(
            context, page, lx, ly, _BALLOON_MIN_SIZE_MM, _BALLOON_MIN_SIZE_MM
        ):
            self.report({"ERROR"}, "このモードではその位置にフキダシを作成できません")
            return {"RUNNING_MODAL"}
        entry = _create_balloon_entry(
            context,
            page,
            shape=_BALLOON_DEFAULT_SHAPE,
            x=lx,
            y=ly,
            w=_BALLOON_MIN_SIZE_MM,
            h=_BALLOON_MIN_SIZE_MM,
        )
        self._start_create_drag(page, entry, lx, ly)
        return {"RUNNING_MODAL"}

    def _start_create_drag(self, page, entry, x_mm: float, y_mm: float) -> None:
        self._dragging = True
        self._drag_action = "create"
        self._drag_page_id = getattr(page, "id", "")
        self._drag_balloon_id = getattr(entry, "id", "")
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_moved = False
        self._snapshots = [(entry.id, entry.x_mm, entry.y_mm, entry.width_mm, entry.height_mm)]

    def _start_tail_drag(self, page, entry, x_mm: float, y_mm: float) -> None:
        self._dragging = True
        self._drag_action = "tail"
        self._drag_page_id = getattr(page, "id", "")
        self._drag_balloon_id = getattr(entry, "id", "")
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_moved = False
        self._snapshots = []

    def _start_balloon_drag(self, page, entry, part: str, x_mm: float, y_mm: float) -> None:
        self._dragging = True
        self._drag_action = "move" if part == "body" else part
        self._drag_page_id = getattr(page, "id", "")
        self._drag_balloon_id = getattr(entry, "id", "")
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_moved = False
        self._snapshots = self._make_snapshots(page, entry)

    def _make_snapshots(self, page, entry) -> list[tuple[str, float, float, float, float]]:
        if bool(getattr(entry, "selected", False)) and self._drag_action == "move":
            indices = _selected_balloon_indices(page)
        else:
            indices = [_find_balloon_index(page, getattr(entry, "id", ""))]
        snapshots = []
        for idx in indices:
            if 0 <= idx < len(page.balloons):
                item = page.balloons[idx]
                snapshots.append((item.id, item.x_mm, item.y_mm, item.width_mm, item.height_mm))
        return snapshots

    def _clear_drag_state(self) -> None:
        self._dragging = False
        self._drag_action = ""
        self._drag_page_id = ""
        self._drag_balloon_id = ""
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._drag_moved = False
        self._snapshots = []

    def _modal_dragging(self, context, event):
        if event.type == "MOUSEMOVE":
            self._update_drag(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._update_drag(context, event)
            self._finish_drag(context, event)
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._cancel_drag(context)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def _drag_page_and_entry(self, context):
        work = get_work(context)
        _page_index, page = _find_page_with_index_by_id(work, self._drag_page_id)
        if page is None:
            return None, None
        idx = _find_balloon_index(page, self._drag_balloon_id)
        entry = page.balloons[idx] if 0 <= idx < len(page.balloons) else None
        return page, entry

    def _update_drag(self, context, event) -> None:
        page, entry = self._drag_page_and_entry(context)
        if entry is None or page is None:
            self._clear_drag_state()
            return
        work, current_page, lx, ly = _resolve_local_xy_for_page_from_event(
            context, event, getattr(page, "id", "")
        )
        if work is None or current_page is None or lx is None or ly is None:
            return
        dx = float(lx) - self._drag_start_x
        dy = float(ly) - self._drag_start_y
        if abs(dx) > _BALLOON_DRAG_EPS_MM or abs(dy) > _BALLOON_DRAG_EPS_MM:
            self._drag_moved = True
        if self._drag_action == "tail":
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "create":
            x, y, w, h = _rect_from_points(self._drag_start_x, self._drag_start_y, lx, ly)
            if _creation_violates_layer_scope(context, page, x, y, w, h):
                return
            _set_balloon_rect(page, entry, x, y, w, h)
        elif self._drag_action == "move":
            if self._move_violates_layer_scope(context, page, dx, dy):
                return
            self._apply_move_snapshots(page, dx, dy)
        else:
            x, y, w, h = self._resize_result_rect(entry, dx, dy)
            if _creation_violates_layer_scope(context, page, x, y, w, h):
                return
            _set_balloon_rect(page, entry, x, y, w, h)
        idx = _find_balloon_index(page, getattr(entry, "id", ""))
        if idx >= 0:
            _select_balloon_index(context, work, page, idx, mode="add")
        layer_stack_utils.tag_view3d_redraw(context)

    def _finish_drag(self, context, event) -> None:
        page, entry = self._drag_page_and_entry(context)
        moved = bool(getattr(self, "_drag_moved", False))
        action = self._drag_action
        show_detail = False
        if action == "create" and not moved:
            _delete_balloon_by_id(context, self._drag_page_id, self._drag_balloon_id)
        elif action == "tail" and moved and page is not None and entry is not None:
            self._finish_tail_drag(context, event, page, entry)
        elif moved:
            self._push_undo_step("B-Name: フキダシ編集")
            layer_stack_utils.sync_layer_stack_after_data_change(context)
        else:
            show_detail = action not in {"create", "tail"} and page is not None and entry is not None
            layer_stack_utils.tag_view3d_redraw(context)
        self._clear_drag_state()
        if show_detail:
            detail_popup.open_active_detail_deferred(context)

    def _finish_tail_drag(self, context, event, page, entry) -> None:
        _work, _page, lx, ly = _resolve_local_xy_for_page_from_event(
            context, event, getattr(page, "id", "")
        )
        if lx is None or ly is None or _point_in_balloon_rect(entry, lx, ly):
            return
        if _add_tail_to_point(entry, lx, ly):
            self._push_undo_step("B-Name: フキダシしっぽ作成")
            layer_stack_utils.sync_layer_stack_after_data_change(context)

    def _apply_move_snapshots(self, page, dx: float, dy: float) -> None:
        for balloon_id, x, y, _w, _h in self._snapshots:
            idx = _find_balloon_index(page, balloon_id)
            if 0 <= idx < len(page.balloons):
                _move_balloon_with_texts(page, page.balloons[idx], x + dx, y + dy)

    def _move_violates_layer_scope(self, context, page, dx: float, dy: float) -> bool:
        for _balloon_id, x, y, w, h in self._snapshots:
            if _creation_violates_layer_scope(context, page, x + dx, y + dy, w, h):
                return True
        return False

    def _resize_result_rect(self, entry, dx: float, dy: float) -> tuple[float, float, float, float]:
        _bid, x, y, w, h = self._snapshots[0]
        right = x + w
        top = y + h
        new_left = x
        new_right = right
        new_bottom = y
        new_top = top
        action = self._drag_action
        if "left" in action:
            new_left = min(right - _BALLOON_MIN_SIZE_MM, x + dx)
        if "right" in action:
            new_right = max(x + _BALLOON_MIN_SIZE_MM, right + dx)
        if "bottom" in action:
            new_bottom = min(top - _BALLOON_MIN_SIZE_MM, y + dy)
        if "top" in action:
            new_top = max(y + _BALLOON_MIN_SIZE_MM, top + dy)
        return new_left, new_bottom, new_right - new_left, new_top - new_bottom

    def _cancel_drag(self, context) -> None:
        page, _entry = self._drag_page_and_entry(context)
        if page is not None:
            for balloon_id, x, y, w, h in self._snapshots:
                idx = _find_balloon_index(page, balloon_id)
                if 0 <= idx < len(page.balloons):
                    _set_balloon_rect(page, page.balloons[idx], x, y, w, h)
        self._clear_drag_state()
        layer_stack_utils.tag_view3d_redraw(context)

    def _push_undo_step(self, message: str) -> None:
        try:
            bpy.ops.ed.undo_push(message=message)
        except Exception:  # noqa: BLE001
            _logger.exception("balloon_tool: undo_push failed")

    def _cleanup(self, context) -> None:
        if getattr(self, "_cursor_modal_set", False):
            panel_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        self._clear_drag_state()

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        _ = keep_selection
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        self._cleanup(context)
        panel_modal_state.clear_active("balloon_tool", self, context)


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
    BNAME_OT_balloon_merge_selected,
    BNAME_OT_balloon_tool,
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
