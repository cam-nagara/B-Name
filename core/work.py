"""作品データの集約 PropertyGroup.

work.json 全体を Blender 内で保持する root コンテナ。Scene.bname_work に
PointerProperty で attach する。

依存順: 参照先 (paper / work_info / safe_area_overlay / page) を先に
register しておくこと。core/__init__.py が順序を保証する。
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..utils import log
from .balloon import BNameBalloonEntry
from .coma import BNameComaEntry
from .page import BNamePageEntry
from .paper import BNamePaperSettings
from .safe_area_overlay import BNameSafeAreaOverlay
from .text_entry import BNameTextEntry
from .work_info import BNameNombre, BNameWorkInfo

_logger = log.get_logger(__name__)


class BNameComaGap(bpy.types.PropertyGroup):
    """コマ間隔ルール (作品共通、計画書 3.2.5.4).

    既定値: 上下 7.3mm / 左右 2.1mm。値は mm 単位。
    Blender のシーン単位に依存しないよう unit は明示せず、UI 表示でも
    名前に "(mm)" を含めて単位を明示する。
    """

    vertical_mm: FloatProperty(  # type: ignore[valid-type]
        name="上下スキマ (mm)",
        default=7.3,
        min=0.0,
        soft_max=50.0,
    )
    horizontal_mm: FloatProperty(  # type: ignore[valid-type]
        name="左右スキマ (mm)",
        default=2.1,
        min=0.0,
        soft_max=50.0,
    )


class BNameWorkData(bpy.types.PropertyGroup):
    """作品 1 件分のデータ (.bname フォルダ 1 個分)."""

    # --- メタ ---
    loaded: BoolProperty(  # type: ignore[valid-type]
        name="作品ロード済み",
        default=False,
    )
    work_dir: StringProperty(  # type: ignore[valid-type]
        name="作品ディレクトリ",
        description="MyWork.bname/ のフルパス",
        default="",
        subtype="DIR_PATH",
    )
    coma_blend_template_path: StringProperty(  # type: ignore[valid-type]
        name="コマblendテンプレート",
        description=(
            "新規 cNN.blend 作成時に初回コピーする .blend。"
            "空ならB-Name標準の空コマシーンを作成"
        ),
        default="",
        subtype="FILE_PATH",
    )

    # --- 各セクション ---
    work_info: PointerProperty(type=BNameWorkInfo)  # type: ignore[valid-type]
    nombre: PointerProperty(type=BNameNombre)  # type: ignore[valid-type]
    paper: PointerProperty(type=BNamePaperSettings)  # type: ignore[valid-type]
    safe_area_overlay: PointerProperty(type=BNameSafeAreaOverlay)  # type: ignore[valid-type]
    coma_gap: PointerProperty(type=BNameComaGap)  # type: ignore[valid-type]

    # --- ページ一覧 ---
    pages: CollectionProperty(type=BNamePageEntry)  # type: ignore[valid-type]
    active_page_index: IntProperty(  # type: ignore[valid-type]
        name="アクティブページ",
        default=-1,
        min=-1,
    )

    # --- ページ外レイヤー ---
    shared_balloons: CollectionProperty(type=BNameBalloonEntry)  # type: ignore[valid-type]
    shared_texts: CollectionProperty(type=BNameTextEntry)  # type: ignore[valid-type]
    shared_comas: CollectionProperty(type=BNameComaEntry)  # type: ignore[valid-type]


# ----- Scene attach ヘルパ -----


def get_work(context: bpy.types.Context | None = None) -> BNameWorkData | None:
    """現在のシーンに紐づく BNameWorkData を返す.

    Scene に PointerProperty が attach されていなければ None。
    """
    ctx = context or bpy.context
    scene = getattr(ctx, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "bname_work", None)


def get_active_page(context: bpy.types.Context | None = None) -> BNamePageEntry | None:
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    idx = work.active_page_index
    if idx < 0 or idx >= len(work.pages):
        return None
    return work.pages[idx]


def find_page_by_id(work: BNameWorkData | None, page_id: str) -> BNamePageEntry | None:
    if work is None or not work.loaded or not page_id:
        return None
    for page in work.pages:
        if getattr(page, "id", "") == page_id:
            return page
    return None


_CLASSES = (
    BNameComaGap,
    BNameWorkData,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bname_work = PointerProperty(type=BNameWorkData)
    _logger.debug("work registered")


def unregister() -> None:
    try:
        del bpy.types.Scene.bname_work
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
