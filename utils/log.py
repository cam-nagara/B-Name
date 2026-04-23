"""B-Name ログ基盤.

全モジュールが ``log.get_logger(__name__)`` で取得する。Blender 本体の
stdout にハンドラ 1 本のみで出力し、多重登録を防ぐためアドオンルート
(``b_name``) にのみハンドラを付ける。
"""

from __future__ import annotations

import logging
import os

ROOT_NAME = "b_name"
_DEFAULT_FORMAT = "[B-Name][%(levelname)s][%(name)s] %(message)s"
_HANDLER_ATTR = "_b_name_handler"


def _parse_level(value: str | None, fallback: int = logging.INFO) -> int:
    if not value:
        return fallback
    if value.isdigit():
        return int(value)
    level = logging.getLevelName(value.upper())
    return level if isinstance(level, int) else fallback


def get_logger(name: str | None = None) -> logging.Logger:
    """アドオンルート配下のロガーを返す.

    引数の name がアドオン配下（``b_name``/``B-Name.*`` 等）でない場合は、
    そのまま末端名としてルート配下にぶら下げる。
    """

    if not name or name == ROOT_NAME:
        return logging.getLogger(ROOT_NAME)
    # パッケージ内呼び出し（例: "B-Name.utils.log"）の末尾だけ利用する
    short = name.rsplit(".", 1)[-1]
    return logging.getLogger(f"{ROOT_NAME}.{short}")


def _ensure_handler(level: int) -> None:
    root = logging.getLogger(ROOT_NAME)
    root.setLevel(level)
    root.propagate = False
    existing = getattr(root, _HANDLER_ATTR, None)
    if existing is not None:
        existing.setLevel(level)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    handler.setLevel(level)
    root.addHandler(handler)
    setattr(root, _HANDLER_ATTR, handler)


def set_level(level: int | str) -> None:
    if isinstance(level, str):
        level = _parse_level(level)
    root = logging.getLogger(ROOT_NAME)
    root.setLevel(level)
    handler = getattr(root, _HANDLER_ATTR, None)
    if handler is not None:
        handler.setLevel(level)


def register() -> None:
    level = _parse_level(os.environ.get("B_NAME_LOG_LEVEL"))
    _ensure_handler(level)
    get_logger(__name__).debug("log registered (level=%s)", logging.getLevelName(level))


def unregister() -> None:
    root = logging.getLogger(ROOT_NAME)
    handler = getattr(root, _HANDLER_ATTR, None)
    if handler is not None:
        root.removeHandler(handler)
        try:
            delattr(root, _HANDLER_ATTR)
        except AttributeError:
            pass
