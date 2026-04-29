"""Blender実機用: ビューポート Alt reparent フェーズBのページ外移送確認."""

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


def _assert_close(actual: float, expected: float, label: str, eps: float = 1.0e-4) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _add_balloon(page, bid: str, parent_key: str):
    entry = page.balloons.add()
    entry.id = bid
    entry.shape = "rect"
    entry.x_mm = 10.0
    entry.y_mm = 20.0
    entry.width_mm = 30.0
    entry.height_mm = 18.0
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_text(page, tid: str, parent_key: str, parent_balloon_id: str = ""):
    entry = page.texts.add()
    entry.id = tid
    entry.body = tid
    entry.x_mm = 14.0
    entry.y_mm = 24.0
    entry.width_mm = 20.0
    entry.height_mm = 10.0
    entry.parent_balloon_id = parent_balloon_id
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_alt_reparent_phase_b_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "AltReparentB.bname"))
        assert "FINISHED" in result, result
        assert "FINISHED" in bpy.ops.bname.page_add("EXEC_DEFAULT")

        from bname_dev.utils import layer_reparent
        from bname_dev.utils import layer_stack as layer_stack_utils
        from bname_dev.utils import page_grid
        from bname_dev.utils.layer_hierarchy import (
            OUTSIDE_KIND,
            OUTSIDE_STACK_KEY,
            coma_stack_key,
            outside_child_key,
            page_stack_key,
        )

        context = bpy.context
        scene = context.scene
        work = scene.bname_work
        page1 = work.pages[0]
        page2 = work.pages[1]
        page1_key = page_stack_key(page1)
        page2_key = page_stack_key(page2)
        coma2_key = coma_stack_key(page2, page2.comas[0])

        balloon = _add_balloon(page1, "outside_balloon", page1_key)
        child = _add_text(page1, "outside_child", page1_key, parent_balloon_id=balloon.id)
        child_x = float(child.x_mm)
        text = _add_text(page1, "outside_text", page1_key)
        text_id = str(text.id)
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)

        _idx, outside_group = _stack_item(context, OUTSIDE_KIND, OUTSIDE_STACK_KEY)
        assert outside_group.key == OUTSIDE_STACK_KEY

        src_off = page_grid.page_total_offset_mm(work, scene, 0)
        _idx, balloon_item = _stack_item(context, "balloon", f"{page1_key}:{balloon.id}")
        assert layer_reparent.reparent_stack_item(
            context,
            balloon_item,
            target=layer_reparent.ClickTarget("outside", None, None, -1, None, None),
        )
        assert len(page1.balloons) == 0
        assert len(work.shared_balloons) == 1
        assert len(work.shared_texts) == 1
        shared_balloon = work.shared_balloons[0]
        shared_child = work.shared_texts[0]
        assert shared_balloon.parent_kind == "none" and shared_balloon.parent_key == ""
        assert shared_child.parent_balloon_id == shared_balloon.id
        _assert_close(shared_balloon.x_mm, 10.0 + src_off[0], "shared balloon x")
        _assert_close(shared_child.x_mm, child_x + src_off[0], "shared child x")

        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        _idx, shared_balloon_item = _stack_item(context, "balloon", outside_child_key(shared_balloon.id))
        assert shared_balloon_item.parent_key == OUTSIDE_STACK_KEY
        assert layer_reparent.reparent_stack_item(
            context,
            shared_balloon_item,
            target=layer_reparent.ClickTarget("coma", page2, page2.comas[0], 1, None, None),
        )
        dst_off = page_grid.page_total_offset_mm(work, scene, 1)
        assert len(work.shared_balloons) == 0
        assert len(work.shared_texts) == 0
        assert len(page2.balloons) == 1
        assert len(page2.texts) == 1
        moved_balloon = page2.balloons[0]
        moved_child = page2.texts[0]
        assert moved_balloon.parent_kind == "coma" and moved_balloon.parent_key == coma2_key
        assert moved_child.parent_balloon_id == moved_balloon.id
        _assert_close(moved_balloon.x_mm, 10.0 + src_off[0] - dst_off[0], "restored balloon x")

        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        _idx, text_item = _stack_item(context, "text", f"{page1_key}:{text_id}")
        assert layer_reparent.reparent_stack_item(
            context,
            text_item,
            target=layer_reparent.ClickTarget("outside", None, None, -1, None, None),
        )
        assert len(work.shared_texts) == 1
        shared_text = work.shared_texts[0]
        assert shared_text.parent_kind == "none" and shared_text.parent_key == ""

        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        _idx, coma_item = _stack_item(context, "coma", coma2_key)
        assert layer_reparent.reparent_stack_item(
            context,
            coma_item,
            target=layer_reparent.ClickTarget("outside", None, None, -1, None, None),
        )
        assert len(page2.comas) == 0
        assert len(work.shared_comas) == 1
        assert len(page2.balloons) == 0
        assert len(work.shared_balloons) == 1
        assert len(work.shared_texts) == 2
        assert work.shared_balloons[0].parent_kind == "none"

        image = scene.bname_image_layers.add()
        image.id = "outside_image"
        image.title = "outside image"
        image.parent_kind = "page"
        image.parent_key = page2_key
        raster = scene.bname_raster_layers.add()
        raster.id = "outside_raster"
        raster.title = "outside raster"
        raster.scope = "page"
        raster.parent_kind = "page"
        raster.parent_key = page2_key
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        _idx, image_item = _stack_item(context, "image", image.id)
        _idx, raster_item = _stack_item(context, "raster", raster.id)
        assert layer_reparent.reparent_stack_item(
            context,
            image_item,
            target=layer_reparent.ClickTarget("outside", None, None, -1, (50.0, 60.0), None),
            new_world_xy_mm=(50.0, 60.0),
        )
        assert image.parent_kind == "none" and image.parent_key == ""
        _assert_close(image.x_mm, 0.0, "image centered x")
        assert layer_reparent.reparent_stack_item(
            context,
            raster_item,
            target=layer_reparent.ClickTarget("outside", None, None, -1, None, None),
        )
        assert raster.scope == "master" and raster.parent_kind == "none" and raster.parent_key == ""

        print("BNAME_ALT_REPARENT_PHASE_B_OUTSIDE_OK")
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
