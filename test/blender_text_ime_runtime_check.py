"""Blender 実機用: テキストツールのIMEランタイム確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


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


def _new_text_entry(work_dir: Path):
    result = bpy.ops.bname.work_new(filepath=str(work_dir))
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bname_work
    page = work.pages[0]
    entry = page.texts.add()
    entry.id = "text_ime_check"
    entry.body = "abcdef"
    entry.x_mm = 0.0
    entry.y_mm = 0.0
    entry.width_mm = 40.0
    entry.height_mm = 20.0
    entry.writing_mode = "horizontal"
    entry.font_size_q = 20.0
    return entry


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_text_ime_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        entry = _new_text_entry(temp_root / "Text_IME.bname")

        from bname_dev.operators import text_edit_runtime, text_op

        class Event:
            def __init__(self, event_type: str, value: str = "PRESS") -> None:
                self.type = event_type
                self.value = value

        text_edit_runtime._clear_ime_text_queue()
        assert not text_op._event_should_pass_to_ime(Event("RET"))
        text_edit_runtime._set_ime_composition_text("日本")
        assert text_op._event_should_pass_to_ime(Event("RET"))
        assert text_op._event_should_pass_to_ime(Event("TEXTINPUT", "NOTHING"))
        assert not text_op._event_should_pass_to_ime(Event("LEFTMOUSE"))
        preview, caret, bounds = text_edit_runtime.preview_entry_with_composition(entry, 2, -1)
        assert entry.body == "abcdef"
        assert preview.body == "ab日本cdef"
        assert caret == 4
        assert bounds == (2, 4)
        assert text_edit_runtime.text_body(preview) == "ab日本cdef"
        assert text_edit_runtime.caret_rect(preview, text_edit_runtime.text_rect(entry), caret).width > 0

        entry.body = "日本語"
        entry.writing_mode = "vertical"
        vertical_rect = text_edit_runtime.text_rect(entry)
        vertical_region = text_edit_runtime.text_inner_rect(vertical_rect)
        vertical_em = text_edit_runtime.text_em_mm(entry)
        vertical_caret = text_edit_runtime.caret_rect(entry, vertical_rect, 0)
        from bname_dev.typography import layout as text_layout
        from bname_dev.ui import overlay_text

        layout_result = text_layout.typeset(
            entry,
            vertical_region.x,
            vertical_region.y,
            vertical_region.width,
            vertical_region.height,
        )
        first_glyph = layout_result.placements[0]
        expected_x = vertical_region.x2 - vertical_em * 0.5
        assert abs(vertical_caret.x - expected_x) < 1e-6, (vertical_caret.x, expected_x)
        assert abs(vertical_caret.x - first_glyph.x_mm) < 1e-6, (vertical_caret.x, first_glyph.x_mm)
        selection_rect = overlay_text._selection_rects(entry, vertical_rect, 1, 0)[0]
        assert abs(selection_rect.x - first_glyph.x_mm) < 1e-6, (selection_rect.x, first_glyph.x_mm)

        entry.body = "abcdef"
        entry.writing_mode = "horizontal"

        text_edit_runtime._set_ime_composition_text("語")
        preview, caret, bounds = text_edit_runtime.preview_entry_with_composition(entry, 4, 1)
        assert preview.body == "a語ef"
        assert caret == 2
        assert bounds == (1, 2)

        text_edit_runtime._begin_ime_composition()
        assert text_edit_runtime.ime_composition_active()
        text_edit_runtime._append_ime_text("日本語")
        assert text_edit_runtime.poll_ime_text() == "日本語"
        assert not text_edit_runtime.ime_composition_active()

        entry.body = "abcdef"
        cursor = text_edit_runtime.replace_selection(entry, 3, 1, "日本語")
        assert entry.body == "a日本語def"
        assert cursor == 4

        text_edit_runtime.begin_ime_capture()
        text_edit_runtime.end_ime_capture()
        print("BNAME_TEXT_IME_RUNTIME_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
