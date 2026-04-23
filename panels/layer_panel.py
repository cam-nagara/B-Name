"""レイヤーパネル (画像レイヤー / 将来 Grease Pencil レイヤー)."""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

B_NAME_CATEGORY = "B-Name"


class BNAME_UL_image_layers(UIList):
    bl_idname = "BNAME_UL_image_layers"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            row.prop(item, "visible", text="", icon="HIDE_OFF" if item.visible else "HIDE_ON", emboss=False)
            row.prop(item, "locked", text="", icon="LOCKED" if item.locked else "UNLOCKED", emboss=False)
            row.prop(item, "title", text="", emboss=False)


class BNAME_PT_image_layers(Panel):
    bl_idname = "BNAME_PT_image_layers"
    bl_label = "画像レイヤー"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 12

    def draw(self, context):
        layout = self.layout
        coll = getattr(context.scene, "bname_image_layers", None)
        if coll is None:
            layout.label(text="未初期化", icon="ERROR")
            return
        row = layout.row()
        row.template_list(
            BNAME_UL_image_layers.bl_idname,
            "",
            context.scene,
            "bname_image_layers",
            context.scene,
            "bname_active_image_layer_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("bname.image_layer_add", text="", icon="ADD")
        col.operator("bname.image_layer_remove", text="", icon="REMOVE")

        idx = context.scene.bname_active_image_layer_index
        if not (0 <= idx < len(coll)):
            return
        entry = coll[idx]
        box = layout.box()
        box.prop(entry, "filepath")
        row = box.row(align=True)
        row.prop(entry, "x_mm")
        row.prop(entry, "y_mm")
        row = box.row(align=True)
        row.prop(entry, "width_mm")
        row.prop(entry, "height_mm")
        row = box.row(align=True)
        row.prop(entry, "rotation_deg")
        row.prop(entry, "flip_x", toggle=True)
        row.prop(entry, "flip_y", toggle=True)

        box = layout.box()
        box.label(text="表示")
        box.prop(entry, "opacity")
        box.prop(entry, "blend_mode")
        box.prop(entry, "tint_color")

        box = layout.box()
        box.label(text="レベル補正")
        box.prop(entry, "brightness")
        box.prop(entry, "contrast")
        box.prop(entry, "binarize_enabled")
        sub = box.row()
        sub.enabled = entry.binarize_enabled
        sub.prop(entry, "binarize_threshold")


_CLASSES = (
    BNAME_UL_image_layers,
    BNAME_PT_image_layers,
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
