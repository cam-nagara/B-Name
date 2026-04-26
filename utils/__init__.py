"""Blender API と Python 標準ライブラリのみに依存するヘルパー群。"""

from . import python_deps

python_deps.ensure_bundled_wheels_on_path()

from . import handlers, log, page_grid  # noqa: E402,F401 — page_grid はヘルパのみ


def register() -> None:
    log.register()
    handlers.register()


def unregister() -> None:
    handlers.unregister()
    log.unregister()
