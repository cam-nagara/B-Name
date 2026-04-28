"""Inline text selection style popup."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_work
from ..utils import layer_stack as layer_stack_utils
from ..utils import text_style
from . import panel_modal_state


def _find_page_by_id(context, page_id: str):
    work = get_work(context)
    if work is None:
        return None
    for page in work.pages:
        if str(getattr(page, "id", "") or "") == str(page_id or ""):
            return page
    return None


def _find_text_entry(context, page_id: str, text_id: str):
    page = _find_page_by_id(context, page_id)
    if page is None:
        return None, None, -1
    for index, entry in enumerate(page.texts):
        if str(getattr(entry, "id", "") or "") == str(text_id or ""):
            return page, entry, index
    return page, None, -1


def _active_text_tool_matches(page_id: str, text_id: str) -> bool:
    op = panel_modal_state.get_active("text_tool")
    return (
        op is not None
        and bool(getattr(op, "_editing", False))
        and str(getattr(op, "_page_id", "") or "") == str(page_id or "")
        and str(getattr(op, "_text_id", "") or "") == str(text_id or "")
    )


class BNAME_OT_text_selection_style_popup(Operator):
    """選択中のインラインテキスト範囲へ文字スタイルを適用する."""

    bl_idname = "bname.text_selection_style_popup"
    bl_label = "選択文字設定"
    bl_options = {"REGISTER", "UNDO"}

    page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    text_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    start: IntProperty(default=0, min=0, options={"HIDDEN"})  # type: ignore[valid-type]
    end: IntProperty(default=0, min=0, options={"HIDDEN"})  # type: ignore[valid-type]
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    font_bold: BoolProperty(name="太字", default=False)  # type: ignore[valid-type]
    font_italic: BoolProperty(name="斜体", default=False)  # type: ignore[valid-type]
    font_size_q: FloatProperty(name="サイズ (Q)", default=20.0, min=1.0, soft_max=200.0)  # type: ignore[valid-type]
    font_choice: EnumProperty(name="フォント", items=text_style.font_dropdown_items)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return get_work(context) is not None

    def invoke(self, context, event):
        page, entry, _idx = _find_text_entry(context, self.page_id, self.text_id)
        if page is None or entry is None:
            self.report({"ERROR"}, "選択中のテキストが見つかりません")
            return {"CANCELLED"}
        start, end = self._bounds(entry)
        if start >= end:
            self.report({"ERROR"}, "文字範囲を選択してください")
            return {"CANCELLED"}
        self.start = start
        self.end = end
        style = text_style.style_for_index(entry, start)
        self.font_choice = text_style.dropdown_choice_for_font_path(style[0])
        self.font_size_q = float(style[1])
        self.color = style[2]
        self.font_bold = bool(style[3])
        self.font_italic = bool(style[4])
        return context.window_manager.invoke_props_popup(self, event)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "color")
        row = layout.row(align=True)
        row.prop(self, "font_bold", toggle=True)
        row.prop(self, "font_italic", toggle=True)
        layout.prop(self, "font_size_q")
        layout.prop(self, "font_choice")

    def check(self, context):
        self._apply(context)
        return True

    def execute(self, context):
        return {"FINISHED"} if self._apply(context) else {"CANCELLED"}

    def _bounds(self, entry) -> tuple[int, int]:
        body_len = len(str(getattr(entry, "body", "") or ""))
        start = max(0, min(body_len, int(self.start)))
        end = max(start, min(body_len, int(self.end)))
        return start, end

    def _apply(self, context) -> bool:
        page, entry, idx = _find_text_entry(context, self.page_id, self.text_id)
        if page is None or entry is None:
            return False
        start, end = self._bounds(entry)
        if start >= end:
            return False
        font = text_style.font_path_from_dropdown_choice(self.font_choice)
        if not text_style.apply_style_span(
            entry,
            start,
            end,
            font=font,
            font_size_q=self.font_size_q,
            color=self.color,
            bold=self.font_bold,
            italic=self.font_italic,
        ):
            return False
        page.active_text_index = idx
        if _active_text_tool_matches(self.page_id, self.text_id):
            active = panel_modal_state.get_active("text_tool")
            if active is not None:
                active._selection_anchor = start
                active._cursor_index = end
        layer_stack_utils.tag_view3d_redraw(context)
        return True


_CLASSES = (BNAME_OT_text_selection_style_popup,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
