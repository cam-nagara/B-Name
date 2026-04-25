"""コマエントリ (PanelEntry) PropertyGroup.

page.json のコマリストに対応。panel_NNN.blend の実体本体は Blender API
側で管理し、ここではメタデータ (形状/Z順序/枠線/白フチ/リンク参照等) を
保持する。

計画書 3.2.5 / 4.7 参照。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..utils import log
from .panel_border import (
    BNamePanelBorder,
    BNamePanelEdgeStyle,
    BNamePanelWhiteMargin,
)

_logger = log.get_logger(__name__)


_SHAPE_TYPE_ITEMS = (
    ("rect", "矩形", ""),
    ("polygon", "多角形", ""),
    ("bezier", "曲線", ""),
    ("freeform", "フリーフォーム", ""),
)


class BNamePanelVertex(bpy.types.PropertyGroup):
    """コマ枠の頂点 (mm)."""

    x_mm: FloatProperty(name="X", default=0.0)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", default=0.0)  # type: ignore[valid-type]


class BNameLayerRef(bpy.types.PropertyGroup):
    """作画レイヤー ID 参照 (Grease Pencil / 画像レイヤー / フキダシ)."""

    layer_id: StringProperty(name="Layer ID", default="")  # type: ignore[valid-type]


class BNamePanelEntry(bpy.types.PropertyGroup):
    """コマ 1 件分のメタデータ (panel_NNN.json 相当)."""

    # --- 識別子 ---
    id: StringProperty(  # type: ignore[valid-type]
        name="コマ ID",
        description="panel_NNN の NNN 部分 (3 桁ゼロパディング)",
        default="",
    )
    title: StringProperty(  # type: ignore[valid-type]
        name="表示名",
        default="",
    )
    panel_stem: StringProperty(  # type: ignore[valid-type]
        name="ファイル stem",
        description="panel_NNN (ファイル名のベース)",
        default="",
    )

    # --- 形状 ---
    shape_type: EnumProperty(  # type: ignore[valid-type]
        name="形状",
        items=_SHAPE_TYPE_ITEMS,
        default="rect",
    )
    vertices: CollectionProperty(type=BNamePanelVertex)  # type: ignore[valid-type]

    # 矩形ショートカット (shape_type='rect' のときに使用)
    rect_x_mm: FloatProperty(name="X", default=0.0)  # type: ignore[valid-type]
    rect_y_mm: FloatProperty(name="Y", default=0.0)  # type: ignore[valid-type]
    rect_width_mm: FloatProperty(name="幅", default=50.0, min=0.1)  # type: ignore[valid-type]
    rect_height_mm: FloatProperty(name="高さ", default=50.0, min=0.1)  # type: ignore[valid-type]

    # --- Z順序・重なりくり抜き ---
    z_order: IntProperty(  # type: ignore[valid-type]
        name="Z順序",
        description="同ページ内のコマ重なり順 (大きいほど手前)",
        default=0,
    )
    overlap_clipping: BoolProperty(  # type: ignore[valid-type]
        name="自動くり抜き",
        description="手前コマが重なる範囲を自動的にくり抜く",
        default=True,
    )

    # --- 枠線・白フチ ---
    border: PointerProperty(type=BNamePanelBorder)  # type: ignore[valid-type]
    white_margin: PointerProperty(type=BNamePanelWhiteMargin)  # type: ignore[valid-type]
    # 辺ごと (edge_index) の個別オーバーライド (枠線選択ツールで設定)
    edge_styles: CollectionProperty(type=BNamePanelEdgeStyle)  # type: ignore[valid-type]

    # --- 紐づけ ---
    layer_refs: CollectionProperty(type=BNameLayerRef)  # type: ignore[valid-type]
    panel_gap_vertical_mm: FloatProperty(  # type: ignore[valid-type]
        name="上下スキマ (個別)",
        default=-1.0,
        description="負値で作品共通ルールを継承",
    )
    panel_gap_horizontal_mm: FloatProperty(  # type: ignore[valid-type]
        name="左右スキマ (個別)",
        default=-1.0,
        description="負値で作品共通ルールを継承",
    )


_CLASSES = (
    BNamePanelVertex,
    BNameLayerRef,
    BNamePanelEntry,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("panel registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
