"""効果線ストローク生成ロジック (計画書 3.1.6).

BNameEffectLineParams を受け取り、放射状 / 流線の頂点列を算出する。
Grease Pencil v3 への書き込みは utils/gpencil.py を経由する。

このモジュールは純粋計算 (点列生成) と GP 統合を担う。Operator は
operators/effect_line_op.py から呼ぶ。
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from ..utils import balloon_shapes, log
from ..utils.geom import Rect, mm_to_m

_logger = log.get_logger(__name__)


@dataclass(frozen=True)
class EffectLineStroke:
    points_xyz: list[tuple[float, float, float]]
    radius: float  # m 単位
    cyclic: bool = False
    radii: list[float] | None = None
    role: str = "line"
    curve_type: str = "POLY"
    bezier_smooth: bool = False


def _jitter(base: float, amount: float, rng: random.Random) -> float:
    if amount <= 0.0:
        return base
    delta = base * amount * (rng.random() * 2.0 - 1.0)
    return base + delta


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _max_line_count(params) -> int:
    return max(1, int(getattr(params, "max_line_count", 300)))


def _ellipse_perimeter_mm(rx: float, ry: float) -> float:
    a = max(0.001, abs(float(rx)))
    b = max(0.001, abs(float(ry)))
    h = ((a - b) ** 2) / ((a + b) ** 2)
    return math.pi * (a + b) * (1.0 + (3.0 * h) / (10.0 + math.sqrt(4.0 - 3.0 * h)))


def _poly_perimeter_mm(points: Sequence[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i, point in enumerate(points):
        nxt = points[(i + 1) % len(points)]
        total += math.hypot(nxt[0] - point[0], nxt[1] - point[1])
    return total


def _scaled_rect(cx: float, cy: float, rx: float, ry: float, scale: float) -> Rect:
    sx = max(0.001, float(rx) * float(scale))
    sy = max(0.001, float(ry) * float(scale))
    return Rect(cx - sx, cy - sy, sx * 2.0, sy * 2.0)


def _rotate_points(
    points: Sequence[tuple[float, float]],
    center: tuple[float, float],
    angle_deg: float,
) -> list[tuple[float, float]]:
    angle = math.radians(float(angle_deg))
    if abs(angle) < 1.0e-9:
        return [(float(x), float(y)) for x, y in points]
    cx, cy = center
    ca = math.cos(angle)
    sa = math.sin(angle)
    out: list[tuple[float, float]] = []
    for x, y in points:
        dx = float(x) - cx
        dy = float(y) - cy
        out.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
    return out


def _shape_outline(
    params,
    prefix: str,
    rect: Rect,
    center_xy_mm: tuple[float, float],
) -> list[tuple[float, float]]:
    shape = getattr(params, f"{prefix}_shape", getattr(params, "base_shape", "rect"))
    if shape == "polygon":
        shape = "octagon"
    points = balloon_shapes.outline_for_shape(
        shape,
        rect,
        rounded_corner_enabled=bool(getattr(params, f"{prefix}_rounded_corner_enabled", False)),
        rounded_corner_radius_mm=float(getattr(params, f"{prefix}_rounded_corner_radius_mm", 0.0)),
        cloud_bump_width_mm=float(getattr(params, f"{prefix}_cloud_bump_width_mm", 10.0)),
        cloud_bump_height_mm=float(getattr(params, f"{prefix}_cloud_bump_height_mm", 4.0)),
        cloud_offset=float(getattr(params, f"{prefix}_cloud_offset_percent", 50.0)) / 100.0,
        cloud_sub_width_ratio=float(getattr(params, f"{prefix}_cloud_sub_width_ratio", 0.0)),
        cloud_sub_height_ratio=float(getattr(params, f"{prefix}_cloud_sub_height_ratio", 0.0)),
    )
    return _rotate_points(points, center_xy_mm, getattr(params, "rotation_deg", 0.0))


def _shape_guide_uses_smooth_bezier(params, prefix: str, *, frame_outline: bool = False) -> bool:
    if frame_outline:
        return False
    shape = str(getattr(params, f"{prefix}_shape", getattr(params, "base_shape", "rect")) or "rect")
    if shape in {"polygon", "octagon", "diamond", "hexagon", "star", "thorn", "spike_straight"}:
        return False
    if shape == "rect":
        return bool(getattr(params, f"{prefix}_rounded_corner_enabled", False)) and (
            float(getattr(params, f"{prefix}_rounded_corner_radius_mm", 0.0)) > 0.0
        )
    return shape in {"ellipse", "cloud", "fluffy", "thorn-curve", "spike_curve", "pill"}


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _ray_outline_point(
    center_xy_mm: tuple[float, float],
    outline: Sequence[tuple[float, float]],
    angle: float,
    *,
    extend_mm: float = 0.0,
) -> tuple[float, float] | None:
    if len(outline) < 2:
        return None
    cx, cy = center_xy_mm
    dx = math.cos(angle)
    dy = math.sin(angle)
    best_t: float | None = None
    for i, a in enumerate(outline):
        b = outline[(i + 1) % len(outline)]
        sx = b[0] - a[0]
        sy = b[1] - a[1]
        denom = _cross(dx, dy, sx, sy)
        if abs(denom) < 1.0e-9:
            continue
        qx = a[0] - cx
        qy = a[1] - cy
        t = _cross(qx, qy, sx, sy) / denom
        u = _cross(qx, qy, dx, dy) / denom
        if t >= -1.0e-6 and -1.0e-6 <= u <= 1.0 + 1.0e-6:
            if best_t is None or t < best_t:
                best_t = t
    if best_t is None:
        return None
    distance = max(0.0, best_t + float(extend_mm))
    return cx + dx * distance, cy + dy * distance


def _point_on_outline_or_ellipse(
    center_xy_mm: tuple[float, float],
    outline: Sequence[tuple[float, float]],
    rx: float,
    ry: float,
    angle: float,
    *,
    extend_mm: float = 0.0,
) -> tuple[float, float]:
    point = _ray_outline_point(center_xy_mm, outline, angle, extend_mm=extend_mm)
    if point is not None:
        return point
    cx, cy = center_xy_mm
    return (
        cx + math.cos(angle) * (float(rx) + float(extend_mm)),
        cy + math.sin(angle) * (float(ry) + float(extend_mm)),
    )


def _actual_outline_by_rays(
    center_xy_mm: tuple[float, float],
    outline: Sequence[tuple[float, float]],
    *,
    extend_mm: float = 0.0,
    samples: int = 128,
) -> list[tuple[float, float]]:
    if len(outline) < 3:
        return [(float(x), float(y)) for x, y in outline]
    out: list[tuple[float, float]] = []
    for i in range(max(12, int(samples))):
        angle = 2.0 * math.pi * i / max(12, int(samples))
        point = _ray_outline_point(center_xy_mm, outline, angle, extend_mm=extend_mm)
        if point is not None:
            out.append(point)
    return out


def _focus_slot_count(params, radius_x_mm: float, radius_y_mm: float) -> int:
    if params.spacing_mode == "angle":
        step_deg = max(0.1, float(params.spacing_angle_deg))
        raw_count = max(4, int(round(360.0 / step_deg)))
    else:
        step_mm = max(0.01, float(params.spacing_distance_mm))
        raw_count = max(8, int(round(_ellipse_perimeter_mm(radius_x_mm, radius_y_mm) / step_mm)))
    return min(raw_count, _max_line_count(params))


def _focus_slot_count_for_outline(
    params,
    outline: Sequence[tuple[float, float]],
    radius_x_mm: float,
    radius_y_mm: float,
) -> int:
    if params.spacing_mode == "angle":
        return _focus_slot_count(params, radius_x_mm, radius_y_mm)
    step_mm = max(0.01, float(params.spacing_distance_mm))
    perimeter = _poly_perimeter_mm(outline) or _ellipse_perimeter_mm(radius_x_mm, radius_y_mm)
    raw_count = max(8, int(round(perimeter / step_mm)))
    return min(raw_count, _max_line_count(params))


def _bundle_gap_slots(params) -> int:
    if not bool(getattr(params, "bundle_enabled", False)):
        return 0
    gap = max(0.0, float(getattr(params, "bundle_gap_mm", 0.0)))
    if params.spacing_mode == "angle":
        unit = max(0.1, float(params.spacing_angle_deg))
    else:
        unit = max(0.01, float(params.spacing_distance_mm))
    return max(1, int(round(gap / unit))) if gap > 0.0 else 1


def _slot_positions(count: int, params, rng: random.Random) -> list[float]:
    count = max(1, int(count))
    if not bool(getattr(params, "bundle_enabled", False)):
        return [float(i) for i in range(count)]
    bundle_size = max(1, int(getattr(params, "bundle_line_count", 4)))
    gap_slots = _bundle_gap_slots(params)
    bundle_jitter = _clamp01(getattr(params, "bundle_jitter_amount", 0.0))
    out: list[float] = []
    slot = 0
    while slot < count:
        for i in range(bundle_size):
            pos = float(slot + i)
            if pos >= count:
                break
            if bundle_jitter > 0.0:
                pos += (rng.random() * 2.0 - 1.0) * 0.35 * bundle_jitter
            out.append(pos)
        slot += bundle_size + gap_slots
    return out


def _slot_fraction(slot: float, count: int, closed: bool) -> float:
    if count <= 1:
        return 0.5
    if closed:
        return (float(slot) % count) / float(count)
    return max(0.0, min(1.0, float(slot) / float(count - 1)))


def _stroke_radii(params, radius_mm: float, point_count: int = 2) -> tuple[float, list[float] | None]:
    base = mm_to_m(max(0.01, radius_mm) / 2.0)
    if getattr(params, "inout_apply", "brush_size") != "brush_size" or point_count < 2:
        return base, None
    start = base * (_clamp01(float(getattr(params, "in_percent", 100.0)) / 100.0))
    end = base * (_clamp01(float(getattr(params, "out_percent", 100.0)) / 100.0))
    if point_count == 2:
        return base, [start, end]
    radii = []
    for i in range(point_count):
        t = i / max(1, point_count - 1)
        radii.append(start + (end - start) * t)
    return base, radii


def generate_focus_strokes(
    params,
    center_xy_mm: tuple[float, float] = (110.0, 160.0),
    radius_x_mm: float = 40.0,
    radius_y_mm: float = 50.0,
    seed: int = 0,
    start_outline_mm: Sequence[tuple[float, float]] | None = None,
    start_extend_mm: float = 0.0,
) -> list[EffectLineStroke]:
    """集中線 (focus) のストローク生成.

    始点形状から終点形状へ線を引く。終点形状が CSP の「内側」に相当する。
    """
    rng = random.Random(seed)
    out: list[EffectLineStroke] = []
    cx, cy = center_xy_mm
    end_rect = _scaled_rect(cx, cy, radius_x_mm, radius_y_mm, 1.0)
    end_outline = _shape_outline(params, "end", end_rect, center_xy_mm)
    if start_outline_mm is None:
        start_rect = _scaled_rect(cx, cy, radius_x_mm, radius_y_mm, 2.0)
        start_outline = _shape_outline(params, "start", start_rect, center_xy_mm)
        start_extend = 0.0
    else:
        start_outline = [(float(x), float(y)) for x, y in start_outline_mm]
        start_extend = max(0.0, float(start_extend_mm))
    count = _focus_slot_count_for_outline(params, end_outline, radius_x_mm, radius_y_mm)
    step_angle = (2.0 * math.pi) / max(1, count)

    for slot in _slot_positions(count, params, rng):
        t = _slot_fraction(slot, count, closed=True)
        angle = 2.0 * math.pi * t + math.radians(float(params.rotation_deg))
        if bool(getattr(params, "spacing_jitter_enabled", False)):
            amount = _clamp01(getattr(params, "spacing_jitter_amount", 0.0))
            angle += step_angle * amount * (rng.random() * 2.0 - 1.0)
        x0, y0 = _point_on_outline_or_ellipse(
            center_xy_mm,
            start_outline,
            radius_x_mm * 2.0,
            radius_y_mm * 2.0,
            angle,
            extend_mm=start_extend,
        )
        x1, y1 = _point_on_outline_or_ellipse(
            center_xy_mm,
            end_outline,
            radius_x_mm,
            radius_y_mm,
            angle,
        )

        radius_mm = _jitter(
            params.brush_size_mm,
            params.brush_jitter_amount if params.brush_jitter_enabled else 0.0,
            rng,
        )
        radius, radii = _stroke_radii(params, radius_mm, 2)

        out.append(
            EffectLineStroke(
                points_xyz=[
                    (mm_to_m(x0), mm_to_m(y0), 0.0),
                    (mm_to_m(x1), mm_to_m(y1), 0.0),
                ],
                radius=radius,
                radii=radii,
            )
        )
    return out


def generate_speed_strokes(
    params,
    origin_xy_mm: tuple[float, float] = (40.0, 120.0),
    region_width_mm: float = 120.0,
    region_height_mm: float = 80.0,
    fixed_span_mm: float | None = None,
    seed: int = 0,
) -> list[EffectLineStroke]:
    """流線 (speed) のストローク生成."""
    rng = random.Random(seed)
    out: list[EffectLineStroke] = []
    line_cap = min(_max_line_count(params), max(1, int(params.speed_line_count)))
    if params.spacing_mode == "distance":
        step_mm = max(0.01, float(params.spacing_distance_mm))
        count = max(1, int(round(region_height_mm / step_mm)) + 1)
    else:
        count = line_cap
    count = min(count, line_cap)
    angle = math.radians(params.speed_angle_deg)
    dx = math.cos(angle)
    dy = math.sin(angle)
    nx = -dy
    ny = dx
    span = max(0.1, float(fixed_span_mm if fixed_span_mm is not None else region_width_mm))
    cx, cy = origin_xy_mm
    spacing_step = region_height_mm / max(1, count - 1) if count > 1 else 0.0
    for slot in _slot_positions(count, params, rng):
        t = _slot_fraction(slot, count, closed=False)
        offset = (t - 0.5) * region_height_mm
        if bool(getattr(params, "spacing_jitter_enabled", False)):
            amount = _clamp01(getattr(params, "spacing_jitter_amount", 0.0))
            offset += spacing_step * amount * (rng.random() * 2.0 - 1.0)
        mid_x = cx + nx * offset
        mid_y = cy + ny * offset
        sx = mid_x - dx * span * 0.5
        sy = mid_y - dy * span * 0.5
        ex = mid_x + dx * span * 0.5
        ey = mid_y + dy * span * 0.5
        radius_mm = _jitter(
            params.brush_size_mm,
            params.brush_jitter_amount if params.brush_jitter_enabled else 0.0,
            rng,
        )
        radius, radii = _stroke_radii(params, radius_mm, 2)
        out.append(
            EffectLineStroke(
                points_xyz=[(mm_to_m(sx), mm_to_m(sy), 0.0), (mm_to_m(ex), mm_to_m(ey), 0.0)],
                radius=radius,
                radii=radii,
            )
        )
    return out


def generate_beta_flash_strokes(
    params,
    center_xy_mm: tuple[float, float],
    radius_x_mm: float,
    radius_y_mm: float,
    seed: int = 0,
) -> list[EffectLineStroke]:
    """ベタフラ: 終点形状を閉じたストロークとして生成 (塗り設定は別途)."""
    _ = seed
    rect = _scaled_rect(center_xy_mm[0], center_xy_mm[1], radius_x_mm, radius_y_mm, 1.0)
    outline = _shape_outline(params, "end", rect, center_xy_mm)
    points = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in outline]
    radius, radii = _stroke_radii(params, params.brush_size_mm, len(points))
    return [
        EffectLineStroke(
            points_xyz=points,
            radius=radius,
            cyclic=True,
            radii=radii,
        )
    ]


def generate_strokes(
    params,
    center_xy_mm=(110.0, 160.0),
    radius_xy_mm=(40.0, 50.0),
    seed=0,
    start_outline_mm: Sequence[tuple[float, float]] | None = None,
    start_extend_mm: float = 0.0,
):
    etype = params.effect_type
    rx, ry = radius_xy_mm
    if etype == "speed":
        return generate_speed_strokes(
            params,
            origin_xy_mm=center_xy_mm,
            region_width_mm=rx * 2.0,
            region_height_mm=ry * 2.0,
            fixed_span_mm=rx * 2.0,
            seed=seed,
        )
    if etype == "beta_flash":
        return generate_beta_flash_strokes(params, center_xy_mm, rx, ry, seed=seed)
    return generate_focus_strokes(
        params,
        center_xy_mm,
        rx,
        ry,
        seed=seed,
        start_outline_mm=start_outline_mm,
        start_extend_mm=start_extend_mm,
    )


def generate_shape_guide_strokes(
    params,
    center_xy_mm=(110.0, 160.0),
    radius_xy_mm=(40.0, 50.0),
    start_outline_mm: Sequence[tuple[float, float]] | None = None,
    start_extend_mm: float = 0.0,
) -> list[EffectLineStroke]:
    """始点/終点の形状ラインをガイドストロークとして返す。"""
    rx, ry = radius_xy_mm
    cx, cy = center_xy_mm
    end_rect = _scaled_rect(cx, cy, rx, ry, 1.0)
    end_outline = _shape_outline(params, "end", end_rect, center_xy_mm)
    if start_outline_mm is None:
        start_rect = _scaled_rect(cx, cy, rx, ry, 2.0)
        start_outline = _shape_outline(params, "start", start_rect, center_xy_mm)
        start_smooth = _shape_guide_uses_smooth_bezier(params, "start")
    else:
        start_outline = _actual_outline_by_rays(
            center_xy_mm,
            start_outline_mm,
            extend_mm=max(0.0, float(start_extend_mm)),
        )
        start_smooth = _shape_guide_uses_smooth_bezier(params, "start", frame_outline=True)
    radius = mm_to_m(max(0.05, min(0.25, float(getattr(params, "brush_size_mm", 0.4)) * 0.4)) / 2.0)
    guides: list[EffectLineStroke] = []
    if len(start_outline) >= 2:
        guides.append(
            EffectLineStroke(
                points_xyz=[(mm_to_m(x), mm_to_m(y), 0.0) for x, y in start_outline],
                radius=radius,
                cyclic=True,
                role="start_guide",
                curve_type="BEZIER",
                bezier_smooth=start_smooth,
            )
        )
    if len(end_outline) >= 2:
        guides.append(
            EffectLineStroke(
                points_xyz=[(mm_to_m(x), mm_to_m(y), 0.0) for x, y in end_outline],
                radius=radius,
                cyclic=True,
                role="end_guide",
                curve_type="BEZIER",
                bezier_smooth=_shape_guide_uses_smooth_bezier(params, "end"),
            )
        )
    return guides
