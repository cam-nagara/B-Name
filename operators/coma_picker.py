"""ワールド座標 (mm) からページ/コマを逆引きするヘルパ.

overview モード中は全ページが grid 配置で描画されているため、ユーザーが
どのページのどのコマをクリックしたかを算出する。通常モード (単ページ
表示) では active ページのみを対象とする。

計画書 3. Phase 1 / 検索ヘルパ find_coma_at_world_mm の仕様 準拠。
"""

from __future__ import annotations

import math
from typing import Sequence

import bpy

from ..utils import geom, log, page_browser, page_grid, page_range

_logger = log.get_logger(__name__)
EDGE_PICK_TOLERANCE_PX = 12.0


def _point_on_segment(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    tolerance: float = 1.0e-4,
) -> bool:
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    if abs(cross) > tolerance:
        return False
    return (
        min(a[0], b[0]) - tolerance <= p[0] <= max(a[0], b[0]) + tolerance
        and min(a[1], b[1]) - tolerance <= p[1] <= max(a[1], b[1]) + tolerance
    )


def _point_in_polygon(
    p: tuple[float, float],
    poly: Sequence[tuple[float, float]],
) -> bool:
    """ray casting で点 p が多角形 poly の内側か辺上にあるかを返す."""
    x, y = p
    n = len(poly)
    if n < 3:
        return False
    for i in range(n):
        if _point_on_segment(p, poly[i], poly[(i + 1) % n]):
            return True
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi + 1.0e-30) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _hit_test_coma(entry, x_mm: float, y_mm: float) -> bool:
    """``entry`` のヒット判定。

    rect: 矩形内であれば True。
    polygon: 多角形内部または辺上であれば True。
    その他 (curve / freeform 等) は現段階では未対応 (False)。
    """
    shape = entry.shape_type
    if shape == "rect":
        return (
            entry.rect_x_mm <= x_mm <= entry.rect_x_mm + entry.rect_width_mm
            and entry.rect_y_mm <= y_mm <= entry.rect_y_mm + entry.rect_height_mm
        )
    if shape == "polygon":
        verts = entry.vertices
        if len(verts) < 3:
            return False
        poly = [(v.x_mm, v.y_mm) for v in verts]
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        if not (min(xs) <= x_mm <= max(xs) and min(ys) <= y_mm <= max(ys)):
            return False
        return _point_in_polygon((x_mm, y_mm), poly)
    return False


def _coma_polygon(entry) -> list[tuple[float, float]]:
    shape = getattr(entry, "shape_type", "")
    if shape == "rect":
        x = float(getattr(entry, "rect_x_mm", 0.0))
        y = float(getattr(entry, "rect_y_mm", 0.0))
        w = float(getattr(entry, "rect_width_mm", 0.0))
        h = float(getattr(entry, "rect_height_mm", 0.0))
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if shape == "polygon":
        return [(float(v.x_mm), float(v.y_mm)) for v in getattr(entry, "vertices", [])]
    return []


def _distance_point_to_segment(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length_sq = dx * dx + dy * dy
    if length_sq < 1.0e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq))
    cx = a[0] + dx * t
    cy = a[1] + dy * t
    return math.hypot(p[0] - cx, p[1] - cy)


def _hit_test_page(page, x_mm: float, y_mm: float) -> int | None:
    """``page`` 内で (x_mm, y_mm) にヒットするコマの index を返す.

    同座標に複数コマが重なっている場合は Z 順最大 (最前面) を返す。
    ヒットしなければ None。
    """
    best_idx: int | None = None
    best_z: int | None = None
    for j, entry in enumerate(page.comas):
        if not _hit_test_coma(entry, x_mm, y_mm):
            continue
        z = int(entry.z_order)
        if best_idx is None or z > (best_z if best_z is not None else z - 1):
            best_idx = j
            best_z = z
    return best_idx


def find_coma_at_world_mm(
    work, x_mm: float, y_mm: float
) -> tuple[int, int] | None:
    """ワールド (mm) 座標から (page_index, coma_index) を解決.

    - overview_mode が False なら active ページのみ対象 (offset=0)
    - overview_mode が True なら全ページを grid offset 付きで走査
    - 同じ位置に複数コマが重なっていても 1 ページ内の Z 順最大のみを返す
      (ページ跨ぎの Z 比較は意味がないため、最初にヒットした「ページ内
      最前面」を採用)
    """
    if work is None or len(work.pages) == 0:
        return None
    scene = bpy.context.scene
    if scene is None:
        return None

    overview = bool(getattr(scene, "bname_overview_mode", False))

    if not overview:
        idx = work.active_page_index
        if not (0 <= idx < len(work.pages)):
            return None
        page = work.pages[idx]
        if not page_range.page_in_range(page):
            return None
        cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
        gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
        cw = work.paper.canvas_width_mm
        ch = work.paper.canvas_height_mm
        start_side = getattr(work.paper, "start_side", "right")
        read_direction = getattr(work.paper, "read_direction", "left")
        ox, oy = page_grid.page_grid_offset_mm(
            idx, cols, gap, cw, ch, start_side, read_direction
        )
        add_x, add_y = page_grid.page_manual_offset_mm(page)
        ox += add_x
        oy += add_y
        hit = _hit_test_page(page, x_mm - ox, y_mm - oy)
        return (idx, hit) if hit is not None else None

    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    cw = work.paper.canvas_width_mm
    ch = work.paper.canvas_height_mm
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    for i, page in enumerate(work.pages):
        if not page_range.page_in_range(page):
            continue
        ox, oy = page_grid.page_grid_offset_mm(
            i, cols, gap, cw, ch, start_side, read_direction
        )
        add_x, add_y = page_grid.page_manual_offset_mm(page)
        ox += add_x
        oy += add_y
        local_x = x_mm - ox
        local_y = y_mm - oy
        # キャンバス矩形の外は早期スキップ (パフォーマンス最適化)
        if not (0.0 <= local_x <= cw and 0.0 <= local_y <= ch):
            continue
        hit = _hit_test_page(page, local_x, local_y)
        if hit is not None:
            return (i, hit)
    return None


def find_coma_at_event(context, event) -> tuple[int, int] | None:
    """VIEW_3D のマウスイベントから (page_index, coma_index) を解決."""
    work = None
    try:
        from ..core.work import get_work

        work = get_work(context)
    except Exception:  # noqa: BLE001
        work = None
    if work is None or not getattr(work, "loaded", False):
        return None
    coords = _event_world_mm(context, event)
    if coords is None:
        return None
    if page_browser.is_page_browser_area(context) and page_browser.fit_enabled(context.scene):
        return _find_coma_at_world_mm_page_browser(context, work, coords[0], coords[1])
    return find_coma_at_world_mm(work, coords[0], coords[1])


def find_coma_edge_at_event(context, event, tolerance_px: float = EDGE_PICK_TOLERANCE_PX):
    """VIEW_3D のマウスイベントから最寄りのコマ辺をピクセル距離で解決."""
    work = None
    try:
        from ..core.work import get_work

        work = get_work(context)
    except Exception:  # noqa: BLE001
        work = None
    if work is None or not getattr(work, "loaded", False):
        return None
    view = _event_view_context(context, event)
    if view is None:
        return None
    area, region, rv3d, mx, my = view
    scene = getattr(context, "scene", None)
    if scene is None:
        return None

    best = None
    best_dist = float(tolerance_px)
    for candidate in _iter_coma_edge_screen_candidates(context, work, area, region, rv3d):
        dist = _distance_point_to_segment(
            (mx, my),
            candidate["screen_a"],
            candidate["screen_b"],
        )
        if dist > best_dist:
            continue
        if best is not None and abs(dist - best_dist) <= 0.01 and candidate["z_order"] < best["z_order"]:
            continue
        best_dist = dist
        best = candidate
    if best is None:
        return None
    return {"page": best["page"], "coma": best["coma"], "edge": best["edge"]}


def _iter_coma_edge_screen_candidates(context, work, area, region, rv3d):
    try:
        from bpy_extras.view3d_utils import location_3d_to_region_2d
    except Exception:  # noqa: BLE001
        return
    for page_index, page in _iter_pickable_pages(context, work, area):
        ox, oy = _page_offset_for_area(context, work, area, page_index)
        for coma_index, panel in enumerate(getattr(page, "comas", [])):
            if not bool(getattr(panel, "visible", True)):
                continue
            poly = _coma_polygon(panel)
            if len(poly) < 2:
                continue
            z_order = int(getattr(panel, "z_order", 0))
            for edge_index in range(len(poly)):
                a = (poly[edge_index][0] + ox, poly[edge_index][1] + oy)
                b = (
                    poly[(edge_index + 1) % len(poly)][0] + ox,
                    poly[(edge_index + 1) % len(poly)][1] + oy,
                )
                ap = location_3d_to_region_2d(
                    region, rv3d, (geom.mm_to_m(a[0]), geom.mm_to_m(a[1]), 0.0)
                )
                bp = location_3d_to_region_2d(
                    region, rv3d, (geom.mm_to_m(b[0]), geom.mm_to_m(b[1]), 0.0)
                )
                if ap is None or bp is None:
                    continue
                yield {
                    "page": page_index,
                    "coma": coma_index,
                    "edge": edge_index,
                    "z_order": z_order,
                    "screen_a": (float(ap.x), float(ap.y)),
                    "screen_b": (float(bp.x), float(bp.y)),
                }


def find_page_at_world_mm(work, x_mm: float, y_mm: float) -> int | None:
    """ワールド (mm) 座標から page_index を解決."""
    if work is None or len(work.pages) == 0:
        return None
    scene = bpy.context.scene
    if scene is None:
        return None
    overview = bool(getattr(scene, "bname_overview_mode", False))
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    cw = float(work.paper.canvas_width_mm)
    ch = float(work.paper.canvas_height_mm)
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")

    if not overview:
        idx = int(getattr(work, "active_page_index", -1))
        if not (0 <= idx < len(work.pages)):
            return None
        if not page_range.page_in_range(work.pages[idx]):
            return None
        ox, oy = page_grid.page_grid_offset_mm(
            idx, cols, gap, cw, ch, start_side, read_direction
        )
        add_x, add_y = page_grid.page_manual_offset_mm(work.pages[idx])
        ox += add_x
        oy += add_y
        return idx if _hit_test_canvas(x_mm - ox, y_mm - oy, cw, ch) else None

    for i, _page in enumerate(work.pages):
        if not page_range.page_in_range(_page):
            continue
        ox, oy = page_grid.page_grid_offset_mm(
            i, cols, gap, cw, ch, start_side, read_direction
        )
        add_x, add_y = page_grid.page_manual_offset_mm(_page)
        ox += add_x
        oy += add_y
        if _hit_test_canvas(x_mm - ox, y_mm - oy, cw, ch):
            return i
    return None


def find_page_at_event(context, event) -> int | None:
    """VIEW_3D のマウスイベントから page_index を解決."""
    work = None
    try:
        from ..core.work import get_work

        work = get_work(context)
    except Exception:  # noqa: BLE001
        work = None
    if work is None or not getattr(work, "loaded", False):
        return None
    coords = _event_world_mm(context, event)
    if coords is None:
        return None
    if page_browser.is_page_browser_area(context) and page_browser.fit_enabled(context.scene):
        return _find_page_at_world_mm_page_browser(context, work, coords[0], coords[1])
    return find_page_at_world_mm(work, coords[0], coords[1])


def _hit_test_canvas(x_mm: float, y_mm: float, width_mm: float, height_mm: float) -> bool:
    return 0.0 <= x_mm <= width_mm and 0.0 <= y_mm <= height_mm


def _find_coma_at_world_mm_page_browser(context, work, x_mm: float, y_mm: float):
    paper = work.paper
    cw = float(paper.canvas_width_mm)
    ch = float(paper.canvas_height_mm)
    area = getattr(context, "area", None)
    for i, page in enumerate(work.pages):
        if not page_range.page_in_range(page):
            continue
        ox, oy = page_browser.page_offset_mm(work, context.scene, area, i)
        local_x = x_mm - ox
        local_y = y_mm - oy
        if not _hit_test_canvas(local_x, local_y, cw, ch):
            continue
        hit = _hit_test_page(page, local_x, local_y)
        if hit is not None:
            return i, hit
    return None


def _find_page_at_world_mm_page_browser(context, work, x_mm: float, y_mm: float) -> int | None:
    paper = work.paper
    cw = float(paper.canvas_width_mm)
    ch = float(paper.canvas_height_mm)
    area = getattr(context, "area", None)
    for i, _page in enumerate(work.pages):
        if not page_range.page_in_range(_page):
            continue
        ox, oy = page_browser.page_offset_mm(work, context.scene, area, i)
        if _hit_test_canvas(x_mm - ox, y_mm - oy, cw, ch):
            return i
    return None


def _event_world_mm(context, event) -> tuple[float, float] | None:
    try:
        from bpy_extras.view3d_utils import region_2d_to_location_3d
    except Exception:  # noqa: BLE001
        return None
    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if not (
                region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height
            ):
                continue
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if rv3d is None:
                continue
            mx = event.mouse_x - region.x
            my = event.mouse_y - region.y
            loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
            if loc is None:
                return None
            return geom.m_to_mm(loc.x), geom.m_to_mm(loc.y)
    return None


def _event_view_context(context, event):
    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if not (
                region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height
            ):
                continue
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if rv3d is None:
                continue
            return area, region, rv3d, event.mouse_x - region.x, event.mouse_y - region.y
    return None


def _iter_pickable_pages(context, work, area):
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    is_browser = page_browser.is_marked_area(area) or page_browser.page_browser_area(context) == area
    overview = bool(getattr(scene, "bname_overview_mode", False)) or is_browser
    if not overview:
        idx = int(getattr(work, "active_page_index", -1))
        if 0 <= idx < len(work.pages) and page_range.page_in_range(work.pages[idx]):
            yield idx, work.pages[idx]
        return
    for idx, page in enumerate(work.pages):
        if page_range.page_in_range(page):
            yield idx, page


def _page_offset_for_area(context, work, area, page_index: int) -> tuple[float, float]:
    scene = getattr(context, "scene", None)
    is_browser = page_browser.is_marked_area(area) or page_browser.page_browser_area(context) == area
    if is_browser and page_browser.fit_enabled(scene):
        return page_browser.page_offset_mm(work, scene, area, page_index)
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    paper = work.paper
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
