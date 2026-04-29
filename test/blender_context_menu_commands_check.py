"""Blender 実機用: B-Name 右クリックメニュー項目の確認."""

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


def _create_work(work_dir: Path):
    result = bpy.ops.bname.work_new(filepath=str(work_dir))
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bname_work
    page = work.pages[0]
    result = bpy.ops.bname.coma_add()
    assert result == {"FINISHED"}, result

    balloon = page.balloons.add()
    balloon.id = "menu_balloon"
    balloon.x_mm = 20.0
    balloon.y_mm = 20.0
    balloon.width_mm = 30.0
    balloon.height_mm = 20.0

    text = page.texts.add()
    text.id = "menu_text"
    text.body = "右クリック"
    text.x_mm = 60.0
    text.y_mm = 20.0
    text.width_mm = 30.0
    text.height_mm = 20.0

    from bname_dev.operators import effect_line_op

    effect_line_op._create_effect_layer(
        bpy.context,
        (20.0, 60.0, 35.0, 35.0),
        parent_key="",
    )
    return work


def _stack_index_for_kind(kind: str) -> int:
    from bname_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(bpy.context)
    assert stack is not None
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") == kind:
            return index
    raise AssertionError(f"stack kind not found: {kind}")


def _assert_menu_for_kind(kind: str) -> None:
    from bname_dev.ui import context_menu
    from bname_dev.utils import layer_stack as layer_stack_utils

    index = _stack_index_for_kind(kind)
    assert layer_stack_utils.select_stack_index(bpy.context, index)
    items = context_menu.selection_command_items(bpy.context)
    labels = [str(item.get("label", "")) for item in items]
    assert labels == ["詳細設定", "複製", "リンク複製", "削除"], (kind, labels)
    enabled = {str(item.get("label", "")): bool(item.get("enabled", False)) for item in items}
    assert enabled["詳細設定"]
    assert enabled["複製"]
    assert enabled["削除"]
    assert enabled["リンク複製"] is (kind == "effect"), (kind, enabled)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_context_menu_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        _create_work(temp_root / "Context_Menu.bname")
        for kind in ("page", "coma", "balloon", "text", "effect"):
            _assert_menu_for_kind(kind)
        print("BNAME_CONTEXT_MENU_COMMANDS_OK")
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
