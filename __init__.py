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
    utils.register()
    logger = log.get_logger(__name__)
    logger.info("B-Name: register start")
    registered: list = []
    try:
        for module in _MODULES:
            module.register()
            registered.append(module)
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
