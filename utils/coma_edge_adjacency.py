"""枠線頂点ドラッグ時の隣接辺連動ヘルパ."""

from __future__ import annotations

import math
from typing import Callable

Point = tuple[float, float]
PanelPolygonFn = Callable[[object], list[Point]]
PageOffsetFn = Callable[[object, int], Point]
FindAdjacentFn = Callable[[object, int, int, int], list[tuple[int, int, int]]]
FindOverlapsFn = Callable[..., list[tuple[int, int]]]


def edge_projection_params(
    base_a: Point,
    base_b: Point,
    target_a: Point,
    target_b: Point,
) -> tuple[float, float, float, float] | None:
    """target edge 両端を base edge の接線率/法線距離として保持する."""
    ex = base_b[0] - base_a[0]
    ey = base_b[1] - base_a[1]
    length = math.hypot(ex, ey)
    if length < 1e-6:
        return None
    ux = ex / length
    uy = ey / length
    nx = -uy
    ny = ux

    def project(p: Point) -> tuple[float, float]:
        px = p[0] - base_a[0]
        py = p[1] - base_a[1]
        return (px * ux + py * uy, px * nx + py * ny)

    ta, da = project(target_a)
    tb, db = project(target_b)
    return ta / length, da, tb / length, db


def line_from_projection_params(
    base_a: Point,
    base_b: Point,
    params: tuple[float, float, float, float],
) -> tuple[Point, Point] | None:
    """保持した接線率/法線距離を、移動後 base edge の線上へ再投影する."""
    ex = base_b[0] - base_a[0]
    ey = base_b[1] - base_a[1]
    length = math.hypot(ex, ey)
    if length < 1e-6:
        return None
    ux = ex / length
    uy = ey / length
    nx = -uy
    ny = ux
    ta, da, tb, db = params
    return (
        (base_a[0] + ux * ta * length + nx * da,
         base_a[1] + uy * ta * length + ny * da),
        (base_a[0] + ux * tb * length + nx * db,
         base_a[1] + uy * tb * length + ny * db),
    )


def capture_vertex_adjacent_edge_states(
    work,
    page_idx: int,
    coma_idx: int,
    vertex_idx: int,
    poly: list[Point],
    *,
    page_offset_fn: PageOffsetFn,
    coma_polygon_fn: PanelPolygonFn,
    find_adjacent_edges_fn: FindAdjacentFn,
    find_overlapping_edges_fn: FindOverlapsFn,
    adjacency_gap_tolerance_mm: float,
    adjacency_overlap_ratio: float,
) -> list[dict]:
    """頂点ドラッグで角度が変わる 2 辺に隣接する edge 状態を記録する."""
    n = len(poly)
    if n < 3:
        return []
    pox, poy = page_offset_fn(work, page_idx)
    out: list[dict] = []
    seen: set[tuple[int, int, int, int]] = set()
    for selected_edge in ((vertex_idx - 1) % n, vertex_idx):
        sel_a = (poly[selected_edge][0] + pox, poly[selected_edge][1] + poy)
        sel_b = (
            poly[(selected_edge + 1) % n][0] + pox,
            poly[(selected_edge + 1) % n][1] + poy,
        )
        candidates = list(find_adjacent_edges_fn(work, page_idx, coma_idx, selected_edge))
        for panel_i2, ei2 in find_overlapping_edges_fn(
            work.pages[page_idx],
            coma_idx,
            selected_edge,
            max_distance_mm=adjacency_gap_tolerance_mm,
            min_overlap_ratio=adjacency_overlap_ratio,
        ):
            candidates.append((page_idx, panel_i2, ei2))
        _append_adjacent_edge_states(
            work,
            candidates,
            selected_edge,
            sel_a,
            sel_b,
            out,
            seen,
            page_offset_fn,
            coma_polygon_fn,
        )
    return out


def _append_adjacent_edge_states(
    work,
    candidates: list[tuple[int, int, int]],
    selected_edge: int,
    sel_a: Point,
    sel_b: Point,
    out: list[dict],
    seen: set[tuple[int, int, int, int]],
    page_offset_fn: PageOffsetFn,
    coma_polygon_fn: PanelPolygonFn,
) -> None:
    for pi2, panel_i2, ei2 in candidates:
        key = (selected_edge, pi2, panel_i2, ei2)
        if key in seen:
            continue
        seen.add(key)
        poly2 = coma_polygon_fn(work.pages[pi2].comas[panel_i2])
        if len(poly2) < 3:
            continue
        ox2, oy2 = page_offset_fn(work, pi2)
        adj_a = (poly2[ei2][0] + ox2, poly2[ei2][1] + oy2)
        adj_b = (
            poly2[(ei2 + 1) % len(poly2)][0] + ox2,
            poly2[(ei2 + 1) % len(poly2)][1] + oy2,
        )
        params = edge_projection_params(sel_a, sel_b, adj_a, adj_b)
        if params is None:
            continue
        out.append(
            {
                "selected_edge": selected_edge,
                "page": pi2,
                "coma": panel_i2,
                "edge": ei2,
                "poly": poly2,
                "params": params,
            }
        )
