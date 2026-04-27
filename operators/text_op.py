"""テキスト (縦書きセリフ/ナレーション/擬音) の Operator (Phase 3).

- 各ページの ``page.texts`` CollectionProperty にテキストを追加/削除
- invoke ではマウス直下のページを逆引きして active に追随
- フキダシへの attach/detach をサポート (``parent_balloon_id``)
- overlay 上の座標はページローカル mm。描画時に grid offset を加算する。
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator

from ..core.mode import MODE_PANEL, get_mode
from ..core.work import get_active_page, get_work
from ..utils import layer_stack as layer_stack_utils, log
from . import panel_modal_state

_logger = log.get_logger(__name__)


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


class BNAME_OT_text_tool(Operator):
    """クリック位置へテキストレイヤーを作成し、インライン入力を開始する."""

    bl_idname = "bname.text_tool"
    bl_label = "テキストツール"
    bl_options = {"REGISTER", "UNDO"}

    _externally_finished: bool
    _cursor_modal_set: bool
    _editing: bool
    _page_id: str
    _text_id: str

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded and get_active_page(context) is not None

    def invoke(self, context, _event):
        if panel_modal_state.get_active("text_tool") is not None:
            panel_modal_state.finish_active("text_tool", context, keep_selection=True)
            return {"FINISHED"}
        panel_modal_state.finish_active("knife_cut", context, keep_selection=False)
        panel_modal_state.finish_active("edge_move", context, keep_selection=True)
        panel_modal_state.finish_active("layer_move", context, keep_selection=True)
        self._externally_finished = False
        self._cursor_modal_set = panel_modal_state.set_modal_cursor(context, "TEXT")
        self._editing = False
        self._page_id = ""
        self._text_id = ""
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
        if not _event_in_view3d_window(context, event):
            return {"PASS_THROUGH"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if event.type == "T" and event.value == "PRESS" and not event.ctrl and not event.alt:
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if (
            event.value == "PRESS"
            and event.type in {"O", "P", "F", "G", "K", "COMMA", "PERIOD", "Z", "X"}
            and not event.ctrl
            and not event.alt
        ):
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"PASS_THROUGH"}
        work, page, lx, ly = _resolve_page_from_event(context, event)
        if work is None or page is None or lx is None or ly is None:
            return {"PASS_THROUGH"}
        width = 30.0
        height = 15.0
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
        self._page_id = getattr(page, "id", "")
        self._text_id = getattr(entry, "id", "")
        self.report({"INFO"}, "本文を入力してください (Enter: 確定 / Esc: キャンセル)")
        return {"RUNNING_MODAL"}

    def _modal_editing(self, context, event):
        if not _event_in_view3d_window(context, event):
            if event.type == "LEFTMOUSE" and event.value == "PRESS":
                self._finish_current_text_edit(context)
                return {"PASS_THROUGH"}
            if not self._is_text_edit_event(event):
                return {"PASS_THROUGH"}
        if event.value != "PRESS":
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            _remove_text_by_id(context, self._page_id, self._text_id)
            self._finish_current_text_edit(context)
            return {"RUNNING_MODAL"}
        if event.type in {"RET", "NUMPAD_ENTER"}:
            if event.shift:
                return self._append_to_current_text(context, "\n")
            self._finish_current_text_edit(context)
            return {"RUNNING_MODAL"}
        if event.type == "BACK_SPACE":
            return self._backspace_current_text(context)
        if event.type == "V" and event.ctrl and not event.alt:
            clipboard = getattr(context.window_manager, "clipboard", "")
            if clipboard:
                return self._append_to_current_text(context, clipboard)
            return {"RUNNING_MODAL"}
        text = getattr(event, "unicode", "") or ""
        if text and not event.ctrl and not event.alt and not getattr(event, "oskey", False):
            return self._append_to_current_text(context, text)
        return {"RUNNING_MODAL"}

    def _is_text_edit_event(self, event) -> bool:
        if event.value != "PRESS":
            return False
        if event.type in {"ESC", "RET", "NUMPAD_ENTER", "BACK_SPACE"}:
            return True
        if event.type == "V" and event.ctrl and not event.alt:
            return True
        text = getattr(event, "unicode", "") or ""
        return bool(text and not event.ctrl and not event.alt and not getattr(event, "oskey", False))

    def _finish_current_text_edit(self, context) -> None:
        self._editing = False
        self._page_id = ""
        self._text_id = ""
        self.report({"INFO"}, "テキストツール: クリック位置にテキストを追加")
        layer_stack_utils.tag_view3d_redraw(context)

    def _current_text_entry(self, context):
        page = _find_page_by_id(context, self._page_id)
        if page is None:
            return None, None, -1
        idx = _find_text_index(page, self._text_id)
        if idx < 0:
            return page, None, -1
        return page, page.texts[idx], idx

    def _append_to_current_text(self, context, text: str):
        page, entry, idx = self._current_text_entry(context)
        if entry is None:
            self.finish_from_external(context, keep_selection=True)
            return {"CANCELLED"}
        cleaned = text.replace("\x00", "")
        if not cleaned:
            return {"RUNNING_MODAL"}
        entry.body = f"{entry.body}{cleaned}"
        page.active_text_index = idx
        if hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "text"
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return {"RUNNING_MODAL"}

    def _backspace_current_text(self, context):
        page, entry, idx = self._current_text_entry(context)
        if entry is None:
            self.finish_from_external(context, keep_selection=True)
            return {"CANCELLED"}
        if entry.body:
            entry.body = entry.body[:-1]
            page.active_text_index = idx
            layer_stack_utils.sync_layer_stack_after_data_change(context)
        return {"RUNNING_MODAL"}

    def _cleanup(self, context) -> None:
        if getattr(self, "_cursor_modal_set", False):
            panel_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        self._editing = False

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
