"""テキスト (縦書きセリフ/ナレーション/擬音) の Operator (Phase 3).

- 各ページの ``page.texts`` CollectionProperty にテキストを追加/削除
- invoke ではマウス直下のページを逆引きして active に追随
- フキダシへの attach/detach をサポート (``parent_balloon_id``)
- overlay 上の座標はページローカル mm。描画時に grid offset を加算する。
"""

from __future__ import annotations

import math
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator

from ..core.mode import MODE_PANEL, get_mode
from ..core.work import get_active_page, get_work
from ..utils import detail_popup, layer_stack as layer_stack_utils, log, page_range, text_style
from ..utils.layer_hierarchy import page_stack_key
from . import panel_modal_state, text_edit_runtime

_logger = log.get_logger(__name__)

_TEXT_DEFAULT_WIDTH_MM = 30.0
_TEXT_DEFAULT_HEIGHT_MM = 15.0
_TEXT_HANDLE_HIT_MM = 2.5
_TEXT_MIN_SIZE_MM = 2.0
_TEXT_DRAG_EPS_MM = 0.05
_TEXT_DOUBLE_CLICK_SECONDS = 0.45
_TEXT_DOUBLE_CLICK_DISTANCE_MM = 3.0


_SPEAKER_TYPE_ITEMS = (
    ("normal", "通常セリフ", ""),
    ("thought", "思考", ""),
    ("shout", "叫び", ""),
    ("narration", "ナレーション", ""),
    ("monologue", "モノローグ", ""),
    ("sfx", "擬音", ""),
)


def _allocate_text_id(page) -> str:
    used = {t.id for t in page.texts}
    i = 1
    while True:
        candidate = f"text_{i:04d}"
        if candidate not in used:
            return candidate
        i += 1


def _resolve_page_from_event(context, event):
    """balloon_op と同じロジックでページ + local mm 座標を解決."""
    from . import balloon_op

    return balloon_op._resolve_page_from_event(context, event)


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

    screen = getattr(context, "screen", None)
    if screen is None:
        return None, None
    mouse_x = int(getattr(event, "mouse_x", -10_000_000))
    mouse_y = int(getattr(event, "mouse_y", -10_000_000))
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if not (
                region.x <= mouse_x < region.x + region.width
                and region.y <= mouse_y < region.y + region.height
            ):
                continue
            rv3d = getattr(area.spaces.active, "region_3d", None)
            if rv3d is None:
                continue
            loc = region_2d_to_location_3d(
                region,
                rv3d,
                (mouse_x - region.x, mouse_y - region.y),
                (0.0, 0.0, 0.0),
            )
            if loc is None:
                continue
            return geom.m_to_mm(loc.x), geom.m_to_mm(loc.y)
    return None, None


def _resolve_local_xy_for_page_from_event(context, event, page_id: str):
    """指定ページをアクティブ変更せず、event の world 座標をそのページローカル mm に変換."""
    from ..utils import page_grid

    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return None, None, None, None
    target_index, target_page = _find_page_with_index_by_id(work, page_id)
    if target_page is None:
        return work, None, None, None
    world_x_mm, world_y_mm = _event_world_xy_mm(context, event)
    if world_x_mm is None or world_y_mm is None:
        return work, target_page, None, None
    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, target_index)
    return work, target_page, world_x_mm - ox_mm, world_y_mm - oy_mm


def _page_indices_for_text_hit_search(work):
    if work is None:
        return
    active_index = int(getattr(work, "active_page_index", -1))
    if (
        0 <= active_index < len(work.pages)
        and page_range.page_in_range(work.pages[active_index])
    ):
        yield active_index
    for page_index in reversed(range(len(work.pages))):
        if (
            page_index != active_index
            and page_range.page_in_range(work.pages[page_index])
        ):
            yield page_index


def _resolve_text_hit_from_event(context, event):
    """既存テキストをページ矩形外でも拾い、空白クリックはページ内だけ作成対象にする."""
    from ..utils import page_grid

    work, page, lx, ly = _resolve_page_from_event(context, event)
    can_create = page is not None and lx is not None and ly is not None
    if can_create:
        hit_index, hit_entry, hit_part = _hit_text_entry(page, lx, ly)
        if hit_entry is not None and hit_index >= 0:
            return work, page, lx, ly, hit_index, hit_entry, hit_part, True

    if work is None or not getattr(work, "loaded", False):
        return work, page, lx, ly, -1, None, "", can_create

    world_x_mm, world_y_mm = _event_world_xy_mm(context, event)
    if world_x_mm is None or world_y_mm is None:
        return work, page, lx, ly, -1, None, "", can_create

    for page_index in _page_indices_for_text_hit_search(work):
        candidate = work.pages[page_index]
        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, page_index)
        local_x = world_x_mm - ox_mm
        local_y = world_y_mm - oy_mm
        hit_index, hit_entry, hit_part = _hit_text_entry(candidate, local_x, local_y)
        if hit_entry is not None and hit_index >= 0:
            return work, candidate, local_x, local_y, hit_index, hit_entry, hit_part, False

    return work, page, lx, ly, -1, None, "", can_create


def _creation_blocked(context, page, x_mm: float, y_mm: float, width_mm: float, height_mm: float) -> bool:
    # ページ一覧ファイルでは、テキストツールはクリック位置にページ上の
    # テキストを作る道具として扱う。コマ編集ファイルでは対象コマ外だけを拒否する。
    if get_mode(context) != MODE_PANEL:
        return False
    try:
        from .balloon_op import _creation_violates_layer_scope

        return bool(_creation_violates_layer_scope(context, page, x_mm, y_mm, width_mm, height_mm))
    except Exception:  # noqa: BLE001
        return False


def _event_in_view3d_window(context, event) -> bool:
    screen = getattr(context, "screen", None)
    if screen is None:
        return False
    mouse_x = int(getattr(event, "mouse_x", -10_000_000))
    mouse_y = int(getattr(event, "mouse_y", -10_000_000))
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if (
                region.x <= mouse_x < region.x + region.width
                and region.y <= mouse_y < region.y + region.height
            ):
                return True
    return False


def _create_text_entry(
    context,
    page,
    *,
    body: str,
    speaker_type: str,
    x_mm: float,
    y_mm: float,
    width_mm: float,
    height_mm: float,
    parent_balloon_id: str = "",
):
    entry = page.texts.add()
    entry.id = _allocate_text_id(page)
    entry.body = body
    entry.speaker_type = speaker_type
    entry.x_mm = x_mm
    entry.y_mm = y_mm
    entry.width_mm = width_mm
    entry.height_mm = height_mm

    missing_parent = ""
    if parent_balloon_id:
        for b in page.balloons:
            if b.id == parent_balloon_id:
                entry.parent_balloon_id = parent_balloon_id
                entry.x_mm = b.x_mm + (b.width_mm - entry.width_mm) / 2.0
                entry.y_mm = b.y_mm + (b.height_mm - entry.height_mm) / 2.0
                break
        else:
            missing_parent = parent_balloon_id

    page.active_text_index = len(page.texts) - 1
    if hasattr(context.scene, "bname_active_layer_kind"):
        context.scene.bname_active_layer_kind = "text"
    layer_stack_utils.sync_layer_stack_after_data_change(context)
    return entry, missing_parent


def _find_page_by_id(context, page_id: str):
    work = get_work(context)
    if work is None:
        return None
    for page in work.pages:
        if getattr(page, "id", "") == page_id:
            return page
    return None


def _find_text_index(page, text_id: str) -> int:
    for i, entry in enumerate(page.texts):
        if getattr(entry, "id", "") == text_id:
            return i
    return -1


def _remove_text_by_id(context, page_id: str, text_id: str) -> None:
    page = _find_page_by_id(context, page_id)
    if page is None:
        return
    idx = _find_text_index(page, text_id)
    if idx < 0:
        return
    page.texts.remove(idx)
    page.active_text_index = min(idx, len(page.texts) - 1) if len(page.texts) else -1
    if len(page.texts) == 0 and hasattr(context.scene, "bname_active_layer_kind"):
        context.scene.bname_active_layer_kind = "gp"
    layer_stack_utils.sync_layer_stack_after_data_change(context)


def _clean_event_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="ignore")
    elif isinstance(value, str):
        text = str(value)
    else:
        return ""
    text = text.replace("\x00", "").replace("\r", "").replace("\n", "")
    return "".join(ch for ch in text if ord(ch) >= 32)


def _event_text(event) -> str:
    if bool(getattr(event, "ctrl", False)) or bool(getattr(event, "alt", False)):
        return ""
    if bool(getattr(event, "oskey", False)):
        return ""
    event_type = getattr(event, "type", "")
    if event_type in {"ESC", "RET", "NUMPAD_ENTER", "BACK_SPACE"}:
        return ""
    value = getattr(event, "value", "")
    for attr in ("unicode", "utf8", "text", "ascii"):
        cleaned = _clean_event_text(getattr(event, attr, ""))
        if cleaned and (event_type == "TEXTINPUT" or value in {"PRESS", "NOTHING"}):
            return cleaned
    if event_type != "TEXTINPUT" and value != "PRESS":
        return ""
    return ""


def _select_text_index(context, work, page, text_index: int) -> bool:
    if page is None or not (0 <= text_index < len(page.texts)):
        return False
    page.active_text_index = text_index
    if work is not None:
        for page_index, candidate in enumerate(work.pages):
            if candidate == page or getattr(candidate, "id", "") == getattr(page, "id", ""):
                work.active_page_index = page_index
                break
    scene = context.scene
    if hasattr(scene, "bname_active_layer_kind"):
        scene.bname_active_layer_kind = "text"
    if hasattr(scene, "bname_active_gp_folder_key"):
        scene.bname_active_gp_folder_key = ""
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    uid = layer_stack_utils.target_uid(
        "text",
        f"{page_stack_key(page)}:{getattr(page.texts[text_index], 'id', '')}",
    )
    if stack is not None:
        for i, item in enumerate(stack):
            if layer_stack_utils.stack_item_uid(item) == uid:
                layer_stack_utils.set_active_stack_index_silently(context, i)
                break
    layer_stack_utils.remember_layer_stack_signature(context)
    layer_stack_utils.tag_view3d_redraw(context)
    return True


def _active_text_selection_bounds(context, page, entry) -> tuple[int, int] | None:
    op = panel_modal_state.get_active("text_tool")
    if op is None or not bool(getattr(op, "_editing", False)):
        return None
    if str(getattr(op, "_page_id", "") or "") != str(getattr(page, "id", "") or ""):
        return None
    if str(getattr(op, "_text_id", "") or "") != str(getattr(entry, "id", "") or ""):
        return None
    return text_edit_runtime.selection_bounds(
        int(getattr(op, "_cursor_index", 0)),
        int(getattr(op, "_selection_anchor", -1)),
    )


def _text_rect(entry) -> tuple[float, float, float, float]:
    x = float(getattr(entry, "x_mm", 0.0))
    y = float(getattr(entry, "y_mm", 0.0))
    w = float(getattr(entry, "width_mm", 0.0))
    h = float(getattr(entry, "height_mm", 0.0))
    return x, y, x + w, y + h


def _text_hit_part(entry, x_mm: float, y_mm: float) -> str:
    left, bottom, right, top = _text_rect(entry)
    threshold = _TEXT_HANDLE_HIT_MM
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


def _hit_text_entry(page, x_mm: float, y_mm: float):
    active_idx = int(getattr(page, "active_text_index", -1))
    indices: list[int] = []
    if 0 <= active_idx < len(page.texts):
        indices.append(active_idx)
    indices.extend(i for i in reversed(range(len(page.texts))) if i != active_idx)
    for idx in indices:
        part = _text_hit_part(page.texts[idx], x_mm, y_mm)
        if part:
            return idx, page.texts[idx], part
    return -1, None, ""


def _set_text_rect(entry, x: float, y: float, width: float, height: float) -> None:
    entry.x_mm = float(x)
    entry.y_mm = float(y)
    entry.width_mm = max(_TEXT_MIN_SIZE_MM, float(width))
    entry.height_mm = max(_TEXT_MIN_SIZE_MM, float(height))


class BNAME_OT_text_add(Operator):
    """アクティブページにテキストを追加. マウス位置から座標決定."""

    bl_idname = "bname.text_add"
    bl_label = "テキストを追加"
    bl_options = {"REGISTER", "UNDO"}

    body: StringProperty(name="本文", default="")  # type: ignore[valid-type]
    speaker_type: EnumProperty(  # type: ignore[valid-type]
        name="種別",
        items=_SPEAKER_TYPE_ITEMS,
        default="normal",
    )
    x_mm: FloatProperty(name="X (mm)", default=0.0)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y (mm)", default=0.0)  # type: ignore[valid-type]
    width_mm: FloatProperty(name="幅 (mm)", default=30.0, min=0.1)  # type: ignore[valid-type]
    height_mm: FloatProperty(name="高さ (mm)", default=15.0, min=0.1)  # type: ignore[valid-type]
    parent_balloon_id: StringProperty(  # type: ignore[valid-type]
        name="親フキダシ ID",
        description="同じページの BNameBalloonEntry.id を指定 (空で独立テキスト)",
        default="",
    )
    use_explicit_position: BoolProperty(default=False, options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded and get_active_page(context) is not None

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "body")
        layout.prop(self, "speaker_type")
        row = layout.row(align=True)
        row.prop(self, "width_mm")
        row.prop(self, "height_mm")

    def invoke(self, context, event):
        if self.use_explicit_position:
            return context.window_manager.invoke_props_dialog(self)
        work, page, lx, ly = _resolve_page_from_event(context, event)
        if work is None or page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        if lx is not None and ly is not None:
            # マウス位置を中央に. 親フキダシが指定済なら後で上書き
            self.x_mm = lx - self.width_mm / 2.0
            self.y_mm = ly - self.height_mm / 2.0
        else:
            self.x_mm = work.paper.canvas_width_mm / 2.0 - self.width_mm / 2.0
            self.y_mm = work.paper.canvas_height_mm / 2.0 - self.height_mm / 2.0
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        if _creation_blocked(context, page, self.x_mm, self.y_mm, self.width_mm, self.height_mm):
            self.report({"ERROR"}, "このモードではその位置にテキストを作成できません")
            return {"CANCELLED"}
        entry, missing_parent = _create_text_entry(
            context,
            page,
            body=self.body,
            speaker_type=self.speaker_type,
            x_mm=self.x_mm,
            y_mm=self.y_mm,
            width_mm=self.width_mm,
            height_mm=self.height_mm,
            parent_balloon_id=self.parent_balloon_id,
        )
        if missing_parent:
            self.report(
                {"WARNING"},
                f"親フキダシ {missing_parent} が見つかりません (独立テキストとして追加)",
            )
        self.report({"INFO"}, f"テキスト追加: {entry.id}")
        return {"FINISHED"}


class BNAME_OT_text_remove(Operator):
    bl_idname = "bname.text_remove"
    bl_label = "テキストを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_text_index < len(page.texts)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_text_index
        if not (0 <= idx < len(page.texts)):
            return {"CANCELLED"}
        tid = page.texts[idx].id
        page.texts.remove(idx)
        if len(page.texts) == 0:
            page.active_text_index = -1
        elif idx >= len(page.texts):
            page.active_text_index = len(page.texts) - 1
        if len(page.texts) == 0 and hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "gp"
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        self.report({"INFO"}, f"テキスト削除: {tid}")
        return {"FINISHED"}


class BNAME_OT_text_attach_to_balloon(Operator):
    """アクティブテキストをアクティブフキダシへ attach (親子連動対象化).

    空文字でも実行: 現在の親子連携を解除して独立テキスト化する。
    """

    bl_idname = "bname.text_attach_to_balloon"
    bl_label = "テキストをフキダシに紐付け"
    bl_options = {"REGISTER", "UNDO"}

    balloon_id: StringProperty(  # type: ignore[valid-type]
        name="フキダシ ID",
        description="空で親子関係を解除 (独立テキスト化)",
        default="",
    )

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_text_index < len(page.texts)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_text_index
        if not (0 <= idx < len(page.texts)):
            return {"CANCELLED"}
        txt = page.texts[idx]
        target_id = self.balloon_id.strip()
        if not target_id:
            txt.parent_balloon_id = ""
            layer_stack_utils.sync_layer_stack_after_data_change(context)
            self.report({"INFO"}, "テキストを独立化しました")
            return {"FINISHED"}
        # 指定 ID のフキダシが同じページに存在するか確認
        for b in page.balloons:
            if b.id == target_id:
                txt.parent_balloon_id = target_id
                # 位置を当該フキダシの中央に合わせる
                txt.x_mm = b.x_mm + (b.width_mm - txt.width_mm) / 2.0
                txt.y_mm = b.y_mm + (b.height_mm - txt.height_mm) / 2.0
                layer_stack_utils.sync_layer_stack_after_data_change(context)
                self.report({"INFO"}, f"テキストを紐付け: {target_id}")
                return {"FINISHED"}
        self.report({"ERROR"}, f"フキダシが見つかりません: {target_id}")
        return {"CANCELLED"}


class BNAME_OT_text_apply_font_to_selection(Operator):
    """テキスト編集中の選択範囲へフォントを適用する."""

    bl_idname = "bname.text_apply_font_to_selection"
    bl_label = "選択範囲にフォントを適用"
    bl_options = {"REGISTER", "UNDO"}

    font: StringProperty(name="フォント", default="", subtype="FILE_PATH")  # type: ignore[valid-type]
    clear: BoolProperty(name="基本フォントに戻す", default=False)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_text_index < len(page.texts)

    def execute(self, context):
        page = get_active_page(context)
        if page is None or not (0 <= page.active_text_index < len(page.texts)):
            return {"CANCELLED"}
        entry = page.texts[page.active_text_index]
        bounds = _active_text_selection_bounds(context, page, entry)
        if bounds is None:
            self.report({"ERROR"}, "フォントを変える文字範囲を選択してください")
            return {"CANCELLED"}
        start, end = bounds
        font = "" if self.clear else str(self.font or "").strip()
        if not self.clear and not font:
            font = str(getattr(context.scene, "bname_text_selection_font", "") or "").strip()
        if not self.clear and not font:
            font = str(getattr(entry, "font", "") or "").strip()
        if not self.clear and not font:
            self.report({"ERROR"}, "適用するフォントを指定してください")
            return {"CANCELLED"}
        if not text_style.apply_font_span(entry, start, end, font):
            return {"CANCELLED"}
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        layer_stack_utils.tag_view3d_redraw(context)
        self.report({"INFO"}, "選択範囲のフォントを更新しました")
        return {"FINISHED"}


class BNAME_OT_text_tool(Operator):
    """クリック位置へテキストレイヤーを作成し、インライン入力を開始する."""

    bl_idname = "bname.text_tool"
    bl_label = "テキストツール"
    bl_options = {"REGISTER", "UNDO"}

    _externally_finished: bool
    _cursor_modal_set: bool
    _editing: bool
    _editing_created_new: bool
    _edit_original_body: str
    _edit_original_font_spans: tuple[tuple[int, int, str], ...]
    _cursor_index: int
    _selection_anchor: int
    _page_id: str
    _text_id: str
    _dragging: bool
    _drag_action: str
    _drag_page_id: str
    _drag_text_id: str
    _drag_start_x: float
    _drag_start_y: float
    _drag_orig_x: float
    _drag_orig_y: float
    _drag_orig_w: float
    _drag_orig_h: float
    _drag_moved: bool
    _last_click_time: float
    _last_click_page_id: str
    _last_click_text_id: str
    _last_click_x: float
    _last_click_y: float
    _ime_timer: object | None

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded and get_active_page(context) is not None

    def invoke(self, context, _event):
        if panel_modal_state.get_active("text_tool") is not None:
            return {"FINISHED"}
        panel_modal_state.finish_active("panel_vertex_edit", context, keep_selection=True)
        panel_modal_state.finish_active("knife_cut", context, keep_selection=False)
        panel_modal_state.finish_active("edge_move", context, keep_selection=True)
        panel_modal_state.finish_active("layer_move", context, keep_selection=True)
        panel_modal_state.finish_active("balloon_tool", context, keep_selection=True)
        panel_modal_state.finish_active("effect_line_tool", context, keep_selection=True)
        self._externally_finished = False
        self._cursor_modal_set = panel_modal_state.set_modal_cursor(context, "TEXT")
        self._editing = False
        self._editing_created_new = False
        self._edit_original_body = ""
        self._edit_original_font_spans = ()
        self._cursor_index = 0
        self._selection_anchor = -1
        self._page_id = ""
        self._text_id = ""
        self._ime_timer = None
        self._clear_drag_state()
        self._clear_click_state()
        context.window_manager.modal_handler_add(self)
        panel_modal_state.set_active("text_tool", self, context)
        self.report({"INFO"}, "テキストツール: クリック位置にテキストを追加")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            panel_modal_state.clear_active("text_tool", self, context)
            return {"FINISHED", "PASS_THROUGH"}
        if getattr(self, "_editing", False):
            return self._modal_editing(context, event)
        if getattr(self, "_dragging", False):
            return self._modal_dragging(context, event)
        if not _event_in_view3d_window(context, event):
            if event.type == "LEFTMOUSE" and event.value == "PRESS":
                self._clear_click_state()
            return {"PASS_THROUGH"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if event.type == "T" and event.value == "PRESS" and not event.ctrl and not event.alt:
            return {"RUNNING_MODAL"}
        if (
            event.value == "PRESS"
            and event.type in {"O", "P", "F", "G", "K"}
            and not event.ctrl
            and not event.alt
        ):
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}
        if event.type != "LEFTMOUSE" or event.value not in {"PRESS", "DOUBLE_CLICK"}:
            return {"PASS_THROUGH"}
        work, page, lx, ly, hit_index, hit_entry, hit_part, can_create = _resolve_text_hit_from_event(
            context, event
        )
        if work is None or page is None:
            return {"PASS_THROUGH"}
        if hit_entry is not None and hit_index >= 0:
            is_double_click = event.value == "DOUBLE_CLICK" or self._is_text_double_click(page, hit_entry, lx, ly)
            _select_text_index(context, work, page, hit_index)
            if is_double_click:
                self._clear_click_state()
                self._start_editing_existing(context, page, hit_entry)
                return {"RUNNING_MODAL"}
            self._remember_text_click(page, hit_entry, lx, ly)
            self._start_text_drag(page, hit_entry, hit_part, lx, ly)
            return {"RUNNING_MODAL"}
        if not can_create or lx is None or ly is None:
            return {"PASS_THROUGH"}
        width = _TEXT_DEFAULT_WIDTH_MM
        height = _TEXT_DEFAULT_HEIGHT_MM
        x_mm = lx - width / 2.0
        y_mm = ly - height / 2.0
        if _creation_blocked(context, page, x_mm, y_mm, width, height):
            self.report({"ERROR"}, "このモードではその位置にテキストを作成できません")
            return {"RUNNING_MODAL"}
        entry, _missing_parent = _create_text_entry(
            context,
            page,
            body="",
            speaker_type="normal",
            x_mm=x_mm,
            y_mm=y_mm,
            width_mm=width,
            height_mm=height,
        )
        self._editing = True
        self._editing_created_new = True
        self._edit_original_body = ""
        self._edit_original_font_spans = ()
        self._cursor_index = 0
        self._selection_anchor = -1
        self._page_id = getattr(page, "id", "")
        self._text_id = getattr(entry, "id", "")
        self._clear_click_state()
        self._begin_inline_input(context)
        self.report({"INFO"}, "本文を入力してください (Enter: 確定 / Esc: キャンセル)")
        return {"RUNNING_MODAL"}

    def _modal_editing(self, context, event):
        queued_text = text_edit_runtime.poll_ime_text()
        if queued_text:
            return self._insert_current_text(context, queued_text)
        if event.type == "TIMER":
            return {"RUNNING_MODAL"}
        if text_edit_runtime.event_is_ime_control(event):
            return {"RUNNING_MODAL"}
        if not _event_in_view3d_window(context, event):
            if not self._is_text_edit_event(event):
                return {"PASS_THROUGH"}
        if event.type == "LEFTMOUSE" and event.value in {"PRESS", "DOUBLE_CLICK"}:
            if self._set_cursor_from_text_click(context, event):
                return {"RUNNING_MODAL"}
            self._finish_current_text_edit(context)
            return self.modal(context, event)
        text = _event_text(event)
        if text:
            return self._insert_current_text(context, text)
        if event.value != "PRESS":
            return {"PASS_THROUGH"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            if bool(getattr(self, "_editing_created_new", False)):
                _remove_text_by_id(context, self._page_id, self._text_id)
            else:
                page, entry, idx = self._current_text_entry(context)
                if entry is not None:
                    entry.body = str(getattr(self, "_edit_original_body", ""))
                    text_style.restore_font_spans(entry, getattr(self, "_edit_original_font_spans", ()))
                    if page is not None:
                        page.active_text_index = idx
                    layer_stack_utils.sync_layer_stack_after_data_change(context)
            self._finish_current_text_edit(context)
            return {"RUNNING_MODAL"}
        if event.type in {"RET", "NUMPAD_ENTER"}:
            if event.shift:
                return self._insert_current_text(context, "\n")
            self._finish_current_text_edit(context)
            return {"RUNNING_MODAL"}
        if event.type == "BACK_SPACE":
            return self._backspace_current_text(context)
        if event.type in {"DEL", "DELETE"}:
            return self._delete_current_text(context)
        if event.type in {"LEFT_ARROW", "RIGHT_ARROW", "UP_ARROW", "DOWN_ARROW", "HOME", "END"}:
            direction = {
                "LEFT_ARROW": "LEFT",
                "RIGHT_ARROW": "RIGHT",
                "UP_ARROW": "UP",
                "DOWN_ARROW": "DOWN",
                "HOME": "HOME",
                "END": "END",
            }[event.type]
            return self._move_text_cursor(context, direction, select=bool(getattr(event, "shift", False)))
        if event.type == "A" and event.ctrl and not event.alt:
            return self._select_all_current_text(context)
        if event.type == "C" and event.ctrl and not event.alt:
            return self._copy_current_selection(context)
        if event.type == "X" and event.ctrl and not event.alt:
            return self._cut_current_selection(context)
        if event.type == "V" and event.ctrl and not event.alt:
            clipboard = getattr(context.window_manager, "clipboard", "")
            if clipboard:
                return self._insert_current_text(context, clipboard)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def _is_text_edit_event(self, event) -> bool:
        if _event_text(event):
            return True
        if event.type == "TEXTINPUT":
            return True
        if event.value != "PRESS":
            return False
        if event.type in {"ESC", "RET", "NUMPAD_ENTER", "BACK_SPACE"}:
            return True
        if event.type in {
            "DEL",
            "DELETE",
            "LEFT_ARROW",
            "RIGHT_ARROW",
            "UP_ARROW",
            "DOWN_ARROW",
            "HOME",
            "END",
        }:
            return True
        if event.type in {"A", "C", "X"} and event.ctrl and not event.alt:
            return True
        if event.type == "V" and event.ctrl and not event.alt:
            return True
        return False

    def _begin_inline_input(self, context) -> None:
        text_edit_runtime.begin_ime_capture()
        if getattr(self, "_ime_timer", None) is not None:
            return
        window = getattr(context, "window", None)
        wm = getattr(context, "window_manager", None)
        if window is None or wm is None:
            return
        try:
            self._ime_timer = wm.event_timer_add(0.05, window=window)
        except Exception:  # noqa: BLE001
            self._ime_timer = None

    def _end_inline_input(self, context) -> None:
        timer = getattr(self, "_ime_timer", None)
        if timer is not None:
            wm = getattr(context, "window_manager", None)
            if wm is not None:
                try:
                    wm.event_timer_remove(timer)
                except Exception:  # noqa: BLE001
                    pass
        self._ime_timer = None
        text_edit_runtime.end_ime_capture()

    def _finish_current_text_edit(self, context) -> None:
        _page, entry, _idx = self._current_text_entry(context)
        spans_changed = (
            entry is not None
            and text_style.font_spans_snapshot(entry) != getattr(self, "_edit_original_font_spans", ())
        )
        if (
            entry is not None
            and (
                str(getattr(entry, "body", "")) != str(getattr(self, "_edit_original_body", ""))
                or spans_changed
            )
        ):
            self._push_undo_step("B-Name: テキスト編集")
        self._end_inline_input(context)
        self._editing = False
        self._editing_created_new = False
        self._edit_original_body = ""
        self._edit_original_font_spans = ()
        self._cursor_index = 0
        self._selection_anchor = -1
        self._page_id = ""
        self._text_id = ""
        self._clear_click_state()
        self.report({"INFO"}, "テキストツール: クリック位置にテキストを追加")
        layer_stack_utils.tag_view3d_redraw(context)

    def _start_editing_existing(self, context, page, entry) -> None:
        self._editing = True
        self._editing_created_new = False
        self._edit_original_body = str(getattr(entry, "body", ""))
        self._edit_original_font_spans = text_style.font_spans_snapshot(entry)
        self._cursor_index = len(self._edit_original_body)
        self._selection_anchor = -1
        self._page_id = getattr(page, "id", "")
        self._text_id = getattr(entry, "id", "")
        self._clear_drag_state()
        self._clear_click_state()
        self._begin_inline_input(context)
        self.report({"INFO"}, "本文を入力してください (Enter: 確定 / Esc: キャンセル)")
        layer_stack_utils.tag_view3d_redraw(context)

    def _start_text_drag(self, page, entry, part: str, x_mm: float, y_mm: float) -> None:
        self._dragging = True
        self._drag_action = "move" if part == "body" else part
        self._drag_page_id = getattr(page, "id", "")
        self._drag_text_id = getattr(entry, "id", "")
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_orig_x = float(entry.x_mm)
        self._drag_orig_y = float(entry.y_mm)
        self._drag_orig_w = float(entry.width_mm)
        self._drag_orig_h = float(entry.height_mm)
        self._drag_moved = False

    def _clear_drag_state(self) -> None:
        self._dragging = False
        self._drag_action = ""
        self._drag_page_id = ""
        self._drag_text_id = ""
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._drag_orig_x = 0.0
        self._drag_orig_y = 0.0
        self._drag_orig_w = 0.0
        self._drag_orig_h = 0.0
        self._drag_moved = False

    def _clear_click_state(self) -> None:
        self._last_click_time = 0.0
        self._last_click_page_id = ""
        self._last_click_text_id = ""
        self._last_click_x = 0.0
        self._last_click_y = 0.0

    def _remember_text_click(self, page, entry, x_mm: float | None, y_mm: float | None) -> None:
        if x_mm is None or y_mm is None:
            self._clear_click_state()
            return
        self._last_click_time = time.monotonic()
        self._last_click_page_id = str(getattr(page, "id", "") or "")
        self._last_click_text_id = str(getattr(entry, "id", "") or "")
        self._last_click_x = float(x_mm)
        self._last_click_y = float(y_mm)

    def _is_text_double_click(self, page, entry, x_mm: float | None, y_mm: float | None) -> bool:
        if x_mm is None or y_mm is None:
            return False
        previous_time = float(getattr(self, "_last_click_time", 0.0) or 0.0)
        if previous_time <= 0.0:
            return False
        if time.monotonic() - previous_time > _TEXT_DOUBLE_CLICK_SECONDS:
            return False
        if str(getattr(page, "id", "") or "") != str(getattr(self, "_last_click_page_id", "") or ""):
            return False
        if str(getattr(entry, "id", "") or "") != str(getattr(self, "_last_click_text_id", "") or ""):
            return False
        distance = math.hypot(float(x_mm) - self._last_click_x, float(y_mm) - self._last_click_y)
        return distance <= _TEXT_DOUBLE_CLICK_DISTANCE_MM

    def _modal_dragging(self, context, event):
        if event.type == "MOUSEMOVE":
            self._update_text_drag(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "DOUBLE_CLICK":
            page, entry, idx = self._drag_text_entry(context)
            moved = bool(getattr(self, "_drag_moved", False))
            self._clear_drag_state()
            if not moved and entry is not None and page is not None and idx >= 0:
                work = get_work(context)
                _select_text_index(context, work, page, idx)
                self._start_editing_existing(context, page, entry)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            moved = bool(getattr(self, "_drag_moved", False))
            action = str(getattr(self, "_drag_action", "") or "")
            self._clear_drag_state()
            if moved:
                self._clear_click_state()
                self._push_undo_step("B-Name: テキスト移動/リサイズ")
                layer_stack_utils.sync_layer_stack_after_data_change(context)
            else:
                layer_stack_utils.tag_view3d_redraw(context)
                if action and action != "move":
                    detail_popup.open_active_detail_deferred(context)
                elif action == "move":
                    click_time = float(getattr(self, "_last_click_time", 0.0) or 0.0)
                    click_page_id = str(getattr(self, "_last_click_page_id", "") or "")
                    click_text_id = str(getattr(self, "_last_click_text_id", "") or "")

                    def _still_single_click() -> bool:
                        return (
                            not bool(getattr(self, "_editing", False))
                            and not bool(getattr(self, "_dragging", False))
                            and float(getattr(self, "_last_click_time", 0.0) or 0.0) == click_time
                            and str(getattr(self, "_last_click_page_id", "") or "") == click_page_id
                            and str(getattr(self, "_last_click_text_id", "") or "") == click_text_id
                        )

                    detail_popup.open_active_detail_deferred_if(
                        context,
                        _still_single_click,
                        delay=_TEXT_DOUBLE_CLICK_SECONDS + 0.05,
                    )
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._cancel_text_drag(context)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def _drag_text_entry(self, context):
        page = _find_page_by_id(context, getattr(self, "_drag_page_id", ""))
        if page is None:
            return None, None, -1
        idx = _find_text_index(page, getattr(self, "_drag_text_id", ""))
        if idx < 0:
            return page, None, -1
        return page, page.texts[idx], idx

    def _update_text_drag(self, context, event) -> None:
        page, entry, idx = self._drag_text_entry(context)
        if entry is None or page is None:
            self._clear_drag_state()
            return
        work, current_page, lx, ly = _resolve_local_xy_for_page_from_event(
            context,
            event,
            getattr(page, "id", ""),
        )
        if work is None or current_page is None or lx is None or ly is None:
            return
        if getattr(page, "id", "") != getattr(current_page, "id", ""):
            return
        dx = float(lx) - float(self._drag_start_x)
        dy = float(ly) - float(self._drag_start_y)
        if abs(dx) > _TEXT_DRAG_EPS_MM or abs(dy) > _TEXT_DRAG_EPS_MM:
            self._drag_moved = True
        x, y, w, h = self._drag_result_rect(dx, dy)
        if self._drag_action != "move" and _creation_blocked(context, page, x, y, w, h):
            return
        _set_text_rect(entry, x, y, w, h)
        _select_text_index(context, work, page, idx)
        layer_stack_utils.tag_view3d_redraw(context)

    def _drag_result_rect(self, dx: float, dy: float) -> tuple[float, float, float, float]:
        x = float(self._drag_orig_x)
        y = float(self._drag_orig_y)
        w = float(self._drag_orig_w)
        h = float(self._drag_orig_h)
        right = x + w
        top = y + h
        action = str(getattr(self, "_drag_action", "") or "")
        if action == "move":
            return x + dx, y + dy, w, h
        new_left = x
        new_right = right
        new_bottom = y
        new_top = top
        if "left" in action:
            new_left = min(right - _TEXT_MIN_SIZE_MM, x + dx)
        if "right" in action:
            new_right = max(x + _TEXT_MIN_SIZE_MM, right + dx)
        if "bottom" in action:
            new_bottom = min(top - _TEXT_MIN_SIZE_MM, y + dy)
        if "top" in action:
            new_top = max(y + _TEXT_MIN_SIZE_MM, top + dy)
        return new_left, new_bottom, new_right - new_left, new_top - new_bottom

    def _cancel_text_drag(self, context) -> None:
        page, entry, _idx = self._drag_text_entry(context)
        if entry is not None:
            _set_text_rect(
                entry,
                self._drag_orig_x,
                self._drag_orig_y,
                self._drag_orig_w,
                self._drag_orig_h,
            )
        self._clear_drag_state()
        layer_stack_utils.tag_view3d_redraw(context)

    def _push_undo_step(self, message: str) -> None:
        try:
            bpy.ops.ed.undo_push(message=message)
        except Exception:  # noqa: BLE001
            _logger.exception("text_tool: undo_push failed")

    def _current_text_entry(self, context):
        page = _find_page_by_id(context, self._page_id)
        if page is None:
            return None, None, -1
        idx = _find_text_index(page, self._text_id)
        if idx < 0:
            return page, None, -1
        return page, page.texts[idx], idx

    def _set_cursor_from_text_click(self, context, event) -> bool:
        page, entry, idx = self._current_text_entry(context)
        if entry is None or page is None or idx < 0:
            return False
        work, hit_page, lx, ly, hit_index, hit_entry, hit_part, _can_create = _resolve_text_hit_from_event(
            context, event
        )
        _ = work
        if (
            hit_entry is None
            or hit_page is None
            or getattr(hit_page, "id", "") != getattr(page, "id", "")
            or getattr(hit_entry, "id", "") != getattr(entry, "id", "")
            or hit_index != idx
        ):
            return False
        if hit_part != "body" or lx is None or ly is None:
            return False
        if event.value == "DOUBLE_CLICK":
            self._cursor_index = len(text_edit_runtime.text_body(entry))
            self._selection_anchor = 0
        else:
            self._cursor_index = text_edit_runtime.cursor_index_from_point(entry, lx, ly)
            self._selection_anchor = -1
        page.active_text_index = idx
        layer_stack_utils.tag_view3d_redraw(context)
        return True

    def _touch_current_text(self, context, page, entry, idx: int) -> None:
        self._cursor_index = text_edit_runtime.clamp_cursor(entry, self._cursor_index)
        page.active_text_index = idx
        if hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "text"
        layer_stack_utils.sync_layer_stack_after_data_change(context)

    def _insert_current_text(self, context, text: str):
        page, entry, idx = self._current_text_entry(context)
        if entry is None:
            self.finish_from_external(context, keep_selection=True)
            return {"CANCELLED"}
        cleaned = text.replace("\x00", "")
        if not cleaned:
            return {"RUNNING_MODAL"}
        self._cursor_index = text_edit_runtime.replace_selection(
            entry,
            self._cursor_index,
            self._selection_anchor,
            cleaned,
        )
        self._selection_anchor = -1
        self._touch_current_text(context, page, entry, idx)
        return {"RUNNING_MODAL"}

    def _backspace_current_text(self, context):
        page, entry, idx = self._current_text_entry(context)
        if entry is None:
            self.finish_from_external(context, keep_selection=True)
            return {"CANCELLED"}
        self._cursor_index = text_edit_runtime.delete_backward(
            entry,
            self._cursor_index,
            self._selection_anchor,
        )
        self._selection_anchor = -1
        self._touch_current_text(context, page, entry, idx)
        return {"RUNNING_MODAL"}

    def _delete_current_text(self, context):
        page, entry, idx = self._current_text_entry(context)
        if entry is None:
            self.finish_from_external(context, keep_selection=True)
            return {"CANCELLED"}
        self._cursor_index = text_edit_runtime.delete_forward(
            entry,
            self._cursor_index,
            self._selection_anchor,
        )
        self._selection_anchor = -1
        self._touch_current_text(context, page, entry, idx)
        return {"RUNNING_MODAL"}

    def _move_text_cursor(self, context, direction: str, *, select: bool):
        page, entry, idx = self._current_text_entry(context)
        if entry is None:
            self.finish_from_external(context, keep_selection=True)
            return {"CANCELLED"}
        if select and self._selection_anchor < 0:
            self._selection_anchor = text_edit_runtime.clamp_cursor(entry, self._cursor_index)
        self._cursor_index = text_edit_runtime.move_cursor(entry, self._cursor_index, direction)
        if not select:
            self._selection_anchor = -1
        page.active_text_index = idx
        layer_stack_utils.tag_view3d_redraw(context)
        return {"RUNNING_MODAL"}

    def _select_all_current_text(self, context):
        page, entry, idx = self._current_text_entry(context)
        if entry is None:
            self.finish_from_external(context, keep_selection=True)
            return {"CANCELLED"}
        self._selection_anchor = 0
        self._cursor_index = len(text_edit_runtime.text_body(entry))
        page.active_text_index = idx
        layer_stack_utils.tag_view3d_redraw(context)
        return {"RUNNING_MODAL"}

    def _copy_current_selection(self, context):
        _page, entry, _idx = self._current_text_entry(context)
        if entry is None:
            self.finish_from_external(context, keep_selection=True)
            return {"CANCELLED"}
        selected = text_edit_runtime.selected_text(entry, self._cursor_index, self._selection_anchor)
        if selected:
            context.window_manager.clipboard = selected
        return {"RUNNING_MODAL"}

    def _cut_current_selection(self, context):
        page, entry, idx = self._current_text_entry(context)
        if entry is None:
            self.finish_from_external(context, keep_selection=True)
            return {"CANCELLED"}
        selected = text_edit_runtime.selected_text(entry, self._cursor_index, self._selection_anchor)
        if selected:
            context.window_manager.clipboard = selected
            self._cursor_index = text_edit_runtime.replace_selection(
                entry,
                self._cursor_index,
                self._selection_anchor,
                "",
            )
            self._selection_anchor = -1
            self._touch_current_text(context, page, entry, idx)
        return {"RUNNING_MODAL"}

    def _cleanup(self, context) -> None:
        if getattr(self, "_cursor_modal_set", False):
            panel_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        self._end_inline_input(context)
        self._editing = False
        self._editing_created_new = False
        self._edit_original_body = ""
        self._edit_original_font_spans = ()
        self._cursor_index = 0
        self._selection_anchor = -1
        self._clear_drag_state()
        self._clear_click_state()

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        _ = keep_selection
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        self._cleanup(context)
        panel_modal_state.clear_active("text_tool", self, context)


_CLASSES = (
    BNAME_OT_text_add,
    BNAME_OT_text_remove,
    BNAME_OT_text_attach_to_balloon,
    BNAME_OT_text_apply_font_to_selection,
    BNAME_OT_text_tool,
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
