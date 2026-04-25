"""効果線ツールパネル (Phase 3 骨格)."""

from __future__ import annotations

import bpy
from bpy.types import Panel

B_NAME_CATEGORY = "B-Name"


class BNAME_PT_effect_line(Panel):
    bl_idname = "BNAME_PT_effect_line"
    bl_label = "効果線"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 11
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        params = getattr(context.scene, "bname_effect_line_params", None)
        if params is None:
            layout.label(text="未初期化", icon="ERROR")
            return

        box = layout.box()
        box.label(text="種類")
        box.prop(params, "effect_type")
        box.prop(params, "base_shape")
        if params.base_shape == "polygon":
            box.prop(params, "base_vertex_count")
        box.prop(params, "start_from_center")
        box.prop(params, "rotation_deg")

        box = layout.box()
        box.label(text="線")
        box.prop(params, "brush_size_mm")
        row = box.row(align=True)
        row.prop(params, "brush_jitter_enabled", text="乱れ")
        sub = row.row()
        sub.enabled = params.brush_jitter_enabled
        sub.prop(params, "brush_jitter_amount", text="")

        box.prop(params, "spacing_mode")
        if params.spacing_mode == "angle":
            box.prop(params, "spacing_angle_deg")
        else:
            box.prop(params, "spacing_distance_mm")

        box.prop(params, "length_mm")
        box.prop(params, "extend_past_panel")

        box = layout.box()
        box.label(text="基準位置 / ギザ")
        box.prop(params, "base_position")
        box.prop(params, "base_position_offset")
        box.prop(params, "base_jagged_enabled")
        sub = box.column()
        sub.enabled = params.base_jagged_enabled
        sub.prop(params, "base_jagged_count")
        sub.prop(params, "base_jagged_height_mm")

        box = layout.box()
        box.label(text="入り抜き")
        box.prop(params, "inout_apply")
        row = box.row(align=True)
        row.prop(params, "in_percent")
        row.prop(params, "out_percent")

        box = layout.box()
        box.label(text="色")
        box.prop(params, "line_color")
        if params.effect_type == "beta_flash":
            box.prop(params, "fill_color")
            box.prop(params, "fill_opacity")
            box.prop(params, "fill_base_shape")

        if params.effect_type == "speed":
            box = layout.box()
            box.label(text="流線")
            box.prop(params, "speed_angle_deg")
            box.prop(params, "speed_line_count")

        layout.operator("bname.effect_line_generate", icon="STROKE")


_CLASSES = (BNAME_PT_effect_line,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
