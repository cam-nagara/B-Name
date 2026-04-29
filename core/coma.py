"""コマエントリ (ComaEntry) PropertyGroup.

page.json のコマリストに対応。cNN.blend の実体本体は Blender API
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
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..utils import log
from .coma_border import (
    BNameComaBorder,
    BNameComaEdgeStyle,
    BNameComaWhiteMargin,
)

_logger = log.get_logger(__name__)


_SHAPE_TYPE_ITEMS = (
    ("rect", "矩形", ""),
    ("polygon", "多角形", ""),
    ("bezier", "曲線", ""),
    ("freeform", "フリーフォーム", ""),
)


def _tag_view3d_redraw(context) -> None:
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _on_coma_background_color_changed(self, context) -> None:
    try:
        from ..core.mode import MODE_COMA, get_mode
        from ..utils import coma_camera

        scene = getattr(context, "scene", None)
        if scene is not None and get_mode(context) == MODE_COMA:
            stem = str(getattr(scene, "bname_current_coma_id", "") or "")
            if stem == str(getattr(self, "coma_id", "") or ""):
                coma_camera.sync_world_background_color(context, panel=self)
    except Exception:  # noqa: BLE001
        pass
    _tag_view3d_redraw(context)


def _on_coma_visible_changed(_self, context) -> None:
    _tag_view3d_redraw(context)


class BNameComaVertex(bpy.types.PropertyGroup):
    """コマ枠の頂点 (mm)."""

    x_mm: FloatProperty(name="X", default=0.0)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", default=0.0)  # type: ignore[valid-type]


class BNameLayerRef(bpy.types.PropertyGroup):
    """作画レイヤー ID 参照 (Grease Pencil / 画像レイヤー / フキダシ)."""

    layer_id: StringProperty(name="Layer ID", default="")  # type: ignore[valid-type]


class BNameComaEntry(bpy.types.PropertyGroup):
    """コマ 1 件分のメタデータ (cNN.json 相当)."""

    # --- 識別子 ---
    id: StringProperty(  # type: ignore[valid-type]
        name="コマ ID",
        description="cNN 形式のコマID (2 桁ゼロパディング)",
        default="",
    )
    title: StringProperty(  # type: ignore[valid-type]
        name="表示名",
        default="",
    )
    coma_id: StringProperty(  # type: ignore[valid-type]
        name="ファイル stem",
        description="cNN (ファイル名のベース)",
        default="",
    )

    # --- 形状 ---
    shape_type: EnumProperty(  # type: ignore[valid-type]
        name="形状",
        items=_SHAPE_TYPE_ITEMS,
        default="rect",
    )
    vertices: CollectionProperty(type=BNameComaVertex)  # type: ignore[valid-type]

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
    visible: BoolProperty(  # type: ignore[valid-type]
        name="表示",
        description="このコマ枠とプレビューを表示する",
        default=True,
        update=_on_coma_visible_changed,
    )
    selected: BoolProperty(  # type: ignore[valid-type]
        name="マルチ選択",
        default=False,
        options={"SKIP_SAVE"},
    )
    background_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="背景色",
        description="コマ内側に敷く背景色。アルファ0で透明",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 0.0),
        min=0.0,
        max=1.0,
        update=_on_coma_background_color_changed,
    )

    # --- 枠線・白フチ ---
    border: PointerProperty(type=BNameComaBorder)  # type: ignore[valid-type]
    white_margin: PointerProperty(type=BNameComaWhiteMargin)  # type: ignore[valid-type]
    # 辺ごと (edge_index) の個別オーバーライド (枠線選択ツールで設定)
    edge_styles: CollectionProperty(type=BNameComaEdgeStyle)  # type: ignore[valid-type]

    # --- 紐づけ ---
    layer_refs: CollectionProperty(type=BNameLayerRef)  # type: ignore[valid-type]
    coma_gap_vertical_mm: FloatProperty(  # type: ignore[valid-type]
        name="上下スキマ (個別)",
        default=-1.0,
        description="負値で作品共通ルールを継承",
    )
    coma_gap_horizontal_mm: FloatProperty(  # type: ignore[valid-type]
        name="左右スキマ (個別)",
        default=-1.0,
        description="負値で作品共通ルールを継承",
    )


_CLASSES = (
    BNameComaVertex,
    BNameLayerRef,
    BNameComaEntry,
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
