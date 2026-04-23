"""セーフライン外側オーバーレイの PropertyGroup.

描画 (draw_handler_add + gpu) は Phase 1-E で ui/overlay.py に実装。
ここではデータモデルと既定値のみ保持する。

仕様 (計画書 3.2.6):
- 既定色 #808080, 不透明度 30%, ブレンドモード 乗算
- 表示専用 — 書き出しには含めない (3.8.4 参照)
- 作品共通既定 (work.json)、ページ単位でオーバーライド可
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty

from ..utils import log

_logger = log.get_logger(__name__)

_BLEND_MODE_ITEMS = (
    ("multiply", "乗算", "乗算ブレンド (既定)"),
    ("normal", "通常", "通常ブレンド"),
    ("overlay", "オーバーレイ", "オーバーレイブレンド"),
)

# 既定色 #808080 を linear RGB ではなく sRGB 風の中間グレーで指定。
# Blender の FloatVectorProperty(subtype=COLOR) はリニア空間で扱うが、
# UI 上は 0.5 前後が中間グレーに見えるため 0.5 を既定とする。
_DEFAULT_COLOR = (0.5, 0.5, 0.5, 1.0)


class BNameSafeAreaOverlay(bpy.types.PropertyGroup):
    """セーフライン外側を塗りつぶして視認性を確保するビューポート専用オーバーレイ."""

    enabled: BoolProperty(  # type: ignore[valid-type]
        name="セーフライン外側オーバーレイ",
        description="本文として使えない範囲をマスクして表示 (書き出しには含まれない)",
        default=True,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="塗りつぶし色",
        subtype="COLOR",
        size=4,
        default=_DEFAULT_COLOR,
        min=0.0,
        max=1.0,
    )
    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        default=0.30,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    blend_mode: EnumProperty(  # type: ignore[valid-type]
        name="ブレンドモード",
        items=_BLEND_MODE_ITEMS,
        default="multiply",
    )


_CLASSES = (BNameSafeAreaOverlay,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("safe_area_overlay registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
