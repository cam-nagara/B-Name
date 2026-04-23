"""紙面編集モード / コマ編集モードの状態管理 (計画書 3.4 参照).

Scene.bname_mode に現在のモード文字列を保持。切り替えは operators の
bname.mode_toggle で行い、描画ハンドラ側 (ui/overlay.py) がモードを
見て紙面 / 個別コマのどちらを描くかを判定する。

Phase 2 段階では状態保持のみ。実際の Scene 差し替え・3D シーン切替は
Phase 4 (3D 連携) で実装する。
"""

from __future__ import annotations

import bpy
from bpy.props import EnumProperty, StringProperty

from ..utils import log

_logger = log.get_logger(__name__)

MODE_PAGE = "PAGE"
MODE_PANEL = "PANEL"

_MODE_ITEMS = (
    (MODE_PAGE, "紙面編集", "原稿用紙全体を編集するモード"),
    (MODE_PANEL, "コマ編集", "選択中のコマの 3D シーンを編集するモード"),
)


def register() -> None:
    bpy.types.Scene.bname_mode = EnumProperty(
        name="B-Name モード",
        items=_MODE_ITEMS,
        default=MODE_PAGE,
    )
    bpy.types.Scene.bname_current_panel_stem = StringProperty(
        name="現在編集中のコマ stem",
        default="",
    )
    _logger.debug("mode registered")


def unregister() -> None:
    try:
        del bpy.types.Scene.bname_mode
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bname_current_panel_stem
    except AttributeError:
        pass


def get_mode(context: bpy.types.Context | None = None) -> str:
    ctx = context or bpy.context
    scene = getattr(ctx, "scene", None)
    if scene is None:
        return MODE_PAGE
    return getattr(scene, "bname_mode", MODE_PAGE)


def set_mode(mode: str, context: bpy.types.Context | None = None) -> None:
    ctx = context or bpy.context
    scene = getattr(ctx, "scene", None)
    if scene is None:
        return
    if mode not in (MODE_PAGE, MODE_PANEL):
        raise ValueError(f"invalid mode: {mode}")
    scene.bname_mode = mode
