"""フキダシのビューポートオーバーレイ描画."""

from __future__ import annotations

import math
from collections.abc import Callable

from ..utils.geom import Rect
from ..utils import balloon_shapes, object_selection, viewport_colors

DrawRectOutline = Callable[..., None]
DrawPolygonFill = Callable[[list[tuple[float, float]], tuple[float, float, float, float]], None]
DrawPolylineLoop = Callable[..., None]
EntryVisiblePredicate = Callable[[object], bool]
_BALLOON_HANDLE_SIZE_MM = 2.0


def _handle_rects(rect: Rect) -> list[Rect]:
    half = _BALLOON_HANDLE_SIZE_MM * 0.5
    points = (
        (rect.x, rect.y),
        (rect.x + rect.width * 0.5, rect.y),
        (rect.x2, rect.y),
        (rect.x, rect.y + rect.height * 0.5),
        (rect.x2, rect.y + rect.height * 0.5),
        (rect.x, rect.y2),
        (rect.x + rect.width * 0.5, rect.y2),
        (rect.x2, rect.y2),
    )
    return [
        Rect(x - half, y - half, _BALLOON_HANDLE_SIZE_MM, _BALLOON_HANDLE_SIZE_MM)
        for x, y in points
    ]


def draw_balloons(
    page,
    ox_mm: float = 0.0,
    oy_mm: float = 0.0,
    *,
    context=None,
    draw_rect_outline: DrawRectOutline,
    draw_polygon_fill: DrawPolygonFill,
    draw_polyline_loop: DrawPolylineLoop,
    is_entry_visible: EntryVisiblePredicate | None = None,
    active: bool = False,
) -> None:
    """ページ内のフキダシをオーバーレイ描画する."""
    balloons = getattr(page, "balloons", None)
    if balloons is None:
        return
    active_idx = getattr(page, "active_balloon_index", -1)
    for i, entry in enumerate(balloons):
        if is_entry_visible is not None and not is_entry_visible(entry):
            continue
        if entry.shape == "none":
            continue
        rect = Rect(
            entry.x_mm + ox_mm,
            entry.y_mm + oy_mm,
            entry.width_mm,
            entry.height_mm,
        )
        op = float(getattr(entry, "opacity", 1.0))
        if op <= 0.0:
            continue
        fill = (
            float(entry.fill_color[0]),
            float(entry.fill_color[1]),
            float(entry.fill_color[2]),
            float(entry.fill_color[3]) * op,
        )
        line = (
            float(entry.line_color[0]),
            float(entry.line_color[1]),
            float(entry.line_color[2]),
            float(entry.line_color[3]) * op,
        )
        line_width = max(1.0, float(entry.line_width_mm) * 2.0)

        try:
            outline = _balloon_outline_mm(entry, rect)
        except Exception:  # noqa: BLE001
            outline = _outline_rect(rect)
        outline = _apply_balloon_transforms(
            outline,
            rect,
            bool(getattr(entry, "flip_h", False)),
            bool(getattr(entry, "flip_v", False)),
            float(getattr(entry, "rotation_deg", 0.0)),
        )

        draw_polygon_fill(outline, fill)
        draw_polyline_loop(outline, line, line_width=line_width)

        for tail in getattr(entry, "tails", []):
            _draw_balloon_tail(
                rect,
                tail,
                fill,
                line,
                line_width,
                draw_polygon_fill=draw_polygon_fill,
                draw_polyline_loop=draw_polyline_loop,
            )

        selected = (
            (active and (i == active_idx or bool(getattr(entry, "selected", False))))
            or object_selection.is_balloon_selected(context, page, entry)
        )
        if selected:
            draw_rect_outline(rect.inset(-1.0), viewport_colors.SELECTION, width_mm=0.50)
            for handle in _handle_rects(rect):
                draw_polygon_fill(
                    [(handle.x, handle.y), (handle.x2, handle.y), (handle.x2, handle.y2), (handle.x, handle.y2)],
                    viewport_colors.HANDLE_FILL,
                )
                draw_rect_outline(handle, viewport_colors.HANDLE_OUTLINE, width_mm=0.25)


def _outline_rect(rect: Rect) -> list[tuple[float, float]]:
    return [(rect.x, rect.y), (rect.x2, rect.y), (rect.x2, rect.y2), (rect.x, rect.y2)]


def _outline_rounded_rect(rect: Rect, radius_mm: float, segments: int = 8
                          ) -> list[tuple[float, float]]:
    r = max(0.0, min(float(radius_mm), rect.width / 2.0, rect.height / 2.0))
    if r <= 0.0:
        return _outline_rect(rect)
    pts: list[tuple[float, float]] = []
    corners = (
        (rect.x2 - r, rect.y2 - r, 0.0),
        (rect.x + r, rect.y2 - r, math.pi * 0.5),
        (rect.x + r, rect.y + r, math.pi),
        (rect.x2 - r, rect.y + r, math.pi * 1.5),
    )
    for cx, cy, a0 in corners:
        for s in range(segments + 1):
            t = a0 + (math.pi * 0.5) * (s / segments)
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return pts


def _outline_ellipse(rect: Rect, segments: int = 64) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = rect.width * 0.5
    ry = rect.height * 0.5
    return [
        (cx + rx * math.cos(2 * math.pi * i / segments),
         cy + ry * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ]


def _outline_cloud(rect: Rect, wave_count: int, amplitude_mm: float,
                   segments_per_wave: int = 6) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = max(1.0, rect.width * 0.5 - amplitude_mm)
    ry = max(1.0, rect.height * 0.5 - amplitude_mm)
    n = max(8, int(wave_count) * max(1, int(segments_per_wave)))
    pts: list[tuple[float, float]] = []
    for i in range(n):
        t = 2 * math.pi * i / n
        bump = amplitude_mm * (0.5 + 0.5 * math.cos(wave_count * t))
        r_mod = 1.0 + bump / max(1.0, min(rx, ry))
        pts.append((cx + rx * math.cos(t) * r_mod, cy + ry * math.sin(t) * r_mod))
    return pts


def _outline_spike(rect: Rect, spike_count: int, depth_mm: float,
                   smooth: bool = False) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = max(1.0, rect.width * 0.5)
    ry = max(1.0, rect.height * 0.5)
    n = max(6, int(spike_count) * 2)
    pts: list[tuple[float, float]] = []
    for i in range(n):
        t = 2 * math.pi * i / n
        is_tip = (i % 2) == 0
        r_factor = 1.0 if is_tip else max(0.05, 1.0 - depth_mm / max(rx, ry))
        pts.append((cx + rx * math.cos(t) * r_factor, cy + ry * math.sin(t) * r_factor))
    if smooth and len(pts) >= 3:
        sm: list[tuple[float, float]] = []
        for i in range(len(pts)):
            p = pts[i]
            pp = pts[(i - 1) % len(pts)]
            pn = pts[(i + 1) % len(pts)]
            sm.append(((pp[0] + 2 * p[0] + pn[0]) * 0.25,
                       (pp[1] + 2 * p[1] + pn[1]) * 0.25))
        pts = sm
    return pts


def _outline_polygon_pct(rect: Rect, pct_pts: list[tuple[float, float]]
                         ) -> list[tuple[float, float]]:
    return [
        (rect.x + (px / 100.0) * rect.width,
         rect.y + ((100.0 - py) / 100.0) * rect.height)
        for px, py in pct_pts
    ]


def _outline_pill(rect: Rect, segments: int = 16) -> list[tuple[float, float]]:
    r = min(rect.width, rect.height) * 0.5
    if r <= 0:
        return _outline_rect(rect)
    cy = (rect.y + rect.y2) * 0.5
    cx_left = rect.x + r
    cx_right = rect.x2 - r
    pts: list[tuple[float, float]] = []
    for s in range(segments + 1):
        t = -math.pi * 0.5 + math.pi * (s / segments)
        pts.append((cx_right + r * math.cos(t), cy + r * math.sin(t)))
    for s in range(segments + 1):
        t = math.pi * 0.5 + math.pi * (s / segments)
        pts.append((cx_left + r * math.cos(t), cy + r * math.sin(t)))
    return pts


def _outline_diamond(rect: Rect) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    return [(cx, rect.y2), (rect.x2, cy), (cx, rect.y), (rect.x, cy)]


def _outline_hexagon(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(rect, [
        (25, 0), (75, 0), (100, 50), (75, 100), (25, 100), (0, 50),
    ])


def _outline_octagon(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(rect, [
        (12, 0), (88, 0), (100, 12), (100, 88),
        (88, 100), (12, 100), (0, 88), (0, 12),
    ])


def _outline_star(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(rect, [
        (50, 0), (61, 35), (98, 35), (68, 57), (79, 91),
        (50, 70), (21, 91), (32, 57), (2, 35), (39, 35),
    ])


def _outline_fluffy(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(rect, [
        (50, 3), (70, 8), (88, 16), (96, 30), (92, 50), (96, 70),
        (88, 84), (70, 92), (50, 97), (30, 92), (12, 84), (4, 70),
        (8, 50), (4, 30), (12, 16), (30, 8),
    ])


def _balloon_outline_mm(entry, rect: Rect) -> list[tuple[float, float]]:
    return balloon_shapes.outline_for_entry(entry, rect)


def _apply_balloon_transforms(pts: list[tuple[float, float]], rect: Rect,
                              flip_h: bool, flip_v: bool, rotation_deg: float
                              ) -> list[tuple[float, float]]:
    if not (flip_h or flip_v or abs(rotation_deg) > 1e-6):
        return pts
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    sx = -1.0 if flip_h else 1.0
    sy = -1.0 if flip_v else 1.0
    cos_r = math.cos(math.radians(rotation_deg))
    sin_r = math.sin(math.radians(rotation_deg))
    out: list[tuple[float, float]] = []
    for x, y in pts:
        dx, dy = (x - cx) * sx, (y - cy) * sy
        rx = dx * cos_r - dy * sin_r
        ry = dx * sin_r + dy * cos_r
        out.append((cx + rx, cy + ry))
    return out


def _draw_balloon_tail(
    rect: Rect,
    tail,
    fill_color,
    line_color,
    line_width: float,
    *,
    draw_polygon_fill: DrawPolygonFill,
    draw_polyline_loop: DrawPolylineLoop,
) -> None:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = rect.width * 0.5
    ry = rect.height * 0.5
    angle = math.radians(float(tail.direction_deg))
    dx, dy = math.cos(angle), math.sin(angle)
    denom = math.hypot(dx / max(rx, 0.01), dy / max(ry, 0.01))
    base_x = cx + (dx / denom) if denom > 0 else cx
    base_y = cy + (dy / denom) if denom > 0 else cy
    tip_x = base_x + dx * tail.length_mm
    tip_y = base_y + dy * tail.length_mm
    nx, ny = -dy, dx
    rw = float(tail.root_width_mm) * 0.5
    tw = float(tail.tip_width_mm) * 0.5

    if tail.type == "sticky":
        pts = [
            (base_x + nx * rw, base_y + ny * rw),
            (tip_x + nx * tw if tw > 0 else tip_x, tip_y + ny * tw if tw > 0 else tip_y),
            (tip_x - nx * tw if tw > 0 else tip_x, tip_y - ny * tw if tw > 0 else tip_y),
            (base_x - nx * rw, base_y - ny * rw),
        ]
    elif tail.type == "curve":
        bend = float(tail.curve_bend) * tail.length_mm * 0.4
        mid_x = (base_x + tip_x) * 0.5 + nx * bend
        mid_y = (base_y + tip_y) * 0.5 + ny * bend
        pts = [
            (base_x + nx * rw, base_y + ny * rw),
            (mid_x, mid_y),
            (tip_x, tip_y),
            (mid_x, mid_y),
            (base_x - nx * rw, base_y - ny * rw),
        ]
    else:
        pts = [
            (base_x + nx * rw, base_y + ny * rw),
            (tip_x, tip_y),
            (base_x - nx * rw, base_y - ny * rw),
        ]
    draw_polygon_fill(pts, fill_color)
    draw_polyline_loop(pts, line_color, line_width=line_width)
