"""N-Panel の B-Name タブ: 共通ツールボタン."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_PANEL, get_mode
from ..core.work import get_work
from ..operators import panel_modal_state

B_NAME_CATEGORY = "B-Name"
_MODAL_TOOL_NAMES = (
    "object_tool",
    "knife_cut",
    "edge_move",
    "layer_move",
    "balloon_tool",
    "text_tool",
    "effect_line_tool",
    "panel_vertex_edit",
)


def _active_stack_kind(context) -> str:
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bname_layer_stack", None) if scene is not None else None
    idx = int(getattr(scene, "bname_active_layer_stack_index", -1)) if scene is not None else -1
    if stack is None or not (0 <= idx < len(stack)):
        return ""
    return str(getattr(stack[idx], "kind", "") or "")


def _any_bname_modal_tool_active() -> bool:
    return any(panel_modal_state.is_active(name) for name in _MODAL_TOOL_NAMES)


class BNAME_PT_tools(Panel):
    bl_idname = "BNAME_PT_tools"
    bl_label = "ツール"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 3

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded and get_mode(context) != MODE_PANEL)

    def draw(self, context):
        layout = self.layout
        obj = None
        try:
            from ..utils import gpencil as gp_utils

            obj = gp_utils.get_master_gpencil()
        except Exception:  # noqa: BLE001
            obj = None
        mode = getattr(obj, "mode", "") if obj is not None else ""
        active_stack_kind = _active_stack_kind(context)
        gp_layer_active = (
            active_stack_kind == "gp"
            and getattr(context.scene, "bname_active_layer_kind", "") == "gp"
        )
        modal_tool_active = _any_bname_modal_tool_active()

        row = layout.row(align=True)
        op = row.operator(
            "bname.gpencil_master_mode_set",
            text="",
            icon="OBJECT_DATAMODE",
            depress=(
                panel_modal_state.is_active("object_tool")
                or (not modal_tool_active and mode == "OBJECT")
            ),
        )
        op.mode = "OBJECT"
        draw_slot = row.row(align=True)
        draw_slot.enabled = gp_layer_active
        op = draw_slot.operator(
            "bname.gpencil_master_mode_set",
            text="",
            icon="OUTLINER_OB_GREASEPENCIL",
            depress=(
                not modal_tool_active
                and gp_layer_active
                and mode == "PAINT_GREASE_PENCIL"
            ),
        )
        op.mode = "PAINT_GREASE_PENCIL"
        edit_slot = row.row(align=True)
        edit_slot.enabled = gp_layer_active
        op = edit_slot.operator(
            "bname.gpencil_master_mode_set",
            text="",
            icon="EDITMODE_HLT",
            depress=(not modal_tool_active and gp_layer_active and mode == "EDIT"),
        )
        op.mode = "EDIT"

        row.separator()
        row.operator_context = "INVOKE_DEFAULT"
        row.operator(
            "bname.panel_knife_cut",
            text="",
            icon="SCULPTMODE_HLT",
            depress=panel_modal_state.is_active("knife_cut"),
        )
        row.operator(
            "bname.panel_edge_move",
            text="",
            icon="EMPTY_ARROWS",
            depress=panel_modal_state.is_active("edge_move"),
        )
        row.operator(
            "bname.layer_move_tool",
            text="",
            icon="DRIVER_TRANSFORM",
            depress=panel_modal_state.is_active("layer_move"),
        )
        row.operator(
            "bname.balloon_tool",
            text="",
            icon="MOD_FLUID",
            depress=panel_modal_state.is_active("balloon_tool"),
        )
        row.operator(
            "bname.text_tool",
            text="",
            icon="FONT_DATA",
            depress=panel_modal_state.is_active("text_tool"),
        )
        row.operator(
            "bname.effect_line_tool",
            text="",
            icon="STROKE",
            depress=panel_modal_state.is_active("effect_line_tool"),
        )


_CLASSES = (BNAME_PT_tools,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
