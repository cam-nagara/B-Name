"""Blender リンク参照 / サブプロセス起動ヘルパ.

計画書 3.4.5 / 8.7 / 8.12 参照。「リンク元ファイルを開く」を
新しい Blender インスタンスの subprocess 起動で実装する。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Iterable

import bpy

from ..utils import log

_logger = log.get_logger(__name__)


def open_in_new_blender(blend_path: Path) -> subprocess.Popen | None:
    """新しい Blender プロセスで .blend ファイルを開く.

    Windows ではコンソール窓を抑制する CREATE_NO_WINDOW フラグを付ける。
    macOS / Linux は ``bpy.app.binary_path`` を直接実行すれば OK。
    """
    blend_path = Path(blend_path)
    if not blend_path.is_file():
        _logger.warning("blend file not found: %s", blend_path)
        return None
    kwargs: dict = {"close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            [bpy.app.binary_path, str(blend_path)],
            **kwargs,
        )
        _logger.info("spawned blender: pid=%s %s", proc.pid, blend_path)
        return proc
    except OSError as exc:
        _logger.error("failed to spawn blender: %s", exc)
        return None


def find_linked_filepaths(obj: bpy.types.Object) -> Iterable[Path]:
    """Blender Object の library リンク元 filepath 候補を返す."""
    candidates: list[Path] = []
    lib = getattr(obj, "library", None)
    if lib is not None and lib.filepath:
        # bpy では "//path/to/file.blend" 形式で相対パスを返す場合あり
        abs_path = bpy.path.abspath(lib.filepath)
        candidates.append(Path(abs_path))
    # リンク参照された mesh/material 側
    data = getattr(obj, "data", None)
    if data is not None:
        data_lib = getattr(data, "library", None)
        if data_lib is not None and data_lib.filepath:
            abs_path = bpy.path.abspath(data_lib.filepath)
            candidates.append(Path(abs_path))
    return candidates
