"""フキダシ (Balloon) の PropertyGroup.

計画書 3.1.4 参照。5 形状プリセット (矩形/楕円/雲/トゲ曲線/トゲ直線) +
角丸オプション + 尻尾 3 種 + カスタム形状参照。

描画ロジックは ui/overlay.py および書き出し側 (Phase 6) で扱う。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)


_SHAPE_ITEMS = (
    ("rect", "矩形", "直線で囲まれた長方形"),
    ("ellipse", "楕円", "楕円形のフキダシ"),
    ("pill", "ピル", "両端が半円の丸長方形"),
    ("hexagon", "六角形", "六角形 (Meldex hexagon)"),
    ("octagon", "八角形", "八角形 (Meldex octagon)"),
    ("diamond", "ひし形", "ダイヤ形 (Meldex diamond)"),
    ("star", "星", "5 角星 (Meldex star)"),
    ("cloud", "雲", "モクモクとした雲形 (思考向け)"),
    ("fluffy", "もやもや", "緩い波の楕円 (Meldex fluffy)"),
    ("spike_curve", "トゲ (曲線)", "各トゲが曲線 (叫び向け)"),
    ("spike_straight", "トゲ (直線)", "各トゲが直線 (叫び向け)"),
    ("custom", "カスタム", "カスタム形状プリセット参照"),
    ("none", "本体なし", "テキスト単体 (擬音/ナレーション用)"),
)

_TAIL_TYPE_ITEMS = (
    ("straight", "直線", "三角形の直線状尻尾"),
    ("curve", "曲線", "ベジェで膨らませた曲線状尻尾"),
    ("sticky", "付箋", "矩形タブ状の尻尾"),
)

_LINE_STYLE_ITEMS = (
    ("solid", "実線", ""),
    ("dashed", "破線", ""),
    ("dotted", "点線", ""),
    ("double", "二重線", ""),
)


class BNameBalloonTail(bpy.types.PropertyGroup):
    type: EnumProperty(items=_TAIL_TYPE_ITEMS, default="straight")  # type: ignore[valid-type]
    direction_deg: FloatProperty(name="方向 (度)", default=270.0, soft_min=-360.0, soft_max=360.0)  # type: ignore[valid-type]
    length_mm: FloatProperty(name="長さ", default=6.0, min=0.0, soft_max=50.0)  # type: ignore[valid-type]
    root_width_mm: FloatProperty(name="根元幅", default=3.0, min=0.0, soft_max=20.0)  # type: ignore[valid-type]
    tip_width_mm: FloatProperty(name="先端幅", default=0.0, min=0.0, soft_max=20.0)  # type: ignore[valid-type]
    curve_bend: FloatProperty(  # type: ignore[valid-type]
        name="曲げ",
        description="曲線尻尾のみ: -1.0〜1.0 で曲がり具合",
        default=0.0,
        soft_min=-1.0,
        soft_max=1.0,
    )


class BNameBalloonShapeParams(bpy.types.PropertyGroup):
    """形状固有パラメータ (雲の波数/トゲの本数等)."""

    cloud_wave_count: IntProperty(name="雲の波数", default=12, min=3, soft_max=60)  # type: ignore[valid-type]
    cloud_wave_amplitude_mm: FloatProperty(name="波の振幅", default=3.0, min=0.0, soft_max=20.0)  # type: ignore[valid-type]
    spike_count: IntProperty(name="トゲ数", default=24, min=3, soft_max=80)  # type: ignore[valid-type]
    spike_depth_mm: FloatProperty(name="トゲの深さ", default=6.0, min=0.0, soft_max=30.0)  # type: ignore[valid-type]
    spike_jitter: FloatProperty(  # type: ignore[valid-type]
        name="トゲのばらつき",
        description="0.0-1.0 で形状不規則さ",
        default=0.2,
        min=0.0,
        max=1.0,
    )


class BNameBalloonEntry(bpy.types.PropertyGroup):
    """フキダシ 1 件."""

    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    shape: EnumProperty(name="形状", items=_SHAPE_ITEMS, default="rect")  # type: ignore[valid-type]
    custom_preset_name: StringProperty(  # type: ignore[valid-type]
        name="カスタム形状名",
        description="shape=custom のとき参照するプリセット名",
        default="",
    )

    # 配置 (mm)
    x_mm: FloatProperty(name="X", default=0.0)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", default=0.0)  # type: ignore[valid-type]
    width_mm: FloatProperty(name="幅", default=40.0, min=0.1)  # type: ignore[valid-type]
    height_mm: FloatProperty(name="高さ", default=20.0, min=0.1)  # type: ignore[valid-type]
    rotation_deg: FloatProperty(name="回転", default=0.0)  # type: ignore[valid-type]

    # 角丸 (全形状共通オプション、計画書 3.1.4.2a)
    rounded_corner_enabled: BoolProperty(name="角丸", default=False)  # type: ignore[valid-type]
    rounded_corner_radius_mm: FloatProperty(name="角半径", default=3.0, min=0.0, soft_max=30.0)  # type: ignore[valid-type]

    # 線・塗り
    line_style: EnumProperty(items=_LINE_STYLE_ITEMS, default="solid")  # type: ignore[valid-type]
    line_width_mm: FloatProperty(name="線幅", default=0.6, min=0.0, soft_max=10.0)  # type: ignore[valid-type]
    line_color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0)  # type: ignore[valid-type]
    fill_color: FloatVectorProperty(subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0)  # type: ignore[valid-type]

    # 反転 / 不透明度 (Meldex flipH/flipV/opacity 相当)
    flip_h: BoolProperty(name="水平反転", default=False)  # type: ignore[valid-type]
    flip_v: BoolProperty(name="垂直反転", default=False)  # type: ignore[valid-type]
    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    # 形状固有パラメータ・尻尾
    shape_params: PointerProperty(type=BNameBalloonShapeParams)  # type: ignore[valid-type]
    tails: CollectionProperty(type=BNameBalloonTail)  # type: ignore[valid-type]

    # テキスト (実内容は TextEntry)
    text_id: StringProperty(name="Text ID", default="")  # type: ignore[valid-type]


_CLASSES = (
    BNameBalloonTail,
    BNameBalloonShapeParams,
    BNameBalloonEntry,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("balloon registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
