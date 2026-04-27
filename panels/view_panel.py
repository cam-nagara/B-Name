"""N-Panel の B-Name タブ: ビュー操作."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_PANEL, get_mode
from ..core.work import get_work

B_NAME_CATEGORY = "B-Name"


class BNAME_PT_view(Panel):
    bl_idname = "BNAME_PT_view"
    bl_label = "ビュー"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 4

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and get_mode(context) != MODE_PANEL)

    def draw(self, context):
        layout = self.layout
        is_panel_mode = get_mode(context) == MODE_PANEL
        scene = context.scene

        col = layout.column(align=True)
        col.enabled = not is_panel_mode
        row = col.row(align=True)
        row.operator("bname.view_fit_page", text="ページに合わせる", icon="ZOOM_SELECTED")
        row.operator("bname.view_fit_all", text="全ページを一覧", icon="IMGDISPLAY")
        row = col.row(align=True)
        row.prop(scene, "bname_overview_cols", text="列数")
        row.prop(scene, "bname_overview_gap_mm", text="間隔mm")


_CLASSES = (
    BNAME_PT_view,
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
