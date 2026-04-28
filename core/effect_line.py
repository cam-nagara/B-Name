"""効果線 (集中線/ウニフラ/ベタフラ/流線/白抜き線) の PropertyGroup.

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
)

from . import balloon
from ..utils import log

_logger = log.get_logger(__name__)


_EFFECT_TYPE_ITEMS = (
    ("focus", "集中線", "放射状の集中線"),
    ("uni_flash", "ウニフラ", "ギザギザ基準図形の集中線"),
    ("beta_flash", "ベタフラ", "塗りつぶし版ウニフラ"),
    ("speed", "流線", "動き・速度表現の平行線"),
    ("white_outline", "白抜き線", "白線群の両側に黒線群を重ねた効果線"),
)

_EFFECT_SHAPE_ITEMS = tuple(
    item for item in balloon._SHAPE_ITEMS if item[0] not in {"custom", "none"}
)

_SPACING_MODE_ITEMS = (
    ("angle", "角度指定", ""),
    ("distance", "距離指定", ""),
)

_INOUT_APPLY_ITEMS = (
    ("brush_size", "ブラシサイズ", ""),
    ("opacity", "不透明度", ""),
)

_LEGACY_BASE_SHAPE_TO_EFFECT_SHAPE = {
    "rect": "rect",
    "ellipse": "ellipse",
    "polygon": "octagon",
}

EFFECT_PARAM_FIELDS = (
    "effect_type",
    "rotation_deg",
    "start_shape",
    "start_to_coma_frame",
    "start_rounded_corner_enabled",
    "start_rounded_corner_radius_mm",
    "start_cloud_bump_width_mm",
    "start_cloud_bump_height_mm",
    "start_cloud_offset_percent",
    "start_cloud_sub_width_ratio",
    "start_cloud_sub_height_ratio",
    "end_shape",
    "end_rounded_corner_enabled",
    "end_rounded_corner_radius_mm",
    "end_cloud_bump_width_mm",
    "end_cloud_bump_height_mm",
    "end_cloud_offset_percent",
    "end_cloud_sub_width_ratio",
    "end_cloud_sub_height_ratio",
    "brush_size_mm",
    "brush_jitter_enabled",
    "brush_jitter_amount",
    "spacing_mode",
    "spacing_angle_deg",
    "spacing_distance_mm",
    "spacing_jitter_enabled",
    "spacing_jitter_amount",
    "max_line_count",
    "bundle_enabled",
    "bundle_line_count",
    "bundle_jitter_amount",
    "bundle_gap_mm",
    "inout_apply",
    "in_percent",
    "out_percent",
    "line_color",
    "fill_color",
    "fill_opacity",
    "fill_base_shape",
    "speed_angle_deg",
    "speed_line_count",
    "white_outline_count",
    "white_outline_spacing_mm",
    "white_outline_width_mm",
    "white_outline_width_jitter_enabled",
    "white_outline_width_min_percent",
    "white_outline_length_jitter_enabled",
    "white_outline_length_min_percent",
    "white_outline_white_ratio_percent",
    "white_outline_white_brush_mm",
    "white_outline_white_attenuation",
    "white_outline_black_brush_mm",
    "white_outline_black_attenuation",
    "white_outline_angle_deg",
)


def _on_params_changed(self, context) -> None:
    """選択中の効果線レイヤーへ詳細設定の変更を即時反映する。"""
    if context is None:
        return
    try:
        from ..operators import effect_line_op

        effect_line_op.on_effect_params_changed(context, self)
    except Exception:  # noqa: BLE001
        _logger.exception("effect_line params update failed")


def _color_value(value) -> list[float]:
    try:
        return [float(value[i]) for i in range(4)]
    except Exception:  # noqa: BLE001
        return [0.0, 0.0, 0.0, 1.0]


def effect_params_to_dict(params) -> dict:
    """BNameEffectLineParams をレイヤーメタデータ保存用 dict に変換する。"""
    data = {}
    for field in EFFECT_PARAM_FIELDS:
        if not hasattr(params, field):
            continue
        value = getattr(params, field)
        if field in {"line_color", "fill_color"}:
            data[field] = _color_value(value)
        elif field == "inout_apply":
            data[field] = str(value) if str(value) in {"brush_size", "opacity"} else "brush_size"
        elif isinstance(value, bool):
            data[field] = bool(value)
        elif isinstance(value, int):
            data[field] = int(value)
        elif isinstance(value, float):
            data[field] = float(value)
        else:
            data[field] = str(value)
    return data


def effect_params_from_dict(params, data: dict) -> None:
    """保存済み dict を BNameEffectLineParams へ戻す。未知項目は無視する。"""
    data = dict(data or {})
    if "end_shape" not in data and "base_shape" in data:
        data["end_shape"] = _LEGACY_BASE_SHAPE_TO_EFFECT_SHAPE.get(str(data["base_shape"]), "rect")
    if str(data.get("inout_apply", "")) == "length":
        data["inout_apply"] = "brush_size"
    for field in EFFECT_PARAM_FIELDS:
        if field not in data or not hasattr(params, field):
            continue
        value = data[field]
        try:
            if field in {"line_color", "fill_color"}:
                setattr(params, field, tuple(float(v) for v in value[:4]))
            else:
                setattr(params, field, value)
        except Exception:  # noqa: BLE001
            _logger.debug("effect_line param restore skipped: %s=%r", field, value)


class BNameEffectLineParams(bpy.types.PropertyGroup):
    """効果線ツールのパラメータ (プリセット保存対象)."""

    effect_type: EnumProperty(name="種類", items=_EFFECT_TYPE_ITEMS, default="focus", update=_on_params_changed)  # type: ignore[valid-type]
    rotation_deg: FloatProperty(name="全体回転", default=0.0, update=_on_params_changed)  # type: ignore[valid-type]

    start_shape: EnumProperty(name="始点形状", items=_EFFECT_SHAPE_ITEMS, default="rect", update=_on_params_changed)  # type: ignore[valid-type]
    start_to_coma_frame: BoolProperty(name="始点をコマ枠に設定", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    start_rounded_corner_enabled: BoolProperty(name="角丸", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    start_rounded_corner_radius_mm: FloatProperty(name="角半径", default=3.0, min=0.0, soft_max=30.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_bump_width_mm: FloatProperty(name="山の幅", default=10.0, min=2.0, soft_max=50.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_bump_height_mm: FloatProperty(name="山の高さ", default=4.0, min=0.5, soft_max=25.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_offset_percent: FloatProperty(name="ズラし量 (%)", default=50.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_sub_width_ratio: FloatProperty(name="小山幅 (%)", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    start_cloud_sub_height_ratio: FloatProperty(name="小山高 (%)", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]

    end_shape: EnumProperty(name="終点形状", items=_EFFECT_SHAPE_ITEMS, default="ellipse", update=_on_params_changed)  # type: ignore[valid-type]
    end_rounded_corner_enabled: BoolProperty(name="角丸", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    end_rounded_corner_radius_mm: FloatProperty(name="角半径", default=3.0, min=0.0, soft_max=30.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_bump_width_mm: FloatProperty(name="山の幅", default=10.0, min=2.0, soft_max=50.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_bump_height_mm: FloatProperty(name="山の高さ", default=4.0, min=0.5, soft_max=25.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_offset_percent: FloatProperty(name="ズラし量 (%)", default=50.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_sub_width_ratio: FloatProperty(name="小山幅 (%)", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    end_cloud_sub_height_ratio: FloatProperty(name="小山高 (%)", default=0.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]

    brush_size_mm: FloatProperty(name="ブラシサイズ", default=0.40, min=0.01, soft_max=5.0, update=_on_params_changed)  # type: ignore[valid-type]
    brush_jitter_enabled: BoolProperty(name="乱れ", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    brush_jitter_amount: FloatProperty(name="乱れ量", default=0.2, min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]

    spacing_mode: EnumProperty(name="線の間隔", items=_SPACING_MODE_ITEMS, default="distance", update=_on_params_changed)  # type: ignore[valid-type]
    spacing_angle_deg: FloatProperty(name="線の間隔 (角度)", default=5.0, min=0.1, soft_max=90.0, update=_on_params_changed)  # type: ignore[valid-type]
    spacing_distance_mm: FloatProperty(name="線の間隔 (距離)", default=0.40, min=0.01, soft_max=50.0, update=_on_params_changed)  # type: ignore[valid-type]
    spacing_jitter_enabled: BoolProperty(name="乱れ", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    spacing_jitter_amount: FloatProperty(name="間隔乱れ量", default=0.2, min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    max_line_count: IntProperty(name="最大本数", default=300, min=1, soft_max=1000, update=_on_params_changed)  # type: ignore[valid-type]

    bundle_enabled: BoolProperty(name="まとまり", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    bundle_line_count: IntProperty(name="数", default=4, min=1, soft_max=50, update=_on_params_changed)  # type: ignore[valid-type]
    bundle_jitter_amount: FloatProperty(name="まとまりの乱れ", default=0.2, min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    bundle_gap_mm: FloatProperty(name="まとまり間隔", default=0.2, min=0.0, soft_max=20.0, update=_on_params_changed)  # type: ignore[valid-type]

    inout_apply: EnumProperty(name="適用先", items=_INOUT_APPLY_ITEMS, default="brush_size", update=_on_params_changed)  # type: ignore[valid-type]
    in_percent: FloatProperty(name="入り (%)", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    out_percent: FloatProperty(name="抜き (%)", default=100.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]

    line_color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    fill_color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    fill_opacity: FloatProperty(name="塗り不透明度", default=1.0, min=0.0, max=1.0, update=_on_params_changed)  # type: ignore[valid-type]
    fill_base_shape: BoolProperty(name="下地を塗る", default=False, update=_on_params_changed)  # type: ignore[valid-type]

    # 流線固有
    speed_angle_deg: FloatProperty(name="流線の角度", default=0.0, update=_on_params_changed)  # type: ignore[valid-type]
    speed_line_count: IntProperty(name="流線の本数上限", default=20, min=1, soft_max=200, update=_on_params_changed)  # type: ignore[valid-type]

    # 白抜き線固有
    white_outline_count: IntProperty(name="本数", default=5, min=1, soft_max=100, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_spacing_mm: FloatProperty(name="間隔", default=0.2, min=0.0, soft_max=20.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_width_mm: FloatProperty(name="太さ", default=10.0, min=0.01, soft_max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_width_jitter_enabled: BoolProperty(name="太さ乱れ", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_width_min_percent: FloatProperty(name="最小太さ (%)", default=50.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_length_jitter_enabled: BoolProperty(name="長さ乱れ", default=False, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_length_min_percent: FloatProperty(name="最小長さ (%)", default=50.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_ratio_percent: FloatProperty(name="白線割合 (%)", default=30.0, min=0.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_brush_mm: FloatProperty(name="白線太さ", default=0.3, min=0.01, soft_max=5.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_white_attenuation: FloatProperty(name="白線減衰", default=0.0, min=-100.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_brush_mm: FloatProperty(name="黒線太さ", default=0.3, min=0.01, soft_max=5.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_black_attenuation: FloatProperty(name="黒線減衰", default=0.0, min=-100.0, max=100.0, update=_on_params_changed)  # type: ignore[valid-type]
    white_outline_angle_deg: FloatProperty(name="角度", default=0.0, update=_on_params_changed)  # type: ignore[valid-type]


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
