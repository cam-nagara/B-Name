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


class BNameTextFontSpan(bpy.types.PropertyGroup):
    """本文内の一部範囲に適用するフォント指定."""

    start: IntProperty(name="開始", default=0, min=0)  # type: ignore[valid-type]
    length: IntProperty(name="長さ", default=1, min=1)  # type: ignore[valid-type]
    font: StringProperty(name="フォント", default="", subtype="FILE_PATH")  # type: ignore[valid-type]


class BNameTextStyleSpan(bpy.types.PropertyGroup):
    """本文内の一部範囲に適用する文字スタイル."""

    start: IntProperty(name="開始", default=0, min=0)  # type: ignore[valid-type]
    length: IntProperty(name="長さ", default=1, min=1)  # type: ignore[valid-type]
    font: StringProperty(name="フォント", default="", subtype="FILE_PATH")  # type: ignore[valid-type]
    font_size_q: FloatProperty(name="サイズ (Q)", default=20.0, min=1.0, soft_max=200.0)  # type: ignore[valid-type]
    color: FloatVectorProperty(subtype="COLOR", size=4, default=(0.0, 0.0, 0.0, 1.0), min=0.0, max=1.0)  # type: ignore[valid-type]
    font_bold: BoolProperty(name="太字", default=False)  # type: ignore[valid-type]
    font_italic: BoolProperty(name="斜体", default=False)  # type: ignore[valid-type]


class BNameTextEntry(bpy.types.PropertyGroup):
    """1 つのテキストオブジェクト.

    Phase 3 以降、テキストはページ単位 (``BNamePageEntry.texts``) で保持し、
    ``parent_balloon_id`` 経由でフキダシと親子連動する (フキダシ移動で子
    テキストも同じ delta で動く)。
    """

    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    body: StringProperty(name="本文", default="", options={"TEXTEDIT_UPDATE"})  # type: ignore[valid-type]

    # ページローカル座標 (mm). overlay 描画時にページ grid offset を加算する。
    x_mm: FloatProperty(name="X", default=0.0)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y", default=0.0)  # type: ignore[valid-type]
    width_mm: FloatProperty(name="幅", default=30.0, min=0.1)  # type: ignore[valid-type]
    height_mm: FloatProperty(name="高さ", default=15.0, min=0.1)  # type: ignore[valid-type]

    # 親フキダシ (同一ページの BNameBalloonEntry.id を参照). 空文字なら独立テキスト。
    parent_balloon_id: StringProperty(  # type: ignore[valid-type]
        name="親フキダシ ID",
        description="同じページの BNameBalloonEntry.id を参照。空で独立テキスト。",
        default="",
    )
    speaker_type: EnumProperty(  # type: ignore[valid-type]
        name="セリフ種別",
        items=_SPEAKER_TYPE_ITEMS,
        default="normal",
    )
    speaker_name: StringProperty(name="話者", default="")  # type: ignore[valid-type]

    font: StringProperty(name="基本フォント", default="", subtype="FILE_PATH")  # type: ignore[valid-type]
    font_size_q: FloatProperty(  # type: ignore[valid-type]
        name="サイズ (Q)",
        description="文字サイズを Q 数 (1 Q = 0.25 mm) で指定",
        default=20.0,
        min=1.0,
        soft_max=200.0,
    )
    # 旧データ互換用。UI/保存/組版は font_size_q を使う。
    font_size_pt: FloatProperty(name="サイズ (pt)", default=9.0, min=1.0, soft_max=72.0)  # type: ignore[valid-type]
    # Meldex の fontBold / fontItalic 相当
    font_bold: BoolProperty(name="太字", default=False)  # type: ignore[valid-type]
    font_italic: BoolProperty(name="斜体", default=False)  # type: ignore[valid-type]
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

    # 部分フォント。font が空の範囲は基本フォントに戻す扱い。
    font_spans: CollectionProperty(type=BNameTextFontSpan)  # type: ignore[valid-type]

    # 部分スタイル。font が空の範囲は基本フォントに戻す扱い。
    style_spans: CollectionProperty(type=BNameTextStyleSpan)  # type: ignore[valid-type]

    # 縦中横 (horizontal-in-vertical): 指定した範囲を縦書き内で横向きに
    tatechuyoko_ranges: CollectionProperty(type=BNameRubySpan)  # type: ignore[valid-type]


_CLASSES = (
    BNameRubySpan,
    BNameTextFontSpan,
    BNameTextStyleSpan,
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
