"""多角形編集用の簡易幾何ヘルパ."""

from __future__ import annotations

import math
from typing import Sequence

_EPS = 1.0e-4


def signed_polygon_area(points: Sequence[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    acc = 0.0
    for i, (x0, y0) in enumerate(points):
        x1, y1 = points[(i + 1) % len(points)]
        acc += x0 * y1 - x1 * y0
    return acc * 0.5


def is_simple_polygon(
    points: Sequence[tuple[float, float]],
    *,
    tolerance: float = _EPS,
) -> bool:
    pts = [(float(x), float(y)) for x, y in points]
    n = len(pts)
    if n < 3:
        return False

    for i in range(n):
        a = pts[i]
        b = pts[(i + 1) % n]
        if _distance(a, b) <= tolerance:
            return False

    for i in range(n):
        for j in range(i + 1, n):
            if _are_adjacent_edges(i, j, n):
                continue
            if _distance(pts[i], pts[j]) <= tolerance:
                return False

    for i in range(n):
        a1 = pts[i]
        a2 = pts[(i + 1) % n]
        for j in range(i + 1, n):
            if _are_adjacent_edges(i, j, n):
                continue
            b1 = pts[j]
            b2 = pts[(j + 1) % n]
            if _segments_intersect(a1, a2, b1, b2, tolerance):
                return False
    return True


def _are_adjacent_edges(i: int, j: int, n: int) -> bool:
    return abs(i - j) <= 1 or (i == 0 and j == n - 1)


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
    tolerance: float,
) -> bool:
    if not _bbox_overlaps(a1, a2, b1, b2, tolerance):
        return False

    o1 = _orient(a1, a2, b1)
    o2 = _orient(a1, a2, b2)
    o3 = _orient(b1, b2, a1)
    o4 = _orient(b1, b2, a2)

    if (o1 > tolerance and o2 < -tolerance or o1 < -tolerance and o2 > tolerance) and (
        o3 > tolerance and o4 < -tolerance or o3 < -tolerance and o4 > tolerance
    ):
        return True

    if abs(o1) <= tolerance and _point_on_segment(b1, a1, a2, tolerance):
        return True
    if abs(o2) <= tolerance and _point_on_segment(b2, a1, a2, tolerance):
        return True
    if abs(o3) <= tolerance and _point_on_segment(a1, b1, b2, tolerance):
        return True
    if abs(o4) <= tolerance and _point_on_segment(a2, b1, b2, tolerance):
        return True
    return False


def _bbox_overlaps(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
    tolerance: float,
) -> bool:
    return not (
        max(a1[0], a2[0]) < min(b1[0], b2[0]) - tolerance
        or max(b1[0], b2[0]) < min(a1[0], a2[0]) - tolerance
        or max(a1[1], a2[1]) < min(b1[1], b2[1]) - tolerance
        or max(b1[1], b2[1]) < min(a1[1], a2[1]) - tolerance
    )


def _point_on_segment(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    tolerance: float,
) -> bool:
    return (
        min(a[0], b[0]) - tolerance <= p[0] <= max(a[0], b[0]) + tolerance
        and min(a[1], b[1]) - tolerance <= p[1] <= max(a[1], b[1]) + tolerance
    )


def _orient(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
