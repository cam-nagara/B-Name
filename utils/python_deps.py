"""同梱 Python wheel の読み込み補助.

Blender Extension としてインストールされた場合は manifest の wheels 指定で
Blender 側が処理するが、開発中にアドオンフォルダを直接 import した場合は
binary wheel を自前で展開して sys.path に追加する必要がある。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import sysconfig
import zipfile

_READY = False
_EXTRACT_DIR_NAME = "_installed"
_DLL_DIR_HANDLES: list[object] = []


def ensure_bundled_wheels_on_path() -> None:
    """現在の Python と互換性がある同梱 wheel を import 可能にする."""
    global _READY
    if _READY:
        return
    wheels_dir = _wheels_dir()
    if not wheels_dir.is_dir():
        _READY = True
        return
    for wheel in sorted(wheels_dir.glob("*.whl")):
        if not _wheel_matches_runtime(wheel.name):
            continue
        install_dir = _ensure_wheel_extracted(wheel)
        if install_dir is None:
            continue
        _prepend_sys_path(install_dir)
        _add_dll_dirs(install_dir)
    _READY = True


def _wheels_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "wheels"


def _wheel_matches_runtime(filename: str) -> bool:
    tags = _parse_wheel_tags(filename)
    if tags is None:
        return False
    py_tag, abi_tag, platform_tag = tags
    if py_tag.startswith("py3") and abi_tag == "none" and platform_tag == "any":
        return True
    runtime_py = f"cp{sys.version_info.major}{sys.version_info.minor}"
    if py_tag != runtime_py:
        return False
    if abi_tag not in (runtime_py, "abi3"):
        return False
    return platform_tag in _runtime_platform_tags()


def _parse_wheel_tags(filename: str) -> tuple[str, str, str] | None:
    if not filename.endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return None
    return parts[-3], parts[-2], parts[-1]


def _runtime_platform_tags() -> set[str]:
    tags = {"any"}
    platform_name = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    tags.add(platform_name)
    if platform_name == "win_amd64":
        tags.add("win_amd64")
    if platform_name == "macosx_11_0_arm64":
        tags.add("macosx_11_0_arm64")
    if platform_name.startswith("linux_"):
        tags.add(platform_name)
        tags.add("manylinux2014_x86_64")
        tags.add("manylinux_2_17_x86_64")
    return tags


def _ensure_wheel_extracted(wheel: Path) -> Path | None:
    target = wheel.parent / _EXTRACT_DIR_NAME / wheel.stem
    marker = target / ".bname-wheel.json"
    fingerprint = {
        "wheel": wheel.name,
        "size": wheel.stat().st_size,
        "mtime_ns": wheel.stat().st_mtime_ns,
    }
    if _marker_matches(marker, fingerprint):
        return target
    try:
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(wheel) as archive:
            _extract_wheel_safely(archive, target)
        marker.write_text(json.dumps(fingerprint, sort_keys=True), encoding="utf-8")
        return target
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        return None


def _marker_matches(marker: Path, fingerprint: dict[str, object]) -> bool:
    try:
        return json.loads(marker.read_text(encoding="utf-8")) == fingerprint
    except Exception:
        return False


def _extract_wheel_safely(archive: zipfile.ZipFile, target: Path) -> None:
    root = target.resolve()
    for info in archive.infolist():
        out = (target / info.filename).resolve()
        try:
            out.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"unsafe wheel member: {info.filename!r}") from exc
    archive.extractall(target)


def _prepend_sys_path(path: Path) -> None:
    text = str(path)
    if text in sys.path:
        return
    sys.path.insert(0, text)


def _add_dll_dirs(install_dir: Path) -> None:
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    for child in install_dir.iterdir():
        if child.is_dir() and child.name.endswith(".libs"):
            try:
                _DLL_DIR_HANDLES.append(os.add_dll_directory(str(child)))
            except OSError:
                pass
