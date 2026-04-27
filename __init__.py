"""B-Name — Blender manga name/storyboard authoring addon.

Entry point. Only register/unregister lives here; everything else belongs
to submodules. Registration order follows plan 5.3 to avoid circular imports
and PropertyGroup forward-reference issues.
"""

from __future__ import annotations

# Kept for legacy compatibility only. blender_manifest.toml is authoritative.
bl_info = {
    "name": "B-Name",
    "author": "B-Name Project",
    "version": (0, 1, 0),
    "blender": (4, 3, 0),
    "description": "Blender manga name/storyboard authoring addon",
    "category": "Paint",
}

from . import utils
from . import preferences
from . import core
from . import io
from . import typography
from . import operators
from . import panels
from . import ui
from . import keymap
from .utils import log


def _reload_all_submodules() -> None:
    """VSCode の "Blender: Reload Addons" 時に子モジュールも再 import する.

    Blender のアドオン re-enable は `__init__.py` の register/unregister は
    呼び直すが、子モジュール (utils.gpencil, ui.overlay, operators.* 等) は
    `sys.modules` キャッシュが残ったまま使われる。これにより「コードを修正
    したのに反映されない」現象が起きる。register 前にここで importlib.reload
    で全 B-Name サブモジュールを再ロードすると、ファイル変更が即時反映される。

    PropertyGroup の size 変更や Scene attach の型変更は reload では反映
    されないため、それらの変更時は Blender 自体の再起動が依然必要。
    """
    import importlib
    import sys

    pkg_prefix = (__package__ or "b_name") + "."
    targets = sorted(
        (name for name in list(sys.modules) if name.startswith(pkg_prefix)),
        key=lambda n: n.count("."),  # 深い順は後で
        reverse=True,
    )
    for name in targets:
        mod = sys.modules.get(name)
        if mod is None:
            continue
        try:
            importlib.reload(mod)
        except Exception:  # noqa: BLE001
            pass

# utils は下層ライブラリ扱いで計画書 5.3 の 1-8 には含めない。
# ログハンドラを最初に付け、最後に外すため register/unregister で別扱い。
_MODULES = (
    preferences,
    core,
    io,
    typography,
    operators,
    panels,
    ui,
    keymap,
)


def register() -> None:
    # VSCode Blender Development 拡張の "Reload Addons" でも子モジュールを
    # 確実に再 import する (sys.modules キャッシュ対策)
    _reload_all_submodules()
    utils.register()
    logger = log.get_logger(__name__)
    logger.info("B-Name: register start")
    registered: list = []
    try:
        for module in _MODULES:
            module.register()
            registered.append(module)
        try:
            utils.handlers.schedule_current_file_sync()
        except Exception:
            logger.exception("current B-Name file sync scheduling failed")
    except Exception:
        logger.exception("register failed; rolling back")
        for module in reversed(registered):
            try:
                module.unregister()
            except Exception:
                logger.exception("rollback unregister failed for %s", module.__name__)
        utils.unregister()
        raise
    logger.info("B-Name: register done")


def unregister() -> None:
    logger = log.get_logger(__name__)
    logger.info("B-Name: unregister start")
    for module in reversed(_MODULES):
        try:
            module.unregister()
        except Exception:
            logger.exception("unregister failed for %s", module.__name__)
    logger.info("B-Name: unregister done")
    utils.unregister()
