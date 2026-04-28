"""効果線ストローク生成ロジック (計画書 3.1.6).

BNameEffectLineParams を受け取り、放射状 / 流線の頂点列を算出する。
Grease Pencil v3 への書き込みは utils/gpencil.py を経由する。

このモジュールは純粋計算 (点列生成) と GP 統合を担う。Operator は
operators/effect_line_op.py から呼ぶ。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ..utils import log
from ..utils.geom import mm_to_m

_logger = log.get_logger(__name__)


@dataclass(frozen=True)
class EffectLineStroke:
    points_xyz: list[tuple[float, float, float]]
    radius: float  # m 単位
    cyclic: bool = False
    radii: list[float] | None = None


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


def _focus_slot_count(params, radius_x_mm: float, radius_y_mm: float) -> int:
    if params.spacing_mode == "angle":
        step_deg = max(0.1, float(params.spacing_angle_deg))
        raw_count = max(4, int(round(360.0 / step_deg)))
    else:
        step_mm = max(0.01, float(params.spacing_distance_mm))
        raw_count = max(8, int(round(_ellipse_perimeter_mm(radius_x_mm, radius_y_mm) / step_mm)))
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


def _base_point_on_shape(params, cx: float, cy: float, rx: float, ry: float, angle: float) -> tuple[float, float]:
    dx = math.cos(angle)
    dy = math.sin(angle)
    shape = getattr(params, "base_shape", "ellipse")
    if shape == "rect":
        tx = abs(rx / dx) if abs(dx) > 1e-6 else float("inf")
        ty = abs(ry / dy) if abs(dy) > 1e-6 else float("inf")
        t = min(tx, ty)
        return cx + dx * t, cy + dy * t
    if shape == "polygon":
        n = max(3, int(getattr(params, "base_vertex_count", 6)))
        sector = (2.0 * math.pi) / n
        local = (angle + math.pi / 2.0 + sector * 0.5) % sector - sector * 0.5
        scale = math.cos(sector * 0.5) / max(0.1, math.cos(local))
        return cx + dx * rx * scale, cy + dy * ry * scale
    return cx + rx * dx, cy + ry * dy


def _base_position_offset(params, index: int, angle: float, rng: random.Random) -> float:
    offset = 0.0
    if bool(getattr(params, "base_position_offset_enabled", False)):
        amount = max(0.0, float(getattr(params, "base_position_offset", 0.0)))
        offset += (rng.random() * 2.0 - 1.0) * amount
    if bool(getattr(params, "base_jagged_enabled", False)) or getattr(params, "effect_type", "") == "uni_flash":
        count = max(3, int(getattr(params, "base_jagged_count", 24)))
        phase = int(((angle % (2.0 * math.pi)) / (2.0 * math.pi)) * count)
        sign = 1.0 if phase % 2 == 0 else -1.0
        height = max(0.0, float(getattr(params, "base_jagged_height_mm", 0.0)))
        offset += sign * height * (0.55 + 0.45 * rng.random())
    return offset


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


def _base_shape_points(params, center_xy_mm: tuple[float, float], radius_x_mm: float, radius_y_mm: float) -> list[tuple[float, float]]:
    """基準図形 (長方形/楕円/多角形) の外周頂点を返す (mm, 閉じた輪郭)."""
    cx, cy = center_xy_mm
    shape = params.base_shape
    if shape == "rect":
        return [
            (cx - radius_x_mm, cy - radius_y_mm),
            (cx + radius_x_mm, cy - radius_y_mm),
            (cx + radius_x_mm, cy + radius_y_mm),
            (cx - radius_x_mm, cy + radius_y_mm),
        ]
    if shape == "ellipse":
        count = 64
        return [
            (
                cx + radius_x_mm * math.cos(2 * math.pi * i / count),
                cy + radius_y_mm * math.sin(2 * math.pi * i / count),
            )
            for i in range(count)
        ]
    if shape == "polygon":
        n = max(3, params.base_vertex_count)
        return [
            (
                cx + radius_x_mm * math.cos(2 * math.pi * i / n - math.pi / 2),
                cy + radius_y_mm * math.sin(2 * math.pi * i / n - math.pi / 2),
            )
            for i in range(n)
        ]
    return []


def generate_focus_strokes(
    params,
    center_xy_mm: tuple[float, float] = (110.0, 160.0),
    radius_x_mm: float = 40.0,
    radius_y_mm: float = 50.0,
    seed: int = 0,
) -> list[EffectLineStroke]:
    """集中線 (focus) のストローク生成.

    基準図形の外周上の点から、params.length_mm 方向 (中心から放射状 or
    中央から外側) に線を引く。
    """
    rng = random.Random(seed)
    out: list[EffectLineStroke] = []
    cx, cy = center_xy_mm
    count = _focus_slot_count(params, radius_x_mm, radius_y_mm)
    step_angle = (2.0 * math.pi) / max(1, count)
    length_base = max(0.1, float(params.length_mm))
    if bool(getattr(params, "extend_past_coma", False)):
        length_base = max(length_base, max(radius_x_mm, radius_y_mm) * 2.5)

    for stroke_index, slot in enumerate(_slot_positions(count, params, rng)):
        t = _slot_fraction(slot, count, closed=True)
        angle = 2.0 * math.pi * t + math.radians(float(params.rotation_deg))
        if bool(getattr(params, "spacing_jitter_enabled", False)):
            amount = _clamp01(getattr(params, "spacing_jitter_amount", 0.0))
            angle += step_angle * amount * (rng.random() * 2.0 - 1.0)
        sx, sy = _base_point_on_shape(params, cx, cy, radius_x_mm, radius_y_mm, angle)
        length_mm = _jitter(
            length_base,
            params.length_jitter_amount if params.length_jitter_enabled else 0.0,
            rng,
        )
        dx = math.cos(angle)
        dy = math.sin(angle)
        base_offset = _base_position_offset(params, stroke_index, angle, rng)
        sx += dx * base_offset
        sy += dy * base_offset
        if params.start_from_center:
            x0, y0 = cx, cy
            x1, y1 = sx, sy
        elif params.base_position == "middle":
            x0, y0 = sx - dx * length_mm * 0.5, sy - dy * length_mm * 0.5
            x1, y1 = sx + dx * length_mm * 0.5, sy + dy * length_mm * 0.5
        elif params.base_position == "end":
            x0, y0 = sx - dx * length_mm, sy - dy * length_mm
            x1, y1 = sx, sy
        else:
            x0, y0 = sx, sy
            x1, y1 = sx + dx * length_mm, sy + dy * length_mm

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
    length_mm: float | None = None,
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
    length = max(0.1, float(length_mm if length_mm is not None else params.length_mm))
    if bool(getattr(params, "extend_past_coma", False)):
        length = max(length, math.hypot(region_width_mm, region_height_mm) * 1.5)
    cx, cy = origin_xy_mm
    spacing_step = region_height_mm / max(1, count - 1) if count > 1 else 0.0
    for slot in _slot_positions(count, params, rng):
        t = _slot_fraction(slot, count, closed=False)
        offset = (t - 0.5) * region_height_mm
        if bool(getattr(params, "spacing_jitter_enabled", False)):
            amount = _clamp01(getattr(params, "spacing_jitter_amount", 0.0))
            offset += spacing_step * amount * (rng.random() * 2.0 - 1.0)
        line_length_mm = _jitter(
            length,
            params.length_jitter_amount if params.length_jitter_enabled else 0.0,
            rng,
        )
        mid_x = cx + nx * offset
        mid_y = cy + ny * offset
        sx = mid_x - dx * line_length_mm * 0.5
        sy = mid_y - dy * line_length_mm * 0.5
        ex = mid_x + dx * line_length_mm * 0.5
        ey = mid_y + dy * line_length_mm * 0.5
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
    """ベタフラ: ウニ状外周を閉じた多角形ストロークとして生成 (塗り設定は別途)."""
    rng = random.Random(seed)
    n = max(6, params.base_jagged_count if params.base_jagged_enabled else 24)
    n = min(n, _max_line_count(params))
    jaggy = params.base_jagged_height_mm if params.base_jagged_enabled else 0.0
    points: list[tuple[float, float, float]] = []
    for i in range(n):
        angle = 2.0 * math.pi * i / n + math.radians(float(params.rotation_deg))
        r_offset = jaggy if i % 2 == 0 else -jaggy
        rx = radius_x_mm + r_offset * (0.5 + 0.5 * rng.random())
        ry = radius_y_mm + r_offset * (0.5 + 0.5 * rng.random())
        x = center_xy_mm[0] + rx * math.cos(angle)
        y = center_xy_mm[1] + ry * math.sin(angle)
        points.append((mm_to_m(x), mm_to_m(y), 0.0))
    radius, radii = _stroke_radii(params, params.brush_size_mm, len(points))
    return [
        EffectLineStroke(
            points_xyz=points,
            radius=radius,
            cyclic=True,
            radii=radii,
        )
    ]


def generate_strokes(params, center_xy_mm=(110.0, 160.0), radius_xy_mm=(40.0, 50.0), seed=0):
    etype = params.effect_type
    rx, ry = radius_xy_mm
    if etype == "speed":
        return generate_speed_strokes(
            params,
            origin_xy_mm=center_xy_mm,
            region_width_mm=rx * 2.0,
            region_height_mm=ry * 2.0,
            length_mm=max(params.length_mm, rx * 2.0),
            seed=seed,
        )
    if etype == "beta_flash":
        return generate_beta_flash_strokes(params, center_xy_mm, rx, ry, seed=seed)
    # focus / uni_flash は放射状生成 (uni_flash は基準図形がギザギザ + 放射)
    return generate_focus_strokes(params, center_xy_mm, rx, ry, seed=seed)
