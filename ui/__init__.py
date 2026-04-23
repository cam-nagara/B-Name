"""ui — gpu オーバーレイ描画とカスタムコンテキストメニュー/ポップアップ.

Phase 1-E: overlay (draw_handler_add) と overlay_shared (Pillow 共用ロジック) を提供。
"""

from __future__ import annotations

from . import overlay, overlay_shared  # noqa: F401

_MODULES = (overlay,)


def register() -> None:
    for module in _MODULES:
        module.register()


def unregister() -> None:
    for module in reversed(_MODULES):
        try:
            module.unregister()
        except Exception:
            pass
