"""OS 差異吸収ユーティリティ.

Windows / Linux では Ctrl、macOS では Cmd(OSキー) を主修飾キーとして使う
ケースが多い。キーマップ登録で `ctrl=True` / `oskey=True` を切り替える
ためのヘルパを提供する。
"""

from __future__ import annotations

import sys
from typing import TypedDict


class ModifierFlags(TypedDict, total=False):
    ctrl: bool
    shift: bool
    alt: bool
    oskey: bool


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return sys.platform == "win32"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def platform_ctrl(shift: bool = False, alt: bool = False) -> ModifierFlags:
    """プラットフォーム既定の「Ctrl 相当」修飾を返す.

    macOS では Cmd(oskey) を主修飾に、Win/Linux では Ctrl を主修飾にする。
    """
    flags: ModifierFlags = {"shift": shift, "alt": alt}
    if is_macos():
        flags["oskey"] = True
    else:
        flags["ctrl"] = True
    return flags
