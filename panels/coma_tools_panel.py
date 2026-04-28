"""N-Panel の B-Name タブ: コマ枠線ツール."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.work import get_active_page
from .edge_style_ui import draw_selected_edge_style_box

B_NAME_CATEGORY = "B-Name"


class BNAME_PT_coma_tools(Panel):
    bl_idname = "BNAME_PT_coma_tools"
    bl_label = "枠線ツール"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 7

    @classmethod
    def poll(cls, context):
        return get_active_page(context) is not None

    def draw(self, context):
        layout = self.layout
        layout.operator("bname.coma_split_template", text="縦横均等分割", icon="GRID")
        layout.operator(
            "bname.coma_knife_cut",
            text="枠線カットツール (F)",
            icon="SCULPTMODE_HLT",
        )
        layout.operator(
            "bname.coma_edge_move",
            text="枠線選択ツール (G)",
            icon="EMPTY_ARROWS",
        )
        draw_selected_edge_style_box(layout, context)


_CLASSES = (
    BNAME_PT_coma_tools,
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
