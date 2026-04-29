"""Blender実機用: ビューポート Alt reparent フェーズAのデータ整合性確認."""

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


def _stack_item(context, kind: str, key: str):
    from bname_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    assert stack is not None
    uid = layer_stack_utils.target_uid(kind, key)
    for index, item in enumerate(stack):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return index, item
    raise AssertionError(f"stack item not found: {uid}")


def _add_balloon(page, bid: str, parent_key: str, x: float = 10.0):
    entry = page.balloons.add()
    entry.id = bid
    entry.shape = "rect"
    entry.x_mm = x
    entry.y_mm = 20.0
    entry.width_mm = 30.0
    entry.height_mm = 18.0
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_text(page, tid: str, parent_key: str, x: float = 12.0, parent_balloon_id: str = ""):
    entry = page.texts.add()
    entry.id = tid
    entry.body = tid
    entry.x_mm = x
    entry.y_mm = 22.0
    entry.width_mm = 20.0
    entry.height_mm = 10.0
    entry.parent_balloon_id = parent_balloon_id
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _assert_close(actual: float, expected: float, label: str, eps: float = 1.0e-4) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_alt_reparent_phase_a_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "AltReparent.bname"))
        assert "FINISHED" in result, result
        assert "FINISHED" in bpy.ops.bname.page_add("EXEC_DEFAULT")

        from bname_dev.utils import layer_reparent
        from bname_dev.utils import layer_stack as layer_stack_utils
        from bname_dev.utils import page_grid
        from bname_dev.utils.layer_hierarchy import coma_stack_key, page_stack_key

        context = bpy.context
        work = context.scene.bname_work
        page1 = work.pages[0]
        page2 = work.pages[1]
        coma1 = page1.comas[0]
        coma2 = page2.comas[0]
        page1_key = page_stack_key(page1)
        page2_key = page_stack_key(page2)
        coma1_key = coma_stack_key(page1, coma1)
        coma2_key = coma_stack_key(page2, coma2)

        balloon = _add_balloon(page1, "alt_balloon", page1_key)
        text = _add_text(page1, "alt_text", page1_key)
        child_text = _add_text(page1, "alt_child_text", page1_key, parent_balloon_id=balloon.id)
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)

        _idx, balloon_item = _stack_item(context, "balloon", f"{page1_key}:{balloon.id}")
        target_coma1 = layer_reparent.ClickTarget("coma", page1, coma1, 0, None, None)
        assert layer_reparent.reparent_stack_item(context, balloon_item, target=target_coma1)
        assert balloon.parent_kind == "coma" and balloon.parent_key == coma1_key

        _idx, text_item = _stack_item(context, "text", f"{page1_key}:{text.id}")
        target_coma2 = layer_reparent.ClickTarget("coma", page2, coma2, 1, None, None)
        assert layer_reparent.reparent_stack_item(context, text_item, target=target_coma2)
        assert len(page1.texts) == 1
        assert len(page2.texts) == 1
        moved_text = page2.texts[0]
        assert moved_text.id == "alt_text"
        assert moved_text.parent_kind == "coma" and moved_text.parent_key == coma2_key

        # 別ページへの位置維持: ローカル値はページオフセット差で補正される。
        src_off = page_grid.page_total_offset_mm(work, context.scene, 0)
        dst_off = page_grid.page_total_offset_mm(work, context.scene, 1)
        _assert_close(moved_text.x_mm, 12.0 + src_off[0] - dst_off[0], "cross-page text x")
        _assert_close(moved_text.y_mm, 22.0 + src_off[1] - dst_off[1], "cross-page text y")

        # balloonを別ページへ送ると、子テキストも同じページへ移送される。
        _idx, balloon_item = _stack_item(context, "balloon", f"{page1_key}:{balloon.id}")
        target_page2 = layer_reparent.ClickTarget("page", page2, None, 1, None, None)
        assert layer_reparent.reparent_stack_item(context, balloon_item, target=target_page2)
        assert len(page1.balloons) == 0
        assert len(page2.balloons) == 1
        assert len(page1.texts) == 0
        assert len(page2.texts) == 2
        moved_balloon = page2.balloons[0]
        moved_child = next(t for t in page2.texts if t.id == "alt_child_text")
        assert moved_balloon.parent_kind == "page" and moved_balloon.parent_key == page2_key
        assert moved_child.parent_balloon_id == moved_balloon.id

        # マルチセレクト一括 reparent。
        moved_text.parent_kind = "page"
        moved_text.parent_key = page2_key
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        for item in context.scene.bname_layer_stack:
            layer_stack_utils.set_item_selected(context, item, False)
        _idx, moved_text_item = _stack_item(context, "text", f"{page2_key}:alt_text")
        _idx, moved_balloon_item = _stack_item(context, "balloon", f"{page2_key}:{moved_balloon.id}")
        layer_stack_utils.set_item_selected(context, moved_text_item, True)
        layer_stack_utils.set_item_selected(context, moved_balloon_item, True)
        context.scene.bname_active_layer_stack_index = -1
        changed = layer_reparent.reparent_selected(
            context,
            layer_reparent.ClickTarget("coma", page2, coma2, 1, None, None),
        )
        assert changed == 2
        current_text = next(t for t in page2.texts if t.id == "alt_text")
        current_balloon = next(b for b in page2.balloons if b.id == moved_balloon.id)
        assert current_text.parent_kind == "coma" and current_text.parent_key == coma2_key
        assert current_balloon.parent_kind == "coma" and current_balloon.parent_key == coma2_key

        print("BNAME_ALT_REPARENT_PHASE_A_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
