"""効果線ツールパネル (Phase 3 骨格)."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..utils import balloon_shapes

B_NAME_CATEGORY = "B-Name"


def _draw_shape_settings(layout, params, prefix: str, label: str, *, frame_toggle: bool = False) -> None:
    box = layout.box()
    box.label(text=label)
    if frame_toggle:
        box.prop(params, "start_to_coma_frame")
    content = box.column(align=True)
    if frame_toggle:
        content.enabled = not bool(params.start_to_coma_frame)
    shape_attr = f"{prefix}_shape"
    content.prop(params, shape_attr)
    shape = balloon_shapes.normalize_shape(getattr(params, shape_attr))
    if shape == "rect":
        rounded_attr = f"{prefix}_rounded_corner_enabled"
        content.prop(params, rounded_attr)
        sub = content.row()
        sub.enabled = bool(getattr(params, rounded_attr))
        sub.prop(params, f"{prefix}_rounded_corner_radius_mm")
    if balloon_shapes.is_dynamic_meldex_shape(shape):
        content.prop(params, f"{prefix}_cloud_bump_width_mm")
        content.prop(params, f"{prefix}_cloud_bump_height_mm")
        content.prop(params, f"{prefix}_cloud_offset_percent")
        row = content.row(align=True)
        row.prop(params, f"{prefix}_cloud_sub_width_ratio")
        row.prop(params, f"{prefix}_cloud_sub_height_ratio")


def _draw_white_outline_settings(layout, params) -> None:
    box = layout.box()
    box.label(text="白抜き線")
    row = box.row(align=True)
    row.prop(params, "white_outline_count")
    row.prop(params, "white_outline_spacing_mm")
    box.prop(params, "white_outline_width_mm")
    row = box.row(align=True)
    row.prop(params, "white_outline_width_jitter_enabled")
    sub = row.row()
    sub.enabled = params.white_outline_width_jitter_enabled
    sub.prop(params, "white_outline_width_min_percent", text="最小")
    row = box.row(align=True)
    row.prop(params, "white_outline_length_jitter_enabled")
    sub = row.row()
    sub.enabled = params.white_outline_length_jitter_enabled
    sub.prop(params, "white_outline_length_min_percent", text="最小")
    box.prop(params, "white_outline_white_ratio_percent")
    row = box.row(align=True)
    row.prop(params, "white_outline_white_brush_mm")
    row.prop(params, "white_outline_white_attenuation")
    row = box.row(align=True)
    row.prop(params, "white_outline_black_brush_mm")
    row.prop(params, "white_outline_black_attenuation")
    box.prop(params, "white_outline_angle_deg")


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
        if params.effect_type not in {"white_outline", "speed"}:
            box.prop(params, "rotation_deg")

        if params.effect_type == "white_outline":
            _draw_white_outline_settings(layout, params)
            layout.operator("bname.effect_line_generate", icon="STROKE")
            return

        if params.effect_type != "speed":
            _draw_shape_settings(layout, params, "start", "始点形状", frame_toggle=True)
            _draw_shape_settings(layout, params, "end", "終点形状")

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
