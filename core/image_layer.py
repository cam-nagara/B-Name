"""画像レイヤー (ビットマップ) の PropertyGroup.

計画書 3.1.1 参照。スキャンラフ取り込み・写真参照・実写背景用途。
draw_handler_add + gpu モジュールでオーバーレイ描画 (3.4.3a)。

書き出し時は io/export_pipeline.py が Pillow で合成する。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)

_BLEND_MODE_ITEMS = (
    ("normal", "通常", ""),
    ("multiply", "乗算", ""),
    ("screen", "スクリーン", ""),
    ("overlay", "オーバーレイ", ""),
    ("add", "加算", ""),
)


class BNameImageLayer(bpy.types.PropertyGroup):
    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    title: StringProperty(name="表示名", default="")  # type: ignore[valid-type]
    filepath: StringProperty(  # type: ignore[valid-type]
        name="画像パス",
        description="PNG/JPG/TIFF/PSD",
        subtype="FILE_PATH",
        default="",
    )
    # 配置 (mm)
    x_mm: FloatProperty(name="X", default=0.0)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", default=0.0)  # type: ignore[valid-type]
    width_mm: FloatProperty(name="幅", default=100.0, min=0.1)  # type: ignore[valid-type]
    height_mm: FloatProperty(name="高さ", default=100.0, min=0.1)  # type: ignore[valid-type]
    rotation_deg: FloatProperty(name="回転", default=0.0)  # type: ignore[valid-type]
    flip_x: BoolProperty(name="左右反転", default=False)  # type: ignore[valid-type]
    flip_y: BoolProperty(name="上下反転", default=False)  # type: ignore[valid-type]

    # 表示属性
    visible: BoolProperty(name="表示", default=True)  # type: ignore[valid-type]
    locked: BoolProperty(name="ロック", default=False)  # type: ignore[valid-type]
    opacity: FloatProperty(name="不透明度", default=1.0, min=0.0, max=1.0, subtype="FACTOR")  # type: ignore[valid-type]
    blend_mode: EnumProperty(name="ブレンド", items=_BLEND_MODE_ITEMS, default="normal")  # type: ignore[valid-type]

    # 簡易レベル補正 (下書き取込用途、計画書 3.1.1)
    brightness: FloatProperty(name="明度", default=0.0, soft_min=-1.0, soft_max=1.0)  # type: ignore[valid-type]
    contrast: FloatProperty(name="コントラスト", default=0.0, soft_min=-1.0, soft_max=1.0)  # type: ignore[valid-type]
    binarize_enabled: BoolProperty(name="2値化", default=False)  # type: ignore[valid-type]
    binarize_threshold: FloatProperty(  # type: ignore[valid-type]
        name="2値化しきい値",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    tint_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色合い",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
    )


_CLASSES = (BNameImageLayer,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("image_layer registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
