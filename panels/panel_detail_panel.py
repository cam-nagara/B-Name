"""選択中コマの詳細設定パネル (形状/枠線/白フチ/重なりくり抜き)."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.work import get_active_page

B_NAME_CATEGORY = "B-Name"


def _get_active_panel(context):
    page = get_active_page(context)
    if page is None:
        return None
    idx = page.active_panel_index
    if not (0 <= idx < len(page.panels)):
        return None
    return page.panels[idx]


class BNAME_PT_panel_shape(Panel):
    bl_idname = "BNAME_PT_panel_shape"
    bl_label = "コマ: 形状"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 7

    @classmethod
    def poll(cls, context):
        return _get_active_panel(context) is not None

    def draw(self, context):
        layout = self.layout
        entry = _get_active_panel(context)
        if entry is None:
            return
        layout.prop(entry, "shape_type")
        if entry.shape_type == "rect":
            row = layout.row(align=True)
            row.prop(entry, "rect_x_mm")
            row.prop(entry, "rect_y_mm")
            row = layout.row(align=True)
            row.prop(entry, "rect_width_mm")
            row.prop(entry, "rect_height_mm")
        else:
            layout.label(text=f"頂点数: {len(entry.vertices)}", icon="VERTEXSEL")
        layout.prop(entry, "overlap_clipping")
        row = layout.row(align=True)
        row.prop(entry, "panel_gap_vertical_mm", text="上下 (個別)")
        row.prop(entry, "panel_gap_horizontal_mm", text="左右 (個別)")
        layout.label(text="(負値は作品共通ルールを継承)", icon="INFO")


class BNAME_PT_panel_border(Panel):
    bl_idname = "BNAME_PT_panel_border"
    bl_label = "コマ: 枠線"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 8

    @classmethod
    def poll(cls, context):
        return _get_active_panel(context) is not None

    def draw_header(self, context):
        entry = _get_active_panel(context)
        if entry is not None:
            self.layout.prop(entry.border, "visible", text="")

    def draw(self, context):
        layout = self.layout
        entry = _get_active_panel(context)
        if entry is None:
            return
        b = entry.border
        layout.active = b.visible
        layout.prop(b, "style")
        layout.prop(b, "width_mm")
        layout.prop(b, "color")
        row = layout.row(align=True)
        row.prop(b, "corner_type")
        sub = row.row(align=True)
        sub.enabled = b.corner_type != "square"
        sub.prop(b, "corner_radius_mm", text="半径")

        # 辺ごとオーバーライド
        box = layout.box()
        box.label(text="辺ごとオーバーライド")
        _draw_border_edge(box, "上", b.edge_top)
        _draw_border_edge(box, "右", b.edge_right)
        _draw_border_edge(box, "下", b.edge_bottom)
        _draw_border_edge(box, "左", b.edge_left)


def _draw_border_edge(layout, label: str, edge) -> None:
    row = layout.row(align=True)
    row.prop(edge, "use_override", text=label)
    sub = row.row(align=True)
    sub.enabled = edge.use_override
    sub.prop(edge, "style", text="")
    sub.prop(edge, "width_mm", text="w")
    sub.prop(edge, "visible", text="", icon="HIDE_OFF" if edge.visible else "HIDE_ON")


class BNAME_PT_panel_white_margin(Panel):
    bl_idname = "BNAME_PT_panel_white_margin"
    bl_label = "コマ: 白フチ"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 9

    @classmethod
    def poll(cls, context):
        return _get_active_panel(context) is not None

    def draw_header(self, context):
        entry = _get_active_panel(context)
        if entry is not None:
            self.layout.prop(entry.white_margin, "enabled", text="")

    def draw(self, context):
        layout = self.layout
        entry = _get_active_panel(context)
        if entry is None:
            return
        wm = entry.white_margin
        layout.active = wm.enabled
        layout.prop(wm, "width_mm")
        layout.prop(wm, "color")
        box = layout.box()
        box.label(text="辺ごとオーバーライド")
        _draw_white_margin_edge(box, "上", wm.edge_top)
        _draw_white_margin_edge(box, "右", wm.edge_right)
        _draw_white_margin_edge(box, "下", wm.edge_bottom)
        _draw_white_margin_edge(box, "左", wm.edge_left)


def _draw_white_margin_edge(layout, label: str, edge) -> None:
    row = layout.row(align=True)
    row.prop(edge, "use_override", text=label)
    sub = row.row(align=True)
    sub.enabled = edge.use_override
    sub.prop(edge, "enabled", text="ON")
    sub.prop(edge, "width_mm", text="w")


_CLASSES = (
    BNAME_PT_panel_shape,
    BNAME_PT_panel_border,
    BNAME_PT_panel_white_margin,
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
