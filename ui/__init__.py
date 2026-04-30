"""ui — gpu オーバーレイ描画とカスタムコンテキストメニュー/ポップアップ.

Phase 1-E: overlay (draw_handler_add) と overlay_shared (Pillow 共用ロジック) を提供。
"""

from __future__ import annotations

from . import (  # noqa: F401
    coma_return_header,
    context_menu,
    overlay,
    overlay_shared,
    reparent_overlay,
    sidebar,
)

_MODULES = (overlay, reparent_overlay, context_menu, coma_return_header)


def register() -> None:
    for module in _MODULES:
        module.register()


def unregister() -> None:
    for module in reversed(_MODULES):
        try:
            module.unregister()
        except Exception:
            pass
