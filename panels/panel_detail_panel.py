"""選択中コマの詳細設定パネル (形状/枠線/白フチ/重なりくり抜き)."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.work import get_active_page
from .edge_style_ui import draw_selected_edge_style_box

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
    bl_options = {"DEFAULT_CLOSED"}

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

        # 頂点/辺のドラッグ編集 (modal)
        row = layout.row(align=True)
        row.operator(
            "bname.panel_edit_vertices",
            text="頂点/辺をドラッグ編集",
            icon="EDITMODE_HLT",
        )
        layout.label(text="(Enter=確定 / ESC=キャンセル / 緑線=スナップ)", icon="INFO")

        # 形状変換ボタン
        row = layout.row(align=True)
        if entry.shape_type == "rect":
            row.operator("bname.panel_to_polygon", text="多角形化", icon="MESH_DATA")
        else:
            row.operator("bname.panel_to_rect", text="矩形化 (外接)", icon="MESH_PLANE")

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
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        if _get_active_panel(context) is not None:
            return True
        return getattr(context.window_manager, "bname_edge_select_kind", "none") != "none"

    def draw_header(self, context):
        entry = _get_active_panel(context)
        if entry is not None:
            self.layout.prop(entry.border, "visible", text="")

    def draw(self, context):
        layout = self.layout
        entry = _get_active_panel(context)
        if entry is not None:
            b = entry.border
            content = layout.column()
            content.active = b.visible
            content.prop(b, "style")
            content.prop(b, "width_mm")
            content.prop(b, "color")
            row = content.row(align=True)
            row.prop(b, "corner_type")
            sub = row.row(align=True)
            sub.enabled = b.corner_type != "square"
            sub.prop(b, "corner_radius_mm", text="半径")

            box = content.box()
            box.label(text="辺ごとオーバーライド")
            _draw_border_edge(box, "上", b.edge_top)
            _draw_border_edge(box, "右", b.edge_right)
            _draw_border_edge(box, "下", b.edge_bottom)
            _draw_border_edge(box, "左", b.edge_left)

        draw_selected_edge_style_box(layout, context)


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
    bl_options = {"DEFAULT_CLOSED"}

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
