"""テキストエントリ PropertyGroup (フキダシ内テキスト/擬音/ナレーション共通).

計画書 3.1.4.4 / 3.1.5 参照。縦書き・ルビ・縦中横・白フチ・行間/字間を
保持する。実際の組版レンダリングは typography/ が担当する。
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
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)


_WRITING_MODE_ITEMS = (
    ("vertical", "縦書き", ""),
    ("horizontal", "横書き", ""),
)

_SPEAKER_TYPE_ITEMS = (
    ("normal", "通常セリフ", ""),
    ("thought", "思考", ""),
    ("shout", "叫び", ""),
    ("narration", "ナレーション", ""),
    ("monologue", "モノローグ", ""),
    ("sfx", "擬音", ""),
    ("custom", "カスタム", ""),
)


class BNameRubySpan(bpy.types.PropertyGroup):
    """親文字範囲とルビ (フリガナ) を対応付ける."""

    start: IntProperty(name="開始", default=0, min=0)  # type: ignore[valid-type]
    length: IntProperty(name="長さ", default=1, min=1)  # type: ignore[valid-type]
    ruby_text: StringProperty(name="ルビ", default="")  # type: ignore[valid-type]
    # ルビスタイル: monoRuby (1文字1ルビ), groupRuby (親語全体に), jukugoRuby (熟語ルビ)
    style: EnumProperty(  # type: ignore[valid-type]
        name="スタイル",
        items=(
            ("mono", "モノルビ", ""),
            ("group", "グループルビ", ""),
            ("jukugo", "熟語ルビ", ""),
        ),
        default="mono",
    )


class BNameTextEntry(bpy.types.PropertyGroup):
    """1 つのテキストオブジェクト."""

    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    body: StringProperty(name="本文", default="")  # type: ignore[valid-type]
    speaker_type: EnumProperty(  # type: ignore[valid-type]
        name="セリフ種別",
        items=_SPEAKER_TYPE_ITEMS,
        default="normal",
    )
    speaker_name: StringProperty(name="話者", default="")  # type: ignore[valid-type]

    font: StringProperty(name="フォント", default="")  # type: ignore[valid-type]
    font_size_pt: FloatProperty(name="サイズ (pt)", default=9.0, min=1.0, soft_max=72.0)  # type: ignore[valid-type]
    color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0)  # type: ignore[valid-type]
    writing_mode: EnumProperty(items=_WRITING_MODE_ITEMS, default="vertical")  # type: ignore[valid-type]
    line_height: FloatProperty(name="行間", default=1.4, min=0.5, soft_max=3.0)  # type: ignore[valid-type]
    letter_spacing: FloatProperty(name="字間", default=0.0, soft_min=-1.0, soft_max=1.0)  # type: ignore[valid-type]

    # 白フチ (計画書 3.1.4.4)
    stroke_enabled: BoolProperty(name="白フチ", default=False)  # type: ignore[valid-type]
    stroke_width_mm: FloatProperty(name="フチ幅", default=0.2, min=0.0, soft_max=5.0)  # type: ignore[valid-type]
    stroke_color: FloatVectorProperty(subtype="COLOR", size=4, default=(1.0, 1.0, 1.0, 1.0), min=0.0, max=1.0)  # type: ignore[valid-type]

    # ルビ (複数スパン)
    ruby_spans: CollectionProperty(type=BNameRubySpan)  # type: ignore[valid-type]

    # 縦中横 (horizontal-in-vertical): 指定した範囲を縦書き内で横向きに
    tatechuyoko_ranges: CollectionProperty(type=BNameRubySpan)  # type: ignore[valid-type]


_CLASSES = (
    BNameRubySpan,
    BNameTextEntry,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("text_entry registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
