"""フキダシパネル (Phase 3 骨格)."""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from ..core.work import get_active_page

B_NAME_CATEGORY = "B-Name"


class BNAME_UL_balloons(UIList):
    bl_idname = "BNAME_UL_balloons"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            icon_name = "OUTLINER_OB_FONT" if item.shape == "none" else "MOD_FLUID"
            row.label(text=item.id, icon=icon_name)
            row.prop(item, "shape", text="", emboss=False)


class BNAME_PT_balloons(Panel):
    bl_idname = "BNAME_PT_balloons"
    bl_label = "フキダシ"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 10

    @classmethod
    def poll(cls, context):
        return get_active_page(context) is not None

    def draw(self, context):
        layout = self.layout
        balloons = getattr(context.scene, "bname_balloons", None)
        if balloons is None:
            layout.label(text="Scene.bname_balloons 未初期化", icon="ERROR")
            return
        row = layout.row()
        row.template_list(
            BNAME_UL_balloons.bl_idname,
            "",
            context.scene,
            "bname_balloons",
            context.scene,
            "bname_active_balloon_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("bname.balloon_add", text="", icon="ADD")
        col.operator("bname.balloon_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("bname.balloon_tail_add", text="", icon="PARTICLE_POINT")

        idx = context.scene.bname_active_balloon_index
        if not (0 <= idx < len(balloons)):
            return
        entry = balloons[idx]
        box = layout.box()
        box.prop(entry, "shape")
        row = box.row(align=True)
        row.prop(entry, "x_mm")
        row.prop(entry, "y_mm")
        row = box.row(align=True)
        row.prop(entry, "width_mm")
        row.prop(entry, "height_mm")
        box.prop(entry, "rotation_deg")
        box.prop(entry, "rounded_corner_enabled")
        sub = box.row()
        sub.enabled = entry.rounded_corner_enabled
        sub.prop(entry, "rounded_corner_radius_mm")

        box = layout.box()
        box.label(text="線・塗り")
        box.prop(entry, "line_style")
        box.prop(entry, "line_width_mm")
        box.prop(entry, "line_color")
        box.prop(entry, "fill_color")

        # 形状別パラメータ
        sp = entry.shape_params
        if entry.shape == "cloud":
            box = layout.box()
            box.label(text="雲パラメータ")
            box.prop(sp, "cloud_wave_count")
            box.prop(sp, "cloud_wave_amplitude_mm")
        elif entry.shape in ("spike_curve", "spike_straight"):
            box = layout.box()
            box.label(text="トゲパラメータ")
            box.prop(sp, "spike_count")
            box.prop(sp, "spike_depth_mm")
            box.prop(sp, "spike_jitter")

        # 尻尾
        box = layout.box()
        box.label(text=f"尻尾 ({len(entry.tails)})")
        for i, tail in enumerate(entry.tails):
            sub = box.box()
            sub.label(text=f"尻尾 {i + 1}")
            sub.prop(tail, "type")
            sub.prop(tail, "direction_deg")
            sub.prop(tail, "length_mm")
            row = sub.row(align=True)
            row.prop(tail, "root_width_mm")
            row.prop(tail, "tip_width_mm")
            if tail.type == "curve":
                sub.prop(tail, "curve_bend")


_CLASSES = (
    BNAME_UL_balloons,
    BNAME_PT_balloons,
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
