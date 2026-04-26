"""用紙設定 PropertyGroup.

work.json の ``paper`` セクションに対応するデータモデル。既定値は計画書
3.2.4「集英社マンガ誌汎用」プリセット (257×364mm / 600dpi) に合わせる。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)

_COLOR_MODE_ITEMS = (
    ("monochrome", "モノクロ", "2 値 (印刷入稿用途)"),
    ("grayscale", "グレースケール", "グレースケール"),
    ("rgb", "RGB", "RGB カラー"),
    ("cmyk", "CMYK", "CMYK カラー"),
)

_UNIT_ITEMS = (
    ("mm", "mm", "ミリメートル"),
    ("px", "px", "ピクセル"),
    ("inch", "inch", "インチ"),
)

_START_SIDE_ITEMS = (
    ("right", "右から", "1 ページ目は右ページ単独 (日本のマンガ・右綴じ)"),
    ("left", "左から", "1 ページ目は左ページ単独 (西洋本・左綴じ)"),
)

_READ_DIRECTION_ITEMS = (
    ("left", "左方向", "ページが左方向に進む (日本のマンガ既定)"),
    ("right", "右方向", "ページが右方向に進む (西洋本)"),
    ("down", "下方向", "ページが下方向に進む (縦スクロール)"),
)


def _tag_view3d_redraw(context) -> None:
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _on_paper_visual_changed(_self, context) -> None:
    try:
        from ..utils import gpencil as gp_utils

        gp_utils.sync_paper_material_color(getattr(_self, "paper_color", None))
    except Exception:  # noqa: BLE001
        pass
    _tag_view3d_redraw(context)


def _on_paper_layout_changed(_self, context) -> None:
    try:
        from ..core.work import get_work
        from ..utils import page_grid

        work = get_work(context)
        if work is not None and work.loaded:
            page_grid.apply_page_collection_transforms(context, work)
    except Exception:  # noqa: BLE001
        pass
    _tag_view3d_redraw(context)


class BNamePaperSettings(bpy.types.PropertyGroup):
    """用紙寸法・解像度・基本枠・セーフライン設定."""

    # --- キャンバス全体 ---
    # 単位は B-Name 独自の ``unit`` プロパティで管理するため、Blender の
    # シーン単位に依存する ``unit="LENGTH"`` は使わない (FloatProperty の
    # 既定 ``unit="NONE"`` にする)。
    canvas_width_mm: FloatProperty(  # type: ignore[valid-type]
        name="幅",
        description="原稿用紙の幅 (裁ち落とし込み、mm)",
        default=257.00,
        min=1.0,
        soft_max=1000.0,
    )
    canvas_height_mm: FloatProperty(  # type: ignore[valid-type]
        name="高さ",
        description="原稿用紙の高さ (裁ち落とし込み、mm)",
        default=364.00,
        min=1.0,
        soft_max=1000.0,
    )
    dpi: IntProperty(  # type: ignore[valid-type]
        name="解像度 (dpi)",
        description="書き出し基準の解像度",
        default=600,
        min=72,
        soft_max=1200,
    )
    unit: EnumProperty(  # type: ignore[valid-type]
        name="単位",
        description="UI 表示上の単位",
        items=_UNIT_ITEMS,
        default="mm",
    )

    # --- 仕上がり (製本) ---
    finish_width_mm: FloatProperty(  # type: ignore[valid-type]
        name="幅",
        description="製本後の仕上がり幅 (mm)",
        default=221.81,
        min=1.0,
        soft_max=1000.0,
    )
    finish_height_mm: FloatProperty(  # type: ignore[valid-type]
        name="高さ",
        description="製本後の仕上がり高さ (mm)",
        default=328.78,
        min=1.0,
        soft_max=1000.0,
    )
    bleed_mm: FloatProperty(  # type: ignore[valid-type]
        name="裁ち落とし幅",
        description="仕上がり枠の外側に確保する塗り足し (mm)",
        default=7.00,
        min=0.0,
        soft_max=50.0,
    )

    # --- 基本枠 (内枠) ---
    inner_frame_width_mm: FloatProperty(  # type: ignore[valid-type]
        name="幅",
        description="本文領域の幅 (mm)",
        default=180.00,
        min=1.0,
        soft_max=500.0,
    )
    inner_frame_height_mm: FloatProperty(  # type: ignore[valid-type]
        name="高さ",
        description="本文領域の高さ (mm)",
        default=270.00,
        min=1.0,
        soft_max=500.0,
    )
    inner_frame_offset_x_mm: FloatProperty(  # type: ignore[valid-type]
        name="横オフセット",
        default=0.00,
        soft_min=-100.0,
        soft_max=100.0,
    )
    inner_frame_offset_y_mm: FloatProperty(  # type: ignore[valid-type]
        name="縦オフセット",
        default=0.00,
        soft_min=-100.0,
        soft_max=100.0,
    )

    # --- セーフライン (天/地/ノド/小口) ---
    safe_top_mm: FloatProperty(  # type: ignore[valid-type]
        name="天",
        default=17.49,
        min=0.0,
        soft_max=100.0,
    )
    safe_bottom_mm: FloatProperty(  # type: ignore[valid-type]
        name="地",
        default=17.49,
        min=0.0,
        soft_max=100.0,
    )
    safe_gutter_mm: FloatProperty(  # type: ignore[valid-type]
        name="ノド",
        description="綴じ側のセーフライン (mm)",
        default=20.90,
        min=0.0,
        soft_max=100.0,
    )
    safe_fore_edge_mm: FloatProperty(  # type: ignore[valid-type]
        name="小口",
        description="綴じと反対側のセーフライン (mm)",
        default=17.23,
        min=0.0,
        soft_max=100.0,
    )

    # --- 色・線数 ---
    color_mode: EnumProperty(  # type: ignore[valid-type]
        name="基本表現色",
        items=_COLOR_MODE_ITEMS,
        default="monochrome",
    )
    default_line_count: FloatProperty(  # type: ignore[valid-type]
        name="基本線数",
        description="モノクロ書き出し時の網点線数",
        default=60.00,
        min=10.0,
        soft_max=200.0,
    )
    paper_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="用紙色",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_paper_visual_changed,
    )
    display_alpha: FloatProperty(  # type: ignore[valid-type]
        name="紙面表示アルファ",
        description="ビューポート上の紙面 (キャンバス色) の表示透明度. 0 で非表示、1 で完全不透明",
        default=0.85,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    color_profile: StringProperty(  # type: ignore[valid-type]
        name="カラープロファイル",
        description="表示/書き出し用 ICC プロファイル名",
        default="sRGB IEC61966-2.1",
    )

    # --- 綴じ / 読む方向 ---
    start_side: EnumProperty(  # type: ignore[valid-type]
        name="開始ページの位置",
        description="1 ページ目が見開きの左右どちらに来るか",
        items=_START_SIDE_ITEMS,
        default="left",
        update=_on_paper_layout_changed,
    )
    read_direction: EnumProperty(  # type: ignore[valid-type]
        name="読む方向",
        description="overview で次の見開きペアが置かれる方向",
        items=_READ_DIRECTION_ITEMS,
        default="left",
        update=_on_paper_layout_changed,
    )

    # --- プリセット参照 ---
    preset_name: StringProperty(  # type: ignore[valid-type]
        name="使用プリセット名",
        default="集英社マンガ誌汎用",
    )


_CLASSES = (BNamePaperSettings,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("paper registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
