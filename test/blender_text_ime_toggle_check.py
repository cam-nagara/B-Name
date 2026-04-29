"""Blender GUI用: 半角/全角キー相当でIME open状態が切り替わるか確認."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import ctypes
from ctypes import wintypes
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(os.environ.get("BNAME_TEXT_IME_TOGGLE_OUT", "") or ".")
_MOD = None
_STATE: dict[str, object] = {}


_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_IMM32 = ctypes.WinDLL("imm32", use_last_error=True)
_USER32.GetFocus.argtypes = []
_USER32.GetFocus.restype = wintypes.HWND
_USER32.GetActiveWindow.argtypes = []
_USER32.GetActiveWindow.restype = wintypes.HWND
_USER32.GetForegroundWindow.argtypes = []
_USER32.GetForegroundWindow.restype = wintypes.HWND
_USER32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_USER32.GetClassNameW.restype = ctypes.c_int
_USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_USER32.GetWindowTextW.restype = ctypes.c_int
_USER32.GetParent.argtypes = [wintypes.HWND]
_USER32.GetParent.restype = wintypes.HWND
_USER32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_USER32.GetWindowThreadProcessId.restype = wintypes.DWORD
_IMM32.ImmGetContext.argtypes = [wintypes.HWND]
_IMM32.ImmGetContext.restype = wintypes.HANDLE
_IMM32.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
_IMM32.ImmReleaseContext.restype = wintypes.BOOL
_IMM32.ImmGetOpenStatus.argtypes = [wintypes.HANDLE]
_IMM32.ImmGetOpenStatus.restype = wintypes.BOOL
_IMM32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
_IMM32.ImmGetDefaultIMEWnd.restype = wintypes.HWND


def _win_text(getter, hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    try:
        getter(wintypes.HWND(hwnd), buffer, len(buffer))
    except Exception:  # noqa: BLE001
        return ""
    return buffer.value


def _hwnd_info(label: str, hwnd) -> dict[str, object]:
    value = int(hwnd or 0)
    info: dict[str, object] = {
        "label": label,
        "hwnd": value,
        "class": "",
        "title": "",
        "parent": 0,
        "thread": 0,
        "process": 0,
        "himc": 0,
        "open_status": None,
        "default_ime_hwnd": 0,
    }
    if not value:
        return info
    info["class"] = _win_text(_USER32.GetClassNameW, value)
    info["title"] = _win_text(_USER32.GetWindowTextW, value)
    info["parent"] = int(_USER32.GetParent(value) or 0)
    pid = wintypes.DWORD(0)
    info["thread"] = int(_USER32.GetWindowThreadProcessId(value, ctypes.byref(pid)) or 0)
    info["process"] = int(pid.value)
    info["default_ime_hwnd"] = int(_IMM32.ImmGetDefaultIMEWnd(value) or 0)
    himc = _IMM32.ImmGetContext(value)
    info["himc"] = int(himc or 0)
    if himc:
        try:
            info["open_status"] = bool(_IMM32.ImmGetOpenStatus(himc))
        finally:
            _IMM32.ImmReleaseContext(value, himc)
    return info


def _ime_debug_state(text_edit_runtime) -> list[dict[str, object]]:
    raw = [
        ("capture", getattr(text_edit_runtime, "_IME_CAPTURE_HWND", 0) or 0),
        ("focus", _USER32.GetFocus()),
        ("active", _USER32.GetActiveWindow()),
        ("foreground", _USER32.GetForegroundWindow()),
    ]
    seen = set()
    result = []
    for label, hwnd in raw:
        value = int(hwnd or 0)
        if value in seen:
            continue
        seen.add(value)
        result.append(_hwnd_info(label, value))
    return result


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _write_state() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "state.json").write_text(
        json.dumps(_STATE, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _has_window() -> bool:
    wm = getattr(bpy.context, "window_manager", None)
    return bool(wm is not None and getattr(wm, "windows", []))


def _setup_tick():
    global _MOD
    if not _has_window():
        return 0.25
    _MOD = _load_addon()
    from bname_dev.operators import text_edit_runtime

    text_edit_runtime.begin_ime_capture()
    text_edit_runtime.set_ime_open_status(False)
    before = text_edit_runtime.ime_open_status()
    _STATE.update(
        {
            "capture_hwnd": int(getattr(text_edit_runtime, "_IME_CAPTURE_HWND", 0) or 0),
            "before": before,
            "debug_before": _ime_debug_state(text_edit_runtime),
        }
    )
    _write_state()
    bpy.app.timers.register(_finish_tick, first_interval=4.0)
    return None


def _finish_tick():
    from bname_dev.operators import text_edit_runtime

    _STATE["after"] = text_edit_runtime.ime_open_status()
    _STATE["debug_after"] = _ime_debug_state(text_edit_runtime)
    text_edit_runtime.end_ime_capture()
    _STATE["capture_after_end"] = int(getattr(text_edit_runtime, "_IME_CAPTURE_HWND", 0) or 0)
    _STATE["debug_after_end"] = _ime_debug_state(text_edit_runtime)
    _write_state()
    if _MOD is not None:
        _MOD.unregister()
    os._exit(0)


bpy.app.timers.register(_setup_tick, first_interval=0.25)
