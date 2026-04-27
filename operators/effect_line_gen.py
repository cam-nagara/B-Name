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


def _jitter(base: float, amount: float, rng: random.Random) -> float:
    if amount <= 0.0:
        return base
    delta = base * amount * (rng.random() * 2.0 - 1.0)
    return base + delta


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
    # 間隔 (距離指定なら mm、角度指定なら角度)
    if params.spacing_mode == "angle":
        step_deg = max(0.5, params.spacing_angle_deg)
        count = max(4, int(360.0 / step_deg))
    else:
        perim = 2.0 * math.pi * (radius_x_mm + radius_y_mm) / 2.0  # 概算
        step_mm = max(0.1, params.spacing_distance_mm)
        count = max(8, int(perim / step_mm))

    # 基準位置オフセット
    base_offset = params.base_position_offset
    length_base = params.length_mm

    for i in range(count):
        angle = 2.0 * math.pi * i / count + math.radians(params.rotation_deg)
        # 楕円上の出発点
        sx = cx + radius_x_mm * math.cos(angle)
        sy = cy + radius_y_mm * math.sin(angle)
        # 線の長さ (乱れを加味)
        length_mm = _jitter(
            length_base,
            params.length_jitter_amount if params.length_jitter_enabled else 0.0,
            rng,
        )
        # 外側方向ベクトル
        dx = math.cos(angle)
        dy = math.sin(angle)
        # 基準位置ずれ
        sx += dx * base_offset
        sy += dy * base_offset
        # 終点 (中央開始 or 外周開始)
        if params.start_from_center:
            x0, y0 = cx, cy
            x1, y1 = sx, sy
        else:
            x0, y0 = sx, sy
            x1, y1 = sx + dx * length_mm, sy + dy * length_mm

        radius_mm = _jitter(
            params.brush_size_mm,
            params.brush_jitter_amount if params.brush_jitter_enabled else 0.0,
            rng,
        )

        out.append(
            EffectLineStroke(
                points_xyz=[
                    (mm_to_m(x0), mm_to_m(y0), 0.0),
                    (mm_to_m(x1), mm_to_m(y1), 0.0),
                ],
                radius=mm_to_m(radius_mm / 2.0),
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
    count = max(1, params.speed_line_count)
    angle = math.radians(params.speed_angle_deg)
    dx = math.cos(angle)
    dy = math.sin(angle)
    # 法線方向に等間隔配置
    nx = -dy
    ny = dx
    length = max(0.1, float(length_mm if length_mm is not None else params.length_mm))
    cx, cy = origin_xy_mm
    for i in range(count):
        t = (i / max(1, count - 1)) if count > 1 else 0.5
        offset = (t - 0.5) * region_height_mm
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
        out.append(
            EffectLineStroke(
                points_xyz=[(mm_to_m(sx), mm_to_m(sy), 0.0), (mm_to_m(ex), mm_to_m(ey), 0.0)],
                radius=mm_to_m(radius_mm / 2.0),
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
    jaggy = params.base_jagged_height_mm if params.base_jagged_enabled else 0.0
    points: list[tuple[float, float, float]] = []
    for i in range(n):
        angle = 2.0 * math.pi * i / n
        # 交互にギザギザ (内外)
        r_offset = jaggy if i % 2 == 0 else -jaggy
        rx = radius_x_mm + r_offset * (0.5 + 0.5 * rng.random())
        ry = radius_y_mm + r_offset * (0.5 + 0.5 * rng.random())
        x = center_xy_mm[0] + rx * math.cos(angle)
        y = center_xy_mm[1] + ry * math.sin(angle)
        points.append((mm_to_m(x), mm_to_m(y), 0.0))
    return [
        EffectLineStroke(
            points_xyz=points,
            radius=mm_to_m(params.brush_size_mm / 2.0),
            cyclic=True,
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
