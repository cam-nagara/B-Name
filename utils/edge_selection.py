"""B-Name のコマ辺選択状態を共有するヘルパ."""

from __future__ import annotations


def tag_view3d_redraw(context) -> None:
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in getattr(screen, "areas", []):
        if getattr(area, "type", "") == "VIEW_3D":
            try:
                area.tag_redraw()
            except Exception:  # noqa: BLE001
                pass


def set_selection(
    context,
    kind: str,
    *,
    page_index: int = -1,
    panel_index: int = -1,
    edge_index: int = -1,
    vertex_index: int = -1,
    sync_style: bool = True,
) -> bool:
    """アクティブなコマ辺選択を WindowManager プロパティに保存する."""
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bname_edge_select_kind"):
        return False
    if kind not in {"none", "edge", "border", "vertex"}:
        kind = "none"
    if kind == "none":
        page_index = panel_index = edge_index = vertex_index = -1
    try:
        wm.bname_edge_select_kind = kind
        wm.bname_edge_select_page = int(page_index)
        wm.bname_edge_select_panel = int(panel_index)
        wm.bname_edge_select_edge = int(edge_index) if kind == "edge" else -1
        wm.bname_edge_select_vertex = int(vertex_index) if kind == "vertex" else -1
    except Exception:  # noqa: BLE001
        return False
    if kind != "none":
        try:
            from ..core.work import get_work

            work = get_work(context)
            if work is not None and 0 <= int(page_index) < len(work.pages):
                work.active_page_index = int(page_index)
                page = work.pages[int(page_index)]
                if 0 <= int(panel_index) < len(page.panels):
                    page.active_panel_index = int(panel_index)
        except Exception:  # noqa: BLE001
            pass
    if sync_style and kind != "none":
        try:
            from ..operators import panel_edge_style_op

            panel_edge_style_op.sync_selected_style_props(context)
        except Exception:  # noqa: BLE001
            pass
    tag_view3d_redraw(context)
    return True


def clear_selection(context) -> bool:
    return set_selection(context, "none")
