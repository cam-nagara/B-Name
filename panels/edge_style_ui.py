"""枠線選択ツールの選択対象に対するスタイル編集 UI."""

from __future__ import annotations

from ..core.work import get_work


def _find_edge_override(panel_entry, edge_index: int):
    for style in panel_entry.edge_styles:
        if int(style.edge_index) == int(edge_index):
            return style
    return None


def _panel_edge_count(panel_entry) -> int:
    if getattr(panel_entry, "shape_type", "rect") == "rect":
        return 4
    return len(getattr(panel_entry, "vertices", []))


def _selected_panel_context(context):
    wm = context.window_manager
    kind = getattr(wm, "bname_edge_select_kind", "none")
    if kind == "none":
        return None
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    page_index = int(getattr(wm, "bname_edge_select_page", -1))
    panel_index = int(getattr(wm, "bname_edge_select_panel", -1))
    if not (0 <= page_index < len(work.pages)):
        return None
    page = work.pages[page_index]
    if not (0 <= panel_index < len(page.panels)):
        return None
    panel_entry = page.panels[panel_index]
    return (wm, kind, page_index, panel_index, page, panel_entry)


def draw_selected_edge_style_box(layout, context) -> bool:
    selected = _selected_panel_context(context)
    if selected is None:
        return False

    wm, kind, page_index, _panel_index, _page, panel_entry = selected
    box = layout.box()

    if kind == "border":
        box.label(
            text=f"選択中の枠線全体: P{page_index:04d} {panel_entry.id}",
            icon="MESH_DATA",
        )
        box.prop(wm, "bname_edge_style_color", text="線色")
        box.prop(wm, "bname_edge_style_width_mm", text="線幅 (mm)")
        box.operator("bname.edge_style_clear_all", text="全ての個別設定を削除", icon="X")
        return True

    if kind == "edge":
        edge_index = int(getattr(wm, "bname_edge_select_edge", -1))
        override = _find_edge_override(panel_entry, edge_index)
        box.label(
            text=f"選択中の辺 [{edge_index}] : P{page_index:04d} {panel_entry.id}",
            icon="EDGESEL",
        )
        if override is None:
            box.label(
                text="継承中です。変更するとこの辺だけ個別設定になります。",
                icon="LINKED",
            )
        else:
            box.label(text="この辺は個別設定です。", icon="UNLINKED")
        box.prop(wm, "bname_edge_style_color", text="線色")
        box.prop(wm, "bname_edge_style_width_mm", text="線幅 (mm)")
        if override is not None:
            box.operator("bname.edge_style_remove", text="この辺の個別設定を削除", icon="X")
        return True

    if kind == "vertex":
        vertex_index = int(getattr(wm, "bname_edge_select_vertex", -1))
        edge_count = _panel_edge_count(panel_entry)
        if edge_count <= 0 or not (0 <= vertex_index < edge_count):
            return False
        prev_edge = (vertex_index - 1 + edge_count) % edge_count
        next_edge = vertex_index
        prev_override = _find_edge_override(panel_entry, prev_edge)
        next_override = _find_edge_override(panel_entry, next_edge)
        box.label(
            text=f"選択中の頂点 [{vertex_index}] : P{page_index:04d} {panel_entry.id}",
            icon="VERTEXSEL",
        )
        if prev_override is None and next_override is None:
            box.label(
                text="継承中です。変更すると接続する2辺に個別設定を作成します。",
                icon="LINKED",
            )
        elif (
            prev_override is not None
            and next_override is not None
            and (
                tuple(prev_override.color) != tuple(next_override.color)
                or abs(float(prev_override.width_mm) - float(next_override.width_mm)) > 1e-6
            )
        ):
            box.label(
                text="接続2辺の設定が異なります。変更すると同じ値で揃えます。",
                icon="INFO",
            )
        else:
            box.label(
                text=f"接続辺 [{prev_edge}] と [{next_edge}] に同じ値を適用します。",
                icon="UNLINKED",
            )
        box.prop(wm, "bname_edge_style_color", text="線色")
        box.prop(wm, "bname_edge_style_width_mm", text="線幅 (mm)")
        if prev_override is not None or next_override is not None:
            box.operator("bname.vertex_style_remove", text="この頂点の個別設定を削除", icon="X")
        return True

    return False
