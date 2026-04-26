"""コマ枠線の輪郭/角処理計算ヘルパ."""

from __future__ import annotations

import math
from typing import Sequence

_EPS = 1.0e-6
_MITER_LIMIT = 4.0


def polygon_area(points: Sequence[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    acc = 0.0
    for i, (x0, y0) in enumerate(points):
        x1, y1 = points[(i + 1) % len(points)]
        acc += x0 * y1 - x1 * y0
    return acc * 0.5


def is_convex_polygon(points: Sequence[tuple[float, float]]) -> bool:
    if len(points) < 3:
        return False
    orient = polygon_area(points)
    if abs(orient) <= _EPS:
        return False
    sign = 1.0 if orient > 0.0 else -1.0
    found = False
    for i in range(len(points)):
        ax, ay = points[i - 1]
        bx, by = points[i]
        cx, cy = points[(i + 1) % len(points)]
        e1x, e1y = bx - ax, by - ay
        e2x, e2y = cx - bx, cy - by
        cross = e1x * e2y - e1y * e2x
        if abs(cross) <= _EPS:
            continue
        found = True
        if cross * sign < 0.0:
            return False
    return found


def styled_closed_path_mm(
    points: Sequence[tuple[float, float]],
    corner_type: str = "square",
    radius_mm: float = 0.0,
    *,
    corner_segments: int = 8,
) -> list[tuple[float, float]]:
    """角処理を反映した閉パスを返す.

    - ``square``: 元頂点そのまま
    - ``bevel``: 角を面取り
    - ``rounded``: 凸角だけを丸める
    """
    pts = _dedupe_closed(points)
    if len(pts) < 3 or corner_type == "square" or radius_mm <= _EPS:
        return pts

    orient = polygon_area(pts)
    if abs(orient) <= _EPS:
        return pts
    orient_sign = 1.0 if orient > 0.0 else -1.0

    out: list[tuple[float, float]] = []
    for i, b in enumerate(pts):
        a = pts[i - 1]
        c = pts[(i + 1) % len(pts)]
        e1 = (b[0] - a[0], b[1] - a[1])
        e2 = (c[0] - b[0], c[1] - b[1])
        len1 = _length(e1)
        len2 = _length(e2)
        if len1 <= _EPS or len2 <= _EPS:
            _append_unique(out, b)
            continue
        cross = e1[0] * e2[1] - e1[1] * e2[0]
        if cross * orient_sign <= _EPS:
            # 凹角は現状そのまま残す。凸角だけ丸角/面取りする。
            _append_unique(out, b)
            continue

        u1 = (-e1[0] / len1, -e1[1] / len1)  # b -> a
        u2 = (e2[0] / len2, e2[1] / len2)    # b -> c
        dot = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        theta = math.acos(dot)
        tan_half = math.tan(theta * 0.5)
        if theta <= _EPS or abs(tan_half) <= _EPS:
            _append_unique(out, b)
            continue

        max_t = max(0.0, min(len1, len2) * 0.5 - _EPS)
        if max_t <= _EPS:
            _append_unique(out, b)
            continue
        effective_radius = min(float(radius_mm), max_t * tan_half)
        if effective_radius <= _EPS:
            _append_unique(out, b)
            continue
        tangent = effective_radius / tan_half
        p1 = (b[0] + u1[0] * tangent, b[1] + u1[1] * tangent)
        p2 = (b[0] + u2[0] * tangent, b[1] + u2[1] * tangent)

        if corner_type == "bevel":
            _append_unique(out, p1)
            _append_unique(out, p2)
            continue

        bis = _normalize((u1[0] + u2[0], u1[1] + u2[1]))
        if bis is None:
            _append_unique(out, p1)
            _append_unique(out, p2)
            continue
        sin_half = math.sin(theta * 0.5)
        if abs(sin_half) <= _EPS:
            _append_unique(out, p1)
            _append_unique(out, p2)
            continue
        center_dist = effective_radius / sin_half
        center = (b[0] + bis[0] * center_dist, b[1] + bis[1] * center_dist)
        start = math.atan2(p1[1] - center[1], p1[0] - center[0])
        end = math.atan2(p2[1] - center[1], p2[0] - center[0])
        _append_unique(out, p1)
        if orient_sign > 0.0:
            while end <= start:
                end += math.tau
        else:
            while end >= start:
                end -= math.tau
        steps = max(2, int(math.ceil(corner_segments * theta / (math.pi * 0.5))))
        for step in range(1, steps):
            t = step / steps
            angle = start + (end - start) * t
            _append_unique(
                out,
                (
                    center[0] + effective_radius * math.cos(angle),
                    center[1] + effective_radius * math.sin(angle),
                ),
            )
        _append_unique(out, p2)
    return out


def stroke_loops_mm(
    centerline_points: Sequence[tuple[float, float]],
    width_mm: float,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]] | None:
    """閉パスの太線を outer/inner の 2 ループに変換."""
    pts = _dedupe_closed(centerline_points)
    if len(pts) < 3 or float(width_mm) <= _EPS:
        return None
    if not is_convex_polygon(pts):
        return None

    orient = polygon_area(pts)
    if abs(orient) <= _EPS:
        return None
    ccw = orient > 0.0
    half = float(width_mm) * 0.5
    outer: list[tuple[float, float]] = []
    inner: list[tuple[float, float]] = []

    for i, curr in enumerate(pts):
        prev = pts[i - 1]
        nxt = pts[(i + 1) % len(pts)]
        d_prev = _normalize((curr[0] - prev[0], curr[1] - prev[1]))
        d_next = _normalize((nxt[0] - curr[0], nxt[1] - curr[1]))
        if d_prev is None or d_next is None:
            return None
        left_prev = (-d_prev[1], d_prev[0])
        left_next = (-d_next[1], d_next[0])
        if ccw:
            inner_prev, inner_next = left_prev, left_next
            outer_prev, outer_next = (-left_prev[0], -left_prev[1]), (-left_next[0], -left_next[1])
        else:
            outer_prev, outer_next = left_prev, left_next
            inner_prev, inner_next = (-left_prev[0], -left_prev[1]), (-left_next[0], -left_next[1])

        outer.append(_offset_join(curr, d_prev, d_next, outer_prev, outer_next, half))
        inner.append(_offset_join(curr, d_prev, d_next, inner_prev, inner_next, half))
    return outer, inner


def _offset_join(
    curr: tuple[float, float],
    d_prev: tuple[float, float],
    d_next: tuple[float, float],
    n_prev: tuple[float, float],
    n_next: tuple[float, float],
    offset: float,
) -> tuple[float, float]:
    p1 = (curr[0] + n_prev[0] * offset, curr[1] + n_prev[1] * offset)
    p2 = (curr[0] + n_next[0] * offset, curr[1] + n_next[1] * offset)
    hit = _line_intersection(p1, d_prev, p2, d_next)
    if hit is not None:
        dx = hit[0] - curr[0]
        dy = hit[1] - curr[1]
        if math.hypot(dx, dy) <= max(offset * _MITER_LIMIT, offset + _EPS):
            return hit

    bis = _normalize((n_prev[0] + n_next[0], n_prev[1] + n_next[1]))
    if bis is None:
        return p1
    denom = bis[0] * n_prev[0] + bis[1] * n_prev[1]
    if abs(denom) <= _EPS:
        return p1
    scale = min(offset / abs(denom), offset * _MITER_LIMIT)
    return (curr[0] + bis[0] * scale, curr[1] + bis[1] * scale)


def _line_intersection(
    p1: tuple[float, float],
    d1: tuple[float, float],
    p2: tuple[float, float],
    d2: tuple[float, float],
) -> tuple[float, float] | None:
    det = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(det) <= _EPS:
        return None
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    t = (dx * d2[1] - dy * d2[0]) / det
    return (p1[0] + d1[0] * t, p1[1] + d1[1] * t)


def _dedupe_closed(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for pt in points:
        _append_unique(out, (float(pt[0]), float(pt[1])))
    if len(out) >= 2:
        x0, y0 = out[0]
        x1, y1 = out[-1]
        if math.hypot(x1 - x0, y1 - y0) <= _EPS:
            out.pop()
    return out


def _append_unique(points: list[tuple[float, float]], pt: tuple[float, float]) -> None:
    if points:
        dx = points[-1][0] - pt[0]
        dy = points[-1][1] - pt[1]
        if math.hypot(dx, dy) <= _EPS:
            return
    points.append(pt)


def _length(vec: tuple[float, float]) -> float:
    return math.hypot(vec[0], vec[1])


def _normalize(vec: tuple[float, float]) -> tuple[float, float] | None:
    length = _length(vec)
    if length <= _EPS:
        return None
    return (vec[0] / length, vec[1] / length)
