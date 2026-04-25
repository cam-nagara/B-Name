"""作品情報・ノンブルの PropertyGroup.

work.json の ``workInfo`` / ``nombre`` セクションに対応。
原稿上への焼き込み描画は ui/overlay.py (Phase 1-E) で行う。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)

# 原稿上の 6 通り配置 (上下 × 左中右、middle 段は仕上がり枠外への配置で
# 自然なアンカーが取りづらく実用性が低いため除外)
_POSITION_ITEMS = (
    ("top-left", "左上", ""),
    ("top-center", "上中央", ""),
    ("top-right", "右上", ""),
    ("bottom-left", "左下", ""),
    ("bottom-center", "下中央", ""),
    ("bottom-right", "右下", ""),
)


class BNameDisplayItem(bpy.types.PropertyGroup):
    """原稿上に焼き込む 1 項目 (作品名/話数/サブタイトル/作者名) の表示設定."""

    enabled: BoolProperty(  # type: ignore[valid-type]
        name="表示",
        default=False,
    )
    position: EnumProperty(  # type: ignore[valid-type]
        name="位置",
        items=_POSITION_ITEMS,
        default="bottom-left",
    )
    # フォントサイズは Q 数 (写植単位、1 Q = 0.25 mm) で保持。
    # 既定値 10 Q (= 2.5 mm ≈ 7.087 pt) はマンガの作品情報・ノンブルでよく使われる
    # 標準サイズに近い (写植由来の "10 級" は 2.5mm)。
    font_size_q: FloatProperty(  # type: ignore[valid-type]
        name="フォントサイズ (Q)",
        description="文字サイズを Q 数 (1 Q = 0.25 mm) で指定",
        default=20.0,
        min=1.0,
        soft_max=200.0,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )


class BNameWorkInfo(bpy.types.PropertyGroup):
    """作品の書誌情報と、各項目の原稿上表示設定."""

    work_name: StringProperty(  # type: ignore[valid-type]
        name="作品名",
        default="",
    )
    episode_number: IntProperty(  # type: ignore[valid-type]
        name="話数",
        default=1,
        min=0,
        soft_max=9999,
    )
    subtitle: StringProperty(  # type: ignore[valid-type]
        name="サブタイトル",
        default="",
    )
    author: StringProperty(  # type: ignore[valid-type]
        name="作者名",
        default="",
    )

    display_work_name: PointerProperty(type=BNameDisplayItem)  # type: ignore[valid-type]
    display_episode: PointerProperty(type=BNameDisplayItem)  # type: ignore[valid-type]
    display_subtitle: PointerProperty(type=BNameDisplayItem)  # type: ignore[valid-type]
    display_author: PointerProperty(type=BNameDisplayItem)  # type: ignore[valid-type]
    # 「原稿上の表示」のページ番号項目 (旧ノンブルの UI 後継)
    display_page_number: PointerProperty(type=BNameDisplayItem)  # type: ignore[valid-type]
    page_number_start: IntProperty(  # type: ignore[valid-type]
        name="開始番号",
        description="ページ番号表示の開始値 (active_page_index=0 のページに割り当てる番号)",
        default=1,
        min=0,
        soft_max=9999,
    )


class BNameNombre(bpy.types.PropertyGroup):
    """ノンブル (ページ番号) の表示設定.

    既定値は計画書 3.7.3 / 4.6 に従う (I-OTFアンチックStd B / 9.0pt /
    縦 5.00mm / 横 0.00mm / フチ 0.30mm)。
    """

    enabled: BoolProperty(  # type: ignore[valid-type]
        name="ノンブル表示",
        default=True,
    )
    format: StringProperty(  # type: ignore[valid-type]
        name="フォーマット",
        description="プレースホルダ {page} を含む文字列 (例: - {page} -)",
        default="{page}",
    )
    font: StringProperty(  # type: ignore[valid-type]
        name="フォント",
        default="I-OTFアンチックStd B",
    )
    font_size_pt: FloatProperty(  # type: ignore[valid-type]
        name="フォントサイズ (pt)",
        default=9.0,
        min=1.0,
        soft_max=72.0,
    )
    position: EnumProperty(  # type: ignore[valid-type]
        name="位置",
        items=_POSITION_ITEMS,
        default="bottom-center",
    )
    gap_vertical_mm: FloatProperty(  # type: ignore[valid-type]
        name="基本枠との間隔 (縦)",
        default=5.00,
        soft_min=-50.0,
        soft_max=50.0,
    )
    gap_horizontal_mm: FloatProperty(  # type: ignore[valid-type]
        name="基本枠との間隔 (横)",
        default=0.00,
        soft_min=-50.0,
        soft_max=50.0,
    )
    color: FloatVectorProperty(  # type: ignore[valid-type]
        name="色",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
    )
    border_enabled: BoolProperty(  # type: ignore[valid-type]
        name="フチをつける",
        default=False,
    )
    border_width_mm: FloatProperty(  # type: ignore[valid-type]
        name="フチ幅 (mm)",
        default=0.30,
        min=0.0,
        soft_max=5.0,
    )
    border_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="フチ色",
        subtype="COLOR",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
    )
    start_number: IntProperty(  # type: ignore[valid-type]
        name="開始番号",
        default=1,
        min=0,
        soft_max=9999,
    )
    hidden_nombre: BoolProperty(  # type: ignore[valid-type]
        name="隠しノンブル",
        description="ON で原稿外の裁ち落とし外側へ小さく印字",
        default=False,
    )


_CLASSES = (
    # PointerProperty の依存順: 参照先 → 参照元
    BNameDisplayItem,
    BNameWorkInfo,
    BNameNombre,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("work_info registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
