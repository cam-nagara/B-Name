"""コマ詳細設定ダイアログ用の描画ヘルパー."""

from __future__ import annotations

from ..utils import object_selection
from .edge_style_ui import draw_selected_edge_style_box, get_selected_coma_entry


def _same_coma_entry(left, right) -> bool:
    if left is None or right is None:
        return False
    try:
        return int(left.as_pointer()) == int(right.as_pointer())
    except Exception:  # noqa: BLE001
        pass
    left_key = str(getattr(left, "coma_id", "") or getattr(left, "id", "") or "")
    right_key = str(getattr(right, "coma_id", "") or getattr(right, "id", "") or "")
    return bool(left_key and left_key == right_key)


def draw_coma_shape_settings(layout, context, entry) -> None:
    shape_label = "矩形" if entry.shape_type == "rect" else "多角形"
    layout.label(text=f"形状: {shape_label}")
    if entry.shape_type == "rect":
        row = layout.row(align=True)
        row.prop(entry, "rect_x_mm")
        row.prop(entry, "rect_y_mm")
        row = layout.row(align=True)
        row.prop(entry, "rect_width_mm")
        row.prop(entry, "rect_height_mm")
    else:
        layout.label(text=f"頂点数: {len(entry.vertices)}", icon="VERTEXSEL")

    row = layout.row(align=True)
    row.operator(
        "bname.coma_edit_vertices",
        text="頂点/辺をドラッグ編集",
        icon="EDITMODE_HLT",
    )
    layout.label(text="(Enter=確定 / ESC=キャンセル / 緑線=スナップ)", icon="INFO")

    row = layout.row(align=True)
    if entry.shape_type == "rect":
        row.operator("bname.coma_to_polygon", text="多角形化", icon="MESH_DATA")
    else:
        row.operator("bname.coma_to_rect", text="矩形化 (外接)", icon="MESH_PLANE")
    if object_selection.selected_coma_count(context) >= 2:
        layout.operator("bname.coma_merge_selected", text="コマ結合", icon="AUTOMERGE_ON")

    layout.prop(entry, "background_color")
    row = layout.row(align=True)
    row.prop(entry, "coma_gap_vertical_mm", text="上下 (個別)")
    row.prop(entry, "coma_gap_horizontal_mm", text="左右 (個別)")
    layout.label(text="(負値は作品共通ルールを継承)", icon="INFO")


def draw_coma_border_settings(layout, context, entry) -> None:
    b = entry.border
    layout.prop(b, "visible", text="枠線を表示")
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

    selected_coma = get_selected_coma_entry(context)
    if _same_coma_entry(selected_coma, entry):
        draw_selected_edge_style_box(layout, context)


def draw_coma_white_margin_settings(layout, entry) -> None:
    wm = entry.white_margin
    layout.prop(wm, "enabled", text="白フチを表示")
    content = layout.column()
    content.active = wm.enabled
    content.prop(wm, "width_mm")
    content.prop(wm, "color")
    box = content.box()
    box.label(text="辺ごとオーバーライド")
    _draw_white_margin_edge(box, "上", wm.edge_top)
    _draw_white_margin_edge(box, "右", wm.edge_right)
    _draw_white_margin_edge(box, "下", wm.edge_bottom)
    _draw_white_margin_edge(box, "左", wm.edge_left)


def _draw_border_edge(layout, label: str, edge) -> None:
    row = layout.row(align=True)
    row.prop(edge, "use_override", text=label)
    sub = row.row(align=True)
    sub.enabled = edge.use_override
    sub.prop(edge, "style", text="")
    sub.prop(edge, "width_mm", text="w")
    sub.prop(edge, "visible", text="", icon="HIDE_OFF" if edge.visible else "HIDE_ON")


def _draw_white_margin_edge(layout, label: str, edge) -> None:
    row = layout.row(align=True)
    row.prop(edge, "use_override", text=label)
    sub = row.row(align=True)
    sub.enabled = edge.use_override
    sub.prop(edge, "enabled", text="ON")
    sub.prop(edge, "width_mm", text="w")


def register() -> None:
    pass


def unregister() -> None:
    pass
