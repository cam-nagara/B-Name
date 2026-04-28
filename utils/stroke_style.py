"""Styled stroke helpers shared by viewport overlays."""

from __future__ import annotations

import math

Point = tuple[float, float]
StrokeSegment = tuple[Point, Point, float]


def _unit(p0: Point, p1: Point) -> tuple[float, float, float]:
    dx = float(p1[0] - p0[0])
    dy = float(p1[1] - p0[1])
    length = math.hypot(dx, dy)
    if length <= 0.0:
        return 0.0, 0.0, 0.0
    return dx / length, dy / length, length


def dashed_segments(p0: Point, p1: Point, dash: float, gap: float, width: float) -> list[StrokeSegment]:
    ux, uy, length = _unit(p0, p1)
    if length <= 0.0:
        return []
    dash = max(0.001, float(dash))
    gap = max(0.0, float(gap))
    pos = 0.0
    out: list[StrokeSegment] = []
    while pos < length:
        end = min(length, pos + dash)
        start_pt = (p0[0] + ux * pos, p0[1] + uy * pos)
        end_pt = (p0[0] + ux * end, p0[1] + uy * end)
        out.append((start_pt, end_pt, width))
        pos += dash + gap
    return out


def styled_segments_for_line(p0: Point, p1: Point, width: float, style: str = "solid") -> list[StrokeSegment]:
    """Return visible stroke sub-segments for a styled line in the same units as input."""
    width = max(0.001, float(width))
    style = str(style or "solid")
    ux, uy, length = _unit(p0, p1)
    if length <= 0.0:
        return []
    if style == "dashed":
        return dashed_segments(
            p0,
            p1,
            max(width * 4.0, 2.0),
            max(width * 2.5, 1.25),
            width,
        )
    if style == "dotted":
        spacing = max(width * 2.2, 1.0)
        dot_len = max(width * 0.35, 0.08)
        pos = 0.0
        out: list[StrokeSegment] = []
        while pos <= length:
            start = max(0.0, pos - dot_len * 0.5)
            end = min(length, pos + dot_len * 0.5)
            out.append(
                (
                    (p0[0] + ux * start, p0[1] + uy * start),
                    (p0[0] + ux * end, p0[1] + uy * end),
                    width,
                )
            )
            pos += spacing
        return out
    if style == "double":
        nx = -uy
        ny = ux
        offset = max(width * 1.2, 0.5)
        inner_width = max(width * 0.45, 0.05)
        out = []
        for sign in (-0.5, 0.5):
            ox = nx * offset * sign
            oy = ny * offset * sign
            out.append(
                (
                    (p0[0] + ox, p0[1] + oy),
                    (p1[0] + ox, p1[1] + oy),
                    inner_width,
                )
            )
        return out
    return [(p0, p1, width)]


def styled_segments_for_path(
    points: list[Point],
    width: float,
    style: str = "solid",
    *,
    closed: bool = True,
) -> list[StrokeSegment]:
    if len(points) < 2:
        return []
    count = len(points) if closed else len(points) - 1
    out: list[StrokeSegment] = []
    for i in range(count):
        out.extend(styled_segments_for_line(points[i], points[(i + 1) % len(points)], width, style))
    return out
