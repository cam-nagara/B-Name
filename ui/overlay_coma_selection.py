"""選択中の B-Name コマ辺を POST_PIXEL で描画する."""

from __future__ import annotations

import math

import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader

from ..utils import geom, object_selection, page_browser, page_grid, viewport_colors
from . import overlay_visibility

_LINE_WIDTH_PX = 4.0
_VERTEX_MARKER_SIZE_PX = 6.0
_HANDLE_SIZE_PX = 20.0
_HANDLE_OFFSET_PX = 21.0


def draw(context, work, region, rv3d) -> None:
    """枠線選択ツール外でも、選択中の辺/全辺/頂点を描画する."""
    if region is None or rv3d is None:
        return
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    selected_refs = object_selection.selected_coma_refs(context)
    active_coma_selection = getattr(scene, "bname_active_layer_kind", "") == "coma"
    if not active_coma_selection and not selected_refs:
        return
    try:
        from ..operators import coma_modal_state

        if coma_modal_state.is_active("edge_move"):
            return
    except Exception:  # noqa: BLE001
        pass

    wm = getattr(context, "window_manager", None)
    if wm is None:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    for page_index, page, _coma_index, panel in selected_refs:
        if not overlay_visibility.page_visible(page) or not overlay_visibility.coma_visible(panel):
            continue
        poly = _coma_polygon(panel)
        if len(poly) < 2:
            continue
        ox, oy = _page_offset(context, work, page_index)
        world_poly = [(x + ox, y + oy) for x, y in poly]
        for edge_index in range(len(world_poly)):
            _draw_edge(shader, region, rv3d, world_poly, edge_index)
    if not active_coma_selection:
        return
    kind = getattr(wm, "bname_edge_select_kind", "none")
    if kind not in {"edge", "border", "vertex"}:
        return
    page_index = int(getattr(wm, "bname_edge_select_page", -1))
    coma_index = int(getattr(wm, "bname_edge_select_coma", -1))
    if not (0 <= page_index < len(work.pages)):
        return
    page = work.pages[page_index]
    if not overlay_visibility.page_visible(page):
        return
    if not (0 <= coma_index < len(page.comas)):
        return
    panel = page.comas[coma_index]
    if not overlay_visibility.coma_visible(panel):
        return
    poly = _coma_polygon(panel)
    if len(poly) < 2:
        return
    ox, oy = _page_offset(context, work, page_index)
    world_poly = [(x + ox, y + oy) for x, y in poly]

    if kind == "edge":
        edge_index = int(getattr(wm, "bname_edge_select_edge", -1))
        _draw_edge(shader, region, rv3d, world_poly, edge_index)
    elif kind == "border":
        for edge_index in range(len(world_poly)):
            _draw_edge(shader, region, rv3d, world_poly, edge_index)
    elif kind == "vertex":
        vertex_index = int(getattr(wm, "bname_edge_select_vertex", -1))
        _draw_vertex(shader, region, rv3d, world_poly, vertex_index)


def _page_offset(context, work, page_index: int) -> tuple[float, float]:
    scene = getattr(context, "scene", None)
    area = getattr(context, "area", None)
    if page_browser.is_page_browser_area(context) and page_browser.fit_enabled(scene):
        return page_browser.page_offset_mm(work, scene, area, page_index)
    paper = work.paper
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    ox, oy = page_grid.page_grid_offset_mm(
        page_index,
        cols,
        gap,
        float(paper.canvas_width_mm),
        float(paper.canvas_height_mm),
        getattr(paper, "start_side", "right"),
        getattr(paper, "read_direction", "left"),
    )
    add_x, add_y = page_grid.page_manual_offset_mm(work.pages[page_index])
    return ox + add_x, oy + add_y


def _coma_polygon(panel) -> list[tuple[float, float]]:
    if getattr(panel, "shape_type", "") == "rect":
        x = float(panel.rect_x_mm)
        y = float(panel.rect_y_mm)
        w = float(panel.rect_width_mm)
        h = float(panel.rect_height_mm)
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if getattr(panel, "shape_type", "") == "polygon":
        return [(float(v.x_mm), float(v.y_mm)) for v in getattr(panel, "vertices", [])]
    return []


def _world_to_region(region, rv3d, point: tuple[float, float]) -> tuple[float, float] | None:
    coord = location_3d_to_region_2d(
        region,
        rv3d,
        (geom.mm_to_m(point[0]), geom.mm_to_m(point[1]), 0.0),
    )
    if coord is None:
        return None
    return float(coord.x), float(coord.y)


def _draw_edge(shader, region, rv3d, poly: list[tuple[float, float]], edge_index: int) -> None:
    if len(poly) < 2 or not (0 <= edge_index < len(poly)):
        return
    a = poly[edge_index]
    b = poly[(edge_index + 1) % len(poly)]
    ap = _world_to_region(region, rv3d, a)
    bp = _world_to_region(region, rv3d, b)
    if ap is None or bp is None:
        return
    try:
        gpu.state.line_width_set(_LINE_WIDTH_PX)
        batch = batch_for_shader(shader, "LINES", {"pos": [ap, bp]})
        shader.bind()
        shader.uniform_float("color", viewport_colors.SELECTION_STRONG)
        batch.draw(shader)
    finally:
        try:
            gpu.state.line_width_set(1.0)
        except Exception:  # noqa: BLE001
            pass
    _draw_square_marker(shader, ap)
    _draw_square_marker(shader, bp)
    for handle, direction_idx in _handle_centers(ap, bp):
        _draw_triangle_handle(shader, handle, ap, bp, direction_idx)


def _draw_vertex(shader, region, rv3d, poly: list[tuple[float, float]], vertex_index: int) -> None:
    if len(poly) < 2 or not (0 <= vertex_index < len(poly)):
        return
    coord = _world_to_region(region, rv3d, poly[vertex_index])
    if coord is not None:
        _draw_square_marker(shader, coord)


def _handle_centers(
    edge_a_px: tuple[float, float],
    edge_b_px: tuple[float, float],
) -> tuple[tuple[tuple[float, float], int], ...]:
    mx = (edge_a_px[0] + edge_b_px[0]) * 0.5
    my = (edge_a_px[1] + edge_b_px[1]) * 0.5
    dx = edge_b_px[0] - edge_a_px[0]
    dy = edge_b_px[1] - edge_a_px[1]
    length = math.hypot(dx, dy)
    if length < 1.0e-6:
        return ()
    nx = -dy / length
    ny = dx / length
    return (
        ((mx + nx * _HANDLE_OFFSET_PX, my + ny * _HANDLE_OFFSET_PX), 1),
        ((mx - nx * _HANDLE_OFFSET_PX, my - ny * _HANDLE_OFFSET_PX), 2),
    )


def _draw_triangle_handle(
    shader,
    center: tuple[float, float],
    edge_a_px: tuple[float, float],
    edge_b_px: tuple[float, float],
    direction_idx: int,
) -> None:
    cx, cy = center
    ex = edge_b_px[0] - edge_a_px[0]
    ey = edge_b_px[1] - edge_a_px[1]
    length = math.hypot(ex, ey)
    if length < 1.0e-6:
        return
    nx = -ey / length
    ny = ex / length
    if direction_idx == 2:
        nx, ny = -nx, -ny
    tx = ex / length
    ty = ey / length
    size = _HANDLE_SIZE_PX
    apex = (cx + nx * size, cy + ny * size)
    base_l = (cx - tx * size * 0.5 - nx * size * 0.3, cy - ty * size * 0.5 - ny * size * 0.3)
    base_r = (cx + tx * size * 0.5 - nx * size * 0.3, cy + ty * size * 0.5 - ny * size * 0.3)
    batch = batch_for_shader(shader, "TRIS", {"pos": [apex, base_l, base_r]}, indices=[(0, 1, 2)])
    shader.bind()
    shader.uniform_float("color", viewport_colors.HANDLE_FILL)
    batch.draw(shader)


def _draw_square_marker(shader, center: tuple[float, float]) -> None:
    cx, cy = center
    size = _VERTEX_MARKER_SIZE_PX
    verts = [
        (cx - size, cy - size),
        (cx + size, cy - size),
        (cx + size, cy + size),
        (cx - size, cy + size),
    ]
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=[(0, 1, 2), (0, 2, 3)])
    shader.bind()
    shader.uniform_float("color", viewport_colors.HANDLE_OUTLINE)
    batch.draw(shader)
