"""ページエントリの PropertyGroup.

pages.json の各ページ要素に対応する軽量データモデル。コマ情報本体は
Phase 2 で追加される panel.py に分離し、ここではページ単位の識別と
見開き情報のみ保持する。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)

from ..utils import log
from .panel import BNamePanelEntry

_logger = log.get_logger(__name__)


class BNameOriginalPageRef(bpy.types.PropertyGroup):
    """見開きページ結合時の結合元ページ ID を保持する要素."""

    page_id: StringProperty(  # type: ignore[valid-type]
        name="ページ ID",
        default="",
    )


class BNamePageEntry(bpy.types.PropertyGroup):
    """pages.json の 1 エントリに対応."""

    # --- 識別子 ---
    id: StringProperty(  # type: ignore[valid-type]
        name="ページ ID",
        description="pages/NNNN/ の NNNN または NNNN-MMMM (見開き)",
        default="",
    )
    title: StringProperty(  # type: ignore[valid-type]
        name="表示名",
        default="",
    )
    dir_rel: StringProperty(  # type: ignore[valid-type]
        name="格納ディレクトリ (相対)",
        description="作品ルートからの相対パス (例: pages/0001/)",
        default="",
    )

    # --- 見開き情報 ---
    spread: BoolProperty(  # type: ignore[valid-type]
        name="見開き",
        description="2 ページを結合した見開きエントリか",
        default=False,
    )
    original_pages: CollectionProperty(  # type: ignore[valid-type]
        name="結合元ページ",
        type=BNameOriginalPageRef,
    )
    tombo_aligned: BoolProperty(  # type: ignore[valid-type]
        name="トンボを合わせる",
        default=True,
    )
    tombo_gap_mm: FloatProperty(  # type: ignore[valid-type]
        name="トンボ間隔 (mm)",
        description="負値はページを重ねる方向",
        default=-9.60,
        soft_min=-100.0,
        soft_max=100.0,
    )

    # --- キャッシュ情報 ---
    thumbnail_rel: StringProperty(  # type: ignore[valid-type]
        name="ページサムネイル (相対パス)",
        default="",
    )
    panel_count: IntProperty(  # type: ignore[valid-type]
        name="コマ数",
        default=0,
        min=0,
    )

    # --- コマ一覧 ---
    panels: CollectionProperty(type=BNamePanelEntry)  # type: ignore[valid-type]
    active_panel_index: IntProperty(  # type: ignore[valid-type]
        name="アクティブコマ",
        default=-1,
        min=-1,
    )


_CLASSES = (
    BNameOriginalPageRef,
    BNamePageEntry,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("page registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
