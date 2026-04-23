"""コマ枠線・白フチの PropertyGroup.

計画書 3.2.5.1 参照。辺ごとのオーバーライドは「None=継承」を表現する
ため、``use_override`` BoolProperty で ON/OFF を切り替えるパターンで実装。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    PointerProperty,
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)


_LINE_STYLE_ITEMS = (
    ("solid", "実線", ""),
    ("dashed", "破線", ""),
    ("dotted", "点線", ""),
    ("double", "二重線", ""),
)

_CORNER_ITEMS = (
    ("square", "直角", ""),
    ("rounded", "丸角", ""),
    ("bevel", "面取り", ""),
)


class BNameBorderEdgeOverride(bpy.types.PropertyGroup):
    """枠線の辺ごとオーバーライド (4 辺それぞれに保持)."""

    use_override: BoolProperty(  # type: ignore[valid-type]
        name="この辺を個別設定",
        default=False,
    )
    style: EnumProperty(  # type: ignore[valid-type]
        name="線種",
        items=_LINE_STYLE_ITEMS,
        default="solid",
    )
    width_mm: FloatProperty(  # type: ignore[valid-type]
        name="線幅 (mm)",
        default=0.8,
        min=0.0,
        soft_max=10.0,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="線色",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    visible: BoolProperty(  # type: ignore[valid-type]
        name="表示",
        default=True,
    )


class BNamePanelBorder(bpy.types.PropertyGroup):
    """コマ枠線スタイル (全辺共通既定 + 辺ごとオーバーライド)."""

    style: EnumProperty(  # type: ignore[valid-type]
        name="線種",
        items=_LINE_STYLE_ITEMS,
        default="solid",
    )
    width_mm: FloatProperty(  # type: ignore[valid-type]
        name="線幅 (mm)",
        default=0.8,
        min=0.0,
        soft_max=10.0,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="線色",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    corner_type: EnumProperty(  # type: ignore[valid-type]
        name="角処理",
        items=_CORNER_ITEMS,
        default="square",
    )
    corner_radius_mm: FloatProperty(  # type: ignore[valid-type]
        name="角半径 (mm)",
        default=0.0,
        min=0.0,
        soft_max=20.0,
    )
    visible: BoolProperty(  # type: ignore[valid-type]
        name="枠線を表示",
        default=True,
    )

    # 辺ごとオーバーライド
    edge_top: PointerProperty(type=BNameBorderEdgeOverride)  # type: ignore[valid-type]
    edge_right: PointerProperty(type=BNameBorderEdgeOverride)  # type: ignore[valid-type]
    edge_bottom: PointerProperty(type=BNameBorderEdgeOverride)  # type: ignore[valid-type]
    edge_left: PointerProperty(type=BNameBorderEdgeOverride)  # type: ignore[valid-type]


class BNameWhiteMarginEdgeOverride(bpy.types.PropertyGroup):
    """白フチの辺ごとオーバーライド."""

    use_override: BoolProperty(name="この辺を個別設定", default=False)  # type: ignore[valid-type]
    enabled: BoolProperty(name="白フチ", default=False)  # type: ignore[valid-type]
    width_mm: FloatProperty(name="幅 (mm)", default=0.37, min=0.0, soft_max=5.0)  # type: ignore[valid-type]
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
    )


class BNamePanelWhiteMargin(bpy.types.PropertyGroup):
    """コマの白フチ (枠線の外側)."""

    enabled: BoolProperty(  # type: ignore[valid-type]
        name="白フチ",
        default=False,
    )
    width_mm: FloatProperty(  # type: ignore[valid-type]
        name="幅 (mm)",
        default=0.37,
        min=0.0,
        soft_max=5.0,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
    )
    edge_top: PointerProperty(type=BNameWhiteMarginEdgeOverride)  # type: ignore[valid-type]
    edge_right: PointerProperty(type=BNameWhiteMarginEdgeOverride)  # type: ignore[valid-type]
    edge_bottom: PointerProperty(type=BNameWhiteMarginEdgeOverride)  # type: ignore[valid-type]
    edge_left: PointerProperty(type=BNameWhiteMarginEdgeOverride)  # type: ignore[valid-type]


_CLASSES = (
    BNameBorderEdgeOverride,
    BNamePanelBorder,
    BNameWhiteMarginEdgeOverride,
    BNamePanelWhiteMargin,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("panel_border registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
