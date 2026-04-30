"""Blender API と Python 標準ライブラリのみに依存するヘルパー群。"""

from . import python_deps

python_deps.ensure_bundled_wheels_on_path()

from . import handlers, log, page_grid  # noqa: E402,F401 — page_grid はヘルパのみ
from . import object_naming, outliner_model, layer_object_sync  # noqa: E402,F401
from . import (  # noqa: E402,F401
    active_target,
    balloon_curve_object,
    effect_line_object,
    empty_layer_object,
    gp_object_layer,
    mask_apply,
    mask_object,
    outliner_watch,
)


def register() -> None:
    log.register()
    handlers.register()
    outliner_watch.register()


def unregister() -> None:
    outliner_watch.unregister()
    handlers.unregister()
    try:
        layer_object_sync.clear_snapshots()
    except Exception:  # noqa: BLE001
        pass
    log.unregister()
