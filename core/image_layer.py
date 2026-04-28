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


def _on_image_layer_changed(_self, context) -> None:
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


class BNameImageLayer(bpy.types.PropertyGroup):
    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    title: StringProperty(name="表示名", default="")  # type: ignore[valid-type]
    filepath: StringProperty(  # type: ignore[valid-type]
        name="画像パス",
        description="PNG/JPG/TIFF/PSD",
        subtype="FILE_PATH",
        default="",
        update=_on_image_layer_changed,
    )
    # 配置 (mm)
    x_mm: FloatProperty(name="X", default=0.0, update=_on_image_layer_changed)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", default=0.0, update=_on_image_layer_changed)  # type: ignore[valid-type]
    width_mm: FloatProperty(  # type: ignore[valid-type]
        name="幅",
        default=100.0,
        min=0.1,
        update=_on_image_layer_changed,
    )
    height_mm: FloatProperty(  # type: ignore[valid-type]
        name="高さ",
        default=100.0,
        min=0.1,
        update=_on_image_layer_changed,
    )
    rotation_deg: FloatProperty(  # type: ignore[valid-type]
        name="回転",
        default=0.0,
        update=_on_image_layer_changed,
    )
    flip_x: BoolProperty(  # type: ignore[valid-type]
        name="左右反転",
        default=False,
        update=_on_image_layer_changed,
    )
    flip_y: BoolProperty(  # type: ignore[valid-type]
        name="上下反転",
        default=False,
        update=_on_image_layer_changed,
    )

    # 表示属性
    visible: BoolProperty(name="表示", default=True, update=_on_image_layer_changed)  # type: ignore[valid-type]
    locked: BoolProperty(name="ロック", default=False)  # type: ignore[valid-type]
    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_on_image_layer_changed,
    )
    blend_mode: EnumProperty(  # type: ignore[valid-type]
        name="ブレンド",
        items=_BLEND_MODE_ITEMS,
        default="normal",
        update=_on_image_layer_changed,
    )

    # 簡易レベル補正 (下書き取込用途、計画書 3.1.1)
    brightness: FloatProperty(  # type: ignore[valid-type]
        name="明度",
        default=0.0,
        soft_min=-1.0,
        soft_max=1.0,
        update=_on_image_layer_changed,
    )
    contrast: FloatProperty(  # type: ignore[valid-type]
        name="コントラスト",
        default=0.0,
        soft_min=-1.0,
        soft_max=1.0,
        update=_on_image_layer_changed,
    )
    binarize_enabled: BoolProperty(  # type: ignore[valid-type]
        name="2値化",
        default=False,
        update=_on_image_layer_changed,
    )
    binarize_threshold: FloatProperty(  # type: ignore[valid-type]
        name="2値化しきい値",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_on_image_layer_changed,
    )

    tint_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色合い",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_image_layer_changed,
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
