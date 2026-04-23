"""効果線 (集中線/ウニフラ/ベタフラ/流線) の PropertyGroup.

計画書 3.1.6 参照。ツール起動時のパラメータセットと、生成済み効果線
レイヤーのメタデータを保持する。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)


_EFFECT_TYPE_ITEMS = (
    ("focus", "集中線", "放射状の集中線"),
    ("uni_flash", "ウニフラ", "ギザギザ基準図形の集中線"),
    ("beta_flash", "ベタフラ", "塗りつぶし版ウニフラ"),
    ("speed", "流線", "動き・速度表現の平行線"),
)

_BASE_SHAPE_ITEMS = (
    ("rect", "長方形", ""),
    ("ellipse", "楕円", ""),
    ("polygon", "多角形", ""),
)

_SPACING_MODE_ITEMS = (
    ("angle", "角度指定", ""),
    ("distance", "距離指定", ""),
)

_INOUT_APPLY_ITEMS = (
    ("brush_size", "ブラシサイズ", ""),
    ("length", "長さ", ""),
    ("opacity", "不透明度", ""),
)


class BNameEffectLineParams(bpy.types.PropertyGroup):
    """効果線ツールのパラメータ (プリセット保存対象)."""

    effect_type: EnumProperty(name="種類", items=_EFFECT_TYPE_ITEMS, default="focus")  # type: ignore[valid-type]
    base_shape: EnumProperty(name="基準図形", items=_BASE_SHAPE_ITEMS, default="rect")  # type: ignore[valid-type]
    base_vertex_count: IntProperty(name="基準頂点数 (多角形)", default=6, min=3, soft_max=24)  # type: ignore[valid-type]
    start_from_center: BoolProperty(name="中央から開始", default=False)  # type: ignore[valid-type]
    rotation_deg: FloatProperty(name="全体回転", default=0.0)  # type: ignore[valid-type]

    brush_size_mm: FloatProperty(name="ブラシサイズ", default=0.40, min=0.01, soft_max=5.0)  # type: ignore[valid-type]
    brush_jitter_enabled: BoolProperty(name="乱れ", default=False)  # type: ignore[valid-type]
    brush_jitter_amount: FloatProperty(name="乱れ量", default=0.2, min=0.0, max=1.0)  # type: ignore[valid-type]

    spacing_mode: EnumProperty(items=_SPACING_MODE_ITEMS, default="distance")  # type: ignore[valid-type]
    spacing_angle_deg: FloatProperty(name="角度間隔", default=5.0, min=0.1, soft_max=90.0)  # type: ignore[valid-type]
    spacing_distance_mm: FloatProperty(name="距離間隔", default=0.40, min=0.01, soft_max=50.0)  # type: ignore[valid-type]
    spacing_jitter_enabled: BoolProperty(name="間隔乱れ", default=False)  # type: ignore[valid-type]
    spacing_jitter_amount: FloatProperty(name="間隔乱れ量", default=0.2, min=0.0, max=1.0)  # type: ignore[valid-type]

    bundle_enabled: BoolProperty(name="まとまり", default=False)  # type: ignore[valid-type]
    bundle_jitter_amount: FloatProperty(name="束の乱れ", default=0.2, min=0.0, max=1.0)  # type: ignore[valid-type]
    bundle_gap_mm: FloatProperty(name="束内の隙間", default=0.2, min=0.0, soft_max=5.0)  # type: ignore[valid-type]

    length_mm: FloatProperty(name="長さ", default=10.0, min=0.1, soft_max=500.0)  # type: ignore[valid-type]
    length_jitter_enabled: BoolProperty(name="長さ乱れ", default=False)  # type: ignore[valid-type]
    length_jitter_amount: FloatProperty(name="長さ乱れ量", default=0.2, min=0.0, max=1.0)  # type: ignore[valid-type]
    extend_past_panel: BoolProperty(name="コマ外へ延長", default=False)  # type: ignore[valid-type]

    base_position: EnumProperty(  # type: ignore[valid-type]
        name="基準位置",
        items=(("start", "始点", ""), ("middle", "中点", ""), ("end", "終点", "")),
        default="start",
    )
    base_position_offset: FloatProperty(name="基準位置のずれ", default=2.0, soft_min=-20.0, soft_max=20.0)  # type: ignore[valid-type]
    base_jagged_enabled: BoolProperty(name="基準位置をギザギザに", default=False)  # type: ignore[valid-type]
    base_jagged_count: IntProperty(name="ギザ数", default=24, min=3, soft_max=80)  # type: ignore[valid-type]
    base_jagged_height_mm: FloatProperty(name="ギザ高さ", default=1.0, min=0.0, soft_max=10.0)  # type: ignore[valid-type]

    inout_apply: EnumProperty(items=_INOUT_APPLY_ITEMS, default="brush_size")  # type: ignore[valid-type]
    in_percent: FloatProperty(name="入り (%)", default=100.0, min=0.0, max=100.0)  # type: ignore[valid-type]
    out_percent: FloatProperty(name="抜き (%)", default=100.0, min=0.0, max=100.0)  # type: ignore[valid-type]

    line_color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0)  # type: ignore[valid-type]
    fill_color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0)  # type: ignore[valid-type]
    fill_opacity: FloatProperty(name="塗り不透明度", default=1.0, min=0.0, max=1.0)  # type: ignore[valid-type]
    fill_base_shape: BoolProperty(name="下地を塗る", default=False)  # type: ignore[valid-type]

    # 流線固有
    speed_angle_deg: FloatProperty(name="流線の角度", default=0.0)  # type: ignore[valid-type]
    speed_line_count: IntProperty(name="流線の本数", default=20, min=1, soft_max=200)  # type: ignore[valid-type]


_CLASSES = (BNameEffectLineParams,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("effect_line registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
