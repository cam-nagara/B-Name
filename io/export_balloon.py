"""フキダシのラスタ書き出しヘルパ."""

from __future__ import annotations

import math
from typing import Sequence

from ..utils.geom import Rect, mm_to_px


def _ep():
    from . import export_pipeline

    return export_pipeline


def _outline_rect(rect: Rect) -> list[tuple[float, float]]:
    return [(rect.x, rect.y), (rect.x2, rect.y), (rect.x2, rect.y2), (rect.x, rect.y2)]


def _outline_rounded_rect(rect: Rect, radius_mm: float, segments: int = 8) -> list[tuple[float, float]]:
    radius = max(0.0, min(float(radius_mm), rect.width * 0.5, rect.height * 0.5))
    if radius <= 0.0:
        return _outline_rect(rect)
    corners = (
        (rect.x2 - radius, rect.y2 - radius, 0.0),
        (rect.x + radius, rect.y2 - radius, math.pi * 0.5),
        (rect.x + radius, rect.y + radius, math.pi),
        (rect.x2 - radius, rect.y + radius, math.pi * 1.5),
    )
    pts: list[tuple[float, float]] = []
    for cx, cy, start in corners:
        for step in range(segments + 1):
            angle = start + (math.pi * 0.5) * (step / segments)
            pts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
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
    total = max(8, int(wave_count) * max(1, int(segments_per_wave)))
    pts: list[tuple[float, float]] = []
    for i in range(total):
        angle = 2 * math.pi * i / total
        bump = amplitude_mm * (0.5 + 0.5 * math.cos(wave_count * angle))
        radius_factor = 1.0 + bump / max(1.0, min(rx, ry))
        pts.append((cx + rx * math.cos(angle) * radius_factor, cy + ry * math.sin(angle) * radius_factor))
    return pts


def _outline_spike(rect: Rect, spike_count: int, depth_mm: float, *, smooth: bool) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = max(1.0, rect.width * 0.5)
    ry = max(1.0, rect.height * 0.5)
    total = max(6, int(spike_count) * 2)
    pts: list[tuple[float, float]] = []
    for i in range(total):
        angle = 2 * math.pi * i / total
        factor = 1.0 if i % 2 == 0 else max(0.05, 1.0 - depth_mm / max(rx, ry))
        pts.append((cx + rx * math.cos(angle) * factor, cy + ry * math.sin(angle) * factor))
    if smooth and len(pts) >= 3:
        smoothed = []
        for i in range(len(pts)):
            prev_pt = pts[(i - 1) % len(pts)]
            cur_pt = pts[i]
            next_pt = pts[(i + 1) % len(pts)]
            smoothed.append(((prev_pt[0] + 2 * cur_pt[0] + next_pt[0]) * 0.25,
                             (prev_pt[1] + 2 * cur_pt[1] + next_pt[1]) * 0.25))
        pts = smoothed
    return pts


def _outline_polygon_pct(rect: Rect, pct_pts: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    return [
        (rect.x + rect.width * (px / 100.0),
         rect.y + rect.height * ((100.0 - py) / 100.0))
        for px, py in pct_pts
    ]


def _outline_pill(rect: Rect, segments: int = 16) -> list[tuple[float, float]]:
    radius = min(rect.width, rect.height) * 0.5
    if radius <= 0.0:
        return _outline_rect(rect)
    cx_left = rect.x + radius
    cx_right = rect.x2 - radius
    cy = (rect.y + rect.y2) * 0.5
    pts: list[tuple[float, float]] = []
    for step in range(segments + 1):
        angle = -math.pi * 0.5 + math.pi * (step / segments)
        pts.append((cx_right + radius * math.cos(angle), cy + radius * math.sin(angle)))
    for step in range(segments + 1):
        angle = math.pi * 0.5 + math.pi * (step / segments)
        pts.append((cx_left + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pts


def _outline_diamond(rect: Rect) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    return [(cx, rect.y2), (rect.x2, cy), (cx, rect.y), (rect.x, cy)]


def _outline_hexagon(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(rect, [(25, 0), (75, 0), (100, 50), (75, 100), (25, 100), (0, 50)])


def _outline_octagon(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(rect, [(12, 0), (88, 0), (100, 12), (100, 88), (88, 100), (12, 100), (0, 88), (0, 12)])


def _outline_star(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(
        rect,
        [(50, 0), (61, 35), (98, 35), (68, 57), (79, 91),
         (50, 70), (21, 91), (32, 57), (2, 35), (39, 35)],
    )


def _outline_fluffy(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(
        rect,
        [(50, 3), (70, 8), (88, 16), (96, 30), (92, 50), (96, 70),
         (88, 84), (70, 92), (50, 97), (30, 92), (12, 84), (4, 70),
         (8, 50), (4, 30), (12, 16), (30, 8)],
    )


def _balloon_outline_mm(entry, rect: Rect) -> list[tuple[float, float]]:
    shape = getattr(entry, "shape", "rect")
    sp = entry.shape_params
    if shape == "rect":
        if (
            getattr(entry, "rounded_corner_enabled", False)
            and float(getattr(entry, "rounded_corner_radius_mm", 0.0)) > 0.0
        ):
            return _outline_rounded_rect(rect, float(entry.rounded_corner_radius_mm))
        return _outline_rect(rect)
    if shape == "ellipse":
        return _outline_ellipse(rect)
    if shape == "pill":
        return _outline_pill(rect)
    if shape == "diamond":
        return _outline_diamond(rect)
    if shape == "hexagon":
        return _outline_hexagon(rect)
    if shape == "octagon":
        return _outline_octagon(rect)
    if shape == "star":
        return _outline_star(rect)
    if shape == "fluffy":
        return _outline_fluffy(rect)
    if shape == "cloud":
        return _outline_cloud(rect, int(sp.cloud_wave_count), float(sp.cloud_wave_amplitude_mm))
    if shape == "spike_straight":
        return _outline_spike(rect, int(sp.spike_count), float(sp.spike_depth_mm), smooth=False)
    if shape == "spike_curve":
        return _outline_spike(rect, int(sp.spike_count), float(sp.spike_depth_mm), smooth=True)
    return _outline_rect(rect)


def _apply_balloon_transforms(
    pts: Sequence[tuple[float, float]],
    rect: Rect,
    flip_h: bool,
    flip_v: bool,
    rotation_deg: float,
) -> list[tuple[float, float]]:
    if not (flip_h or flip_v or abs(rotation_deg) > 1e-6):
        return list(pts)
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    sx = -1.0 if flip_h else 1.0
    sy = -1.0 if flip_v else 1.0
    cos_r = math.cos(math.radians(rotation_deg))
    sin_r = math.sin(math.radians(rotation_deg))
    out = []
    for x, y in pts:
        dx = (x - cx) * sx
        dy = (y - cy) * sy
        rx = dx * cos_r - dy * sin_r
        ry = dx * sin_r + dy * cos_r
        out.append((cx + rx, cy + ry))
    return out


def _balloon_tail_polygon(rect: Rect, tail) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = rect.width * 0.5
    ry = rect.height * 0.5
    angle = math.radians(float(getattr(tail, "direction_deg", 270.0)))
    dx = math.cos(angle)
    dy = math.sin(angle)
    denom = math.hypot(dx / max(rx, 0.01), dy / max(ry, 0.01))
    base_x = cx + (dx / denom) if denom > 0.0 else cx
    base_y = cy + (dy / denom) if denom > 0.0 else cy
    tip_x = base_x + dx * float(getattr(tail, "length_mm", 0.0))
    tip_y = base_y + dy * float(getattr(tail, "length_mm", 0.0))
    nx = -dy
    ny = dx
    root_half = float(getattr(tail, "root_width_mm", 0.0)) * 0.5
    tip_half = float(getattr(tail, "tip_width_mm", 0.0)) * 0.5
    tail_type = getattr(tail, "type", "straight")
    if tail_type == "sticky":
        return [
            (base_x + nx * root_half, base_y + ny * root_half),
            (tip_x + nx * tip_half if tip_half > 0.0 else tip_x, tip_y + ny * tip_half if tip_half > 0.0 else tip_y),
            (tip_x - nx * tip_half if tip_half > 0.0 else tip_x, tip_y - ny * tip_half if tip_half > 0.0 else tip_y),
            (base_x - nx * root_half, base_y - ny * root_half),
        ]
    if tail_type == "curve":
        bend = float(getattr(tail, "curve_bend", 0.0)) * float(getattr(tail, "length_mm", 0.0)) * 0.4
        mid_x = (base_x + tip_x) * 0.5 + nx * bend
        mid_y = (base_y + tip_y) * 0.5 + ny * bend
        return [
            (base_x + nx * root_half, base_y + ny * root_half),
            (mid_x, mid_y),
            (tip_x, tip_y),
            (mid_x, mid_y),
            (base_x - nx * root_half, base_y - ny * root_half),
        ]
    return [
        (base_x + nx * root_half, base_y + ny * root_half),
        (tip_x, tip_y),
        (base_x - nx * root_half, base_y - ny * root_half),
    ]


def render_balloon_layer(entry, canvas_height_px: int, dpi: int):
    if getattr(entry, "shape", "rect") == "none":
        return None
    ep = _ep()
    rect = Rect(float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm))
    outline = _apply_balloon_transforms(
        _balloon_outline_mm(entry, rect),
        rect,
        bool(getattr(entry, "flip_h", False)),
        bool(getattr(entry, "flip_v", False)),
        float(getattr(entry, "rotation_deg", 0.0)),
    )
    all_pts = list(outline)
    for tail in entry.tails:
        all_pts.extend(_balloon_tail_polygon(rect, tail))
    bbox = ep._points_bbox(all_pts)
    if bbox is None:
        return None
    pad_mm = max(2.0, float(getattr(entry, "line_width_mm", 0.6)) * 4.0)
    canvas = ep._canvas_for_bbox(bbox, canvas_height_px, dpi, pad_mm=pad_mm)
    if canvas is None:
        return None
    fill_color = ep._rgb255(entry.fill_color, alpha=float(getattr(entry, "opacity", 1.0)))
    line_color = ep._rgb255(entry.line_color, alpha=float(getattr(entry, "opacity", 1.0)))
    line_width_px = max(1, int(round(mm_to_px(float(getattr(entry, "line_width_mm", 0.6)), dpi))))
    draw = ep.ImageDraw.Draw(canvas.image)
    outline_px = canvas.points_px(outline)
    if len(outline_px) >= 3:
        draw.polygon(outline_px, fill=fill_color)
    ep._draw_styled_loop(draw, outline_px, line_color, line_width_px, getattr(entry, "line_style", "solid"))
    for tail in entry.tails:
        tail_px = canvas.points_px(_balloon_tail_polygon(rect, tail))
        if len(tail_px) >= 3:
            draw.polygon(tail_px, fill=fill_color)
            ep._draw_styled_loop(draw, tail_px, line_color, line_width_px, getattr(entry, "line_style", "solid"))
    return ep.ExportLayer(
        str(getattr(entry, "id", "") or "balloon"),
        canvas.image,
        canvas.left,
        canvas.top,
        blend_mode=getattr(entry, "blend_mode", "normal"),
        group_path=(
            "balloons",
            str(getattr(entry, "merge_group_id", "") or ""),
        )
        if getattr(entry, "merge_group_id", "")
        else ("balloons",),
    )
