"""N-Panel の B-Name タブ: 共通ツールボタン."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.work import get_work

B_NAME_CATEGORY = "B-Name"


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
        return bool(work and work.loaded)

    def draw(self, context):
        layout = self.layout
        obj = None
        try:
            from ..utils import gpencil as gp_utils

            obj = gp_utils.get_master_gpencil()
        except Exception:  # noqa: BLE001
            obj = None
        mode = getattr(obj, "mode", "") if obj is not None else ""

        row = layout.row(align=True)
        op = row.operator(
            "bname.gpencil_master_mode_set",
            text="",
            icon="OBJECT_DATAMODE",
            depress=(mode == "OBJECT"),
        )
        op.mode = "OBJECT"
        op = row.operator(
            "bname.gpencil_master_mode_set",
            text="",
            icon="OUTLINER_OB_GREASEPENCIL",
            depress=(mode == "PAINT_GREASE_PENCIL"),
        )
        op.mode = "PAINT_GREASE_PENCIL"
        op = row.operator(
            "bname.gpencil_master_mode_set",
            text="",
            icon="EDITMODE_HLT",
            depress=(mode == "EDIT"),
        )
        op.mode = "EDIT"

        row.separator()
        row.operator("bname.panel_knife_cut", text="", icon="SCULPTMODE_HLT")
        row.operator("bname.panel_edge_move", text="", icon="EMPTY_ARROWS")
        row.operator("bname.layer_move_tool", text="", icon="TRANSFORM_MOVE")


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
