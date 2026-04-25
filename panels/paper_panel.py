"""N-Panel の B-Name タブ: 用紙設定・セーフラインオーバーレイ."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.work import get_work

B_NAME_CATEGORY = "B-Name"


class BNAME_PT_paper(Panel):
    bl_idname = "BNAME_PT_paper"
    bl_label = "用紙"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 2
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded)

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        if work is None:
            return
        p = work.paper

        # プリセット操作
        row = layout.row(align=True)
        row.label(text=f"プリセット: {p.preset_name or '(カスタム)'}")
        row = layout.row(align=True)
        row.operator("bname.paper_preset_apply", text="適用", icon="PRESET")
        row.operator("bname.paper_preset_save_local", text="保存", icon="FILE_TICK")

        box = layout.box()
        box.label(text="キャンバス")
        row = box.row(align=True)
        row.prop(p, "canvas_width_mm")
        row.prop(p, "canvas_height_mm")
        row = box.row(align=True)
        row.prop(p, "dpi")
        row.prop(p, "unit", text="")

        box = layout.box()
        box.label(text="仕上がり / 裁ち落とし")
        row = box.row(align=True)
        row.prop(p, "finish_width_mm")
        row.prop(p, "finish_height_mm")
        box.prop(p, "bleed_mm")

        box = layout.box()
        box.label(text="基本枠")
        row = box.row(align=True)
        row.prop(p, "inner_frame_width_mm")
        row.prop(p, "inner_frame_height_mm")
        row = box.row(align=True)
        row.prop(p, "inner_frame_offset_x_mm")
        row.prop(p, "inner_frame_offset_y_mm")

        box = layout.box()
        box.label(text="セーフライン")
        row = box.row(align=True)
        row.prop(p, "safe_top_mm")
        row.prop(p, "safe_bottom_mm")
        row = box.row(align=True)
        row.prop(p, "safe_gutter_mm")
        row.prop(p, "safe_fore_edge_mm")

        box = layout.box()
        box.label(text="色・線数")
        box.prop(p, "color_mode")
        box.prop(p, "default_line_count")
        box.prop(p, "paper_color")
        box.prop(p, "display_alpha", slider=True)
        box.prop(p, "color_profile")
        box.prop(p, "is_spread_layout")


class BNAME_PT_safe_area_overlay(Panel):
    bl_idname = "BNAME_PT_safe_area_overlay"
    bl_label = "セーフライン外オーバーレイ"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 3
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded)

    def draw_header(self, context):
        work = get_work(context)
        if work is not None:
            self.layout.prop(work.safe_area_overlay, "enabled", text="")

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        if work is None:
            return
        sa = work.safe_area_overlay
        layout.active = sa.enabled
        layout.prop(sa, "color")
        layout.prop(sa, "opacity")
        layout.prop(sa, "blend_mode")
        layout.label(text="書き出しには含まれません", icon="INFO")


class BNAME_PT_panel_gap(Panel):
    bl_idname = "BNAME_PT_panel_gap"
    bl_label = "コマ間隔 (作品共通)"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 4
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded)

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        if work is None:
            return
        g = work.panel_gap
        layout.prop(g, "vertical_mm")
        layout.prop(g, "horizontal_mm")


_CLASSES = (
    BNAME_PT_paper,
    BNAME_PT_safe_area_overlay,
    BNAME_PT_panel_gap,
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
