"""keymap — B-Name 専用キーマップとビューポート操作モーダルオペレータ.

register 順序は 5.3 に従い最後。依存する Preferences / utils.log は先に
register 済みである前提。
"""

from . import os_compat  # noqa: F401 - 他モジュールからの参照用
from . import viewport_ops
from . import keymap as _keymap


def register() -> None:
    viewport_ops.register()
    _keymap.register()


def unregister() -> None:
    _keymap.unregister()
    viewport_ops.unregister()
