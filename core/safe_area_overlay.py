"""セーフライン外側オーバーレイの PropertyGroup.

描画 (draw_handler_add + gpu) は ui/overlay.py に実装。
ここではデータモデルと既定値のみ保持する。

仕様:
- 既定色 = 黒 30% グレー (RGB 0.7, 0.7, 0.7)、常に乗算合成・不透明度 100%
- 表示専用 — 書き出しには含めない
- 作品共通既定 (work.json)、ページ単位でオーバーライド可

opacity / blend_mode フィールドは UI から削除済 (常に乗算 alpha=1.0 で固定描画)。
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, FloatVectorProperty

from ..utils import log

_logger = log.get_logger(__name__)

# 黒 30 % のグレー = 紙の白に乗算するとビューポート上で 70% の明度になる.
# RGB 0.7 を 3 成分で指定 (alpha チャネルは持たない)。
_DEFAULT_COLOR = (0.7, 0.7, 0.7)


class BNameSafeAreaOverlay(bpy.types.PropertyGroup):
    """セーフライン外側を乗算で暗くするビューポート専用オーバーレイ."""

    enabled: BoolProperty(  # type: ignore[valid-type]
        name="セーフライン",
        description="セーフライン外を乗算で暗く表示 (書き出しには含まれない)",
        default=True,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="塗りつぶし色",
        subtype="COLOR",
        size=3,
        default=_DEFAULT_COLOR,
        min=0.0,
        max=1.0,
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
