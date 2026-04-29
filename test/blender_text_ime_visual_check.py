"""Blender 実機用: テキストツール IME インライン表示の目視確認."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(os.environ.get("BNAME_TEXT_IME_VISUAL_OUT", "") or tempfile.mkdtemp(prefix="bname_text_ime_visual_"))
_MOD = None
_TEMP_ROOT: Path | None = None
_FAKE_OP = None
_PAGE_ID = ""
_TEXT_ID = ""
_STATE: dict[str, object] = {}
_COMMITTED = False


class _InlineTextOpProbe:
    """overlay_text が読む最小限のテキストツール状態."""

    pass


class _Event:
    def __init__(self, event_type: str, value: str = "PRESS") -> None:
        self.type = event_type
        self.value = value


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


def _screenshot(name: str) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(path), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"screenshot failed: {result}")
    return str(path)


def _write_state() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "state.json").write_text(
        json.dumps(_STATE, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _tag_view3d_redraw() -> None:
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in getattr(wm, "windows", []):
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _view3d_override() -> dict[str, object]:
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return {}
    for window in getattr(wm, "windows", []):
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None:
                continue
            return {"window": window, "screen": screen, "area": area, "region": region}
    return {}


def _current_entry():
    work = getattr(bpy.context.scene, "bname_work", None)
    if work is None:
        return None
    for page in getattr(work, "pages", []):
        if str(getattr(page, "id", "") or "") != _PAGE_ID:
            continue
        for entry in getattr(page, "texts", []):
            if str(getattr(entry, "id", "") or "") == _TEXT_ID:
                return entry
    return None


def _create_work_scene() -> None:
    global _MOD, _TEMP_ROOT
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _MOD = _load_addon()
    _TEMP_ROOT = Path(tempfile.mkdtemp(prefix="bname_text_ime_work_"))
    override = _view3d_override()
    if override:
        with bpy.context.temp_override(**override):
            result = bpy.ops.bname.work_new(filepath=str(_TEMP_ROOT / "Text_IME_Visual.bname"))
    else:
        result = bpy.ops.bname.work_new(filepath=str(_TEMP_ROOT / "Text_IME_Visual.bname"))
    if "FINISHED" not in result:
        raise RuntimeError(f"work_new failed: {result}")


def _install_text_probe() -> None:
    global _FAKE_OP, _PAGE_ID, _TEXT_ID
    work = bpy.context.scene.bname_work
    if work is None or len(work.pages) == 0:
        raise RuntimeError("B-Name work/page is not ready")
    page = work.pages[0]
    page.texts.clear()
    entry = page.texts.add()
    entry.id = "text_ime_visual"
    entry.body = "ABC"
    entry.x_mm = 38.0
    entry.y_mm = 166.0
    entry.width_mm = 182.0
    entry.height_mm = 42.0
    entry.writing_mode = "horizontal"
    entry.font_size_q = 48.0
    page.active_text_index = 0
    work.active_page_index = 0
    _PAGE_ID = str(page.id)
    _TEXT_ID = str(entry.id)

    from bname_dev.io import page_io
    from bname_dev.operators import coma_modal_state, text_edit_runtime, text_op

    page_io.save_page_json(Path(work.work_dir), page)
    page_io.save_pages_json(Path(work.work_dir), work)

    text_edit_runtime._clear_ime_text_queue()
    text_edit_runtime.begin_ime_capture()
    text_edit_runtime._set_ime_composition_text("日本", active=True)

    op = _InlineTextOpProbe()
    op._editing = True
    op._page_id = page.id
    op._text_id = entry.id
    op._cursor_index = len(entry.body)
    op._selection_anchor = -1
    _FAKE_OP = op
    coma_modal_state.set_active("text_tool", op, bpy.context)

    preview, caret, bounds = text_edit_runtime.preview_entry_with_composition(
        entry,
        op._cursor_index,
        op._selection_anchor,
    )
    _STATE.update(
        {
            "body_during_composition": entry.body,
            "composition_text": text_edit_runtime.ime_composition_text(),
            "composition_active": text_edit_runtime.ime_composition_active(),
            "composition_preview_body": preview.body,
            "composition_preview_caret": caret,
            "composition_bounds": list(bounds) if bounds is not None else None,
            "pass_ret_to_ime": text_op._event_should_pass_to_ime(_Event("RET")),
            "pass_textinput_to_ime": text_op._event_should_pass_to_ime(_Event("TEXTINPUT", "NOTHING")),
            "block_mouse_to_ime": not text_op._event_should_pass_to_ime(_Event("LEFTMOUSE")),
            "ime_capture_hwnd_present": bool(getattr(text_edit_runtime, "_IME_CAPTURE_HWND", None)),
        }
    )

    try:
        override = _view3d_override()
        if override:
            with bpy.context.temp_override(**override):
                bpy.ops.bname.view_fit_page()
        else:
            bpy.ops.bname.view_fit_page()
    except Exception:
        pass
    _tag_view3d_redraw()
    _write_state()


def _setup_scene() -> None:
    _create_work_scene()
    _install_text_probe()


def _capture_composition():
    _STATE["composition_screenshot"] = _screenshot("text_ime_composition.png")
    _write_state()
    return None


def _commit_text() -> None:
    global _COMMITTED
    if _COMMITTED:
        return
    try:
        from bname_dev.operators import text_edit_runtime

        entry = _current_entry()
        assert entry is not None
        queued_cursor = text_edit_runtime.replace_selection(entry, len(entry.body), -1, "日本語")
        text_edit_runtime._clear_ime_text_queue()
        if _FAKE_OP is not None:
            _FAKE_OP._cursor_index = queued_cursor
            _FAKE_OP._selection_anchor = -1
        _STATE["body_after_commit"] = entry.body
        _STATE["commit_cursor"] = queued_cursor
        _COMMITTED = True
        _write_state()
        _tag_view3d_redraw()
    except Exception as exc:  # noqa: BLE001
        _STATE["commit_error"] = repr(exc)
        _write_state()


def _capture_commit():
    _commit_text()
    _STATE["commit_screenshot"] = _screenshot("text_ime_committed.png")
    _write_state()
    print("BNAME_TEXT_IME_VISUAL_OK")
    print(json.dumps(_STATE, ensure_ascii=False, sort_keys=True))
    _cleanup()
    os._exit(0)
    return None


def _external_commit():
    _commit_text()
    return None


def _cleanup() -> None:
    try:
        from bname_dev.operators import text_edit_runtime

        text_edit_runtime.end_ime_capture()
    except Exception:
        pass
    if _MOD is not None:
        try:
            _MOD.unregister()
        except Exception:
            pass
    if _TEMP_ROOT is not None:
        shutil.rmtree(_TEMP_ROOT, ignore_errors=True)


def _external_quit():
    _cleanup()
    os._exit(0)


def _external_setup_tick():
    if not _view3d_override():
        return 0.25
    try:
        _create_work_scene()
    except Exception:  # noqa: BLE001
        _STATE["setup_error"] = traceback.format_exc()
        _write_state()
        traceback.print_exc()
        _cleanup()
        os._exit(1)
    bpy.app.timers.register(_external_probe_tick, first_interval=1.5)
    return None


def _external_probe_tick():
    try:
        _install_text_probe()
    except Exception:  # noqa: BLE001
        _STATE["probe_error"] = traceback.format_exc()
        _write_state()
        traceback.print_exc()
        _cleanup()
        os._exit(1)
    bpy.app.timers.register(_external_commit, first_interval=7.0)
    bpy.app.timers.register(_external_quit, first_interval=14.0)
    return None


def main() -> None:
    if os.environ.get("BNAME_TEXT_IME_VISUAL_EXTERNAL"):
        bpy.app.timers.register(_external_setup_tick, first_interval=0.25)
        return
    _setup_scene()
    bpy.app.timers.register(_capture_composition, first_interval=0.8)
    bpy.app.timers.register(_capture_commit, first_interval=1.4)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        _STATE["setup_error"] = traceback.format_exc()
        try:
            _write_state()
        finally:
            traceback.print_exc()
            _cleanup()
            os._exit(1)
