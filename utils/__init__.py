"""Blender API と Python 標準ライブラリのみに依存するヘルパー群。"""

from . import log


def register() -> None:
    log.register()


def unregister() -> None:
    log.unregister()
