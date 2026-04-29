"""Blender 実機用: レイヤーリスト D&D 親変更のデータ移送確認."""

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


def _stack(context):
    from bname_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    assert stack is not None
    layer_stack_utils.remember_layer_stack_signature(context)
    return stack


def _move_uid_below_parent(context, uid: str, parent_uid: str) -> None:
    from bname_dev.utils import layer_stack as layer_stack_utils

    stack = _stack(context)
    from_index = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == uid)
    parent_index = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == parent_uid)
    target_index = parent_index + 1
    if from_index < target_index:
        target_index -= 1
    stack.move(from_index, max(0, min(len(stack) - 1, target_index)))
    layer_stack_utils.apply_stack_order_if_ui_changed(context, moved_uid=uid)
    layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)


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


def _add_image(context, image_id: str, parent_key: str):
    entry = context.scene.bname_image_layers.add()
    entry.id = image_id
    entry.title = image_id
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_raster(context, raster_id: str, parent_key: str):
    entry = context.scene.bname_raster_layers.add()
    entry.id = raster_id
    entry.title = raster_id
    entry.scope = "page"
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_gp_layer(context, name: str, parent_key: str):
    from bname_dev.utils import gp_layer_parenting as gp_parent
    from bname_dev.utils import gpencil as gp_utils

    obj = gp_utils.ensure_master_gpencil(context.scene)
    layer = obj.data.layers.new(name)
    gp_parent.set_parent_key(layer, parent_key)
    return layer


def _add_effect_layer(context, parent_key: str):
    from bname_dev.operators import effect_line_op

    _obj, layer = effect_line_op._create_effect_layer(
        context,
        (10.0, 10.0, 20.0, 20.0),
        parent_key=parent_key,
    )
    return layer


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_layer_stack_dnd_reparent_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "LayerStackDnd.bname"))
        assert "FINISHED" in result, result
        assert "FINISHED" in bpy.ops.bname.page_add("EXEC_DEFAULT")

        from bname_dev.utils import layer_stack as layer_stack_utils
        from bname_dev.utils.layer_hierarchy import (
            COMA_KIND,
            OUTSIDE_KIND,
            OUTSIDE_STACK_KEY,
            coma_stack_key,
            outside_child_key,
            page_stack_key,
        )

        context = bpy.context
        work = context.scene.bname_work
        page1 = work.pages[0]
        page2 = work.pages[1]
        page1_key = page_stack_key(page1)
        page2_key = page_stack_key(page2)
        page1_coma_key = coma_stack_key(page1, page1.comas[0])
        page2_coma_key = coma_stack_key(page2, page2.comas[0])
        page2_uid = layer_stack_utils.target_uid("page", page2_key)
        page2_coma_uid = layer_stack_utils.target_uid(COMA_KIND, page2_coma_key)
        outside_uid = layer_stack_utils.target_uid(OUTSIDE_KIND, OUTSIDE_STACK_KEY)

        text = _add_text(page1, "dnd_cross_text", page1_key)
        text_uid = layer_stack_utils.target_uid("text", f"{page1_key}:{text.id}")
        _move_uid_below_parent(context, text_uid, page2_uid)
        assert len(page1.texts) == 0
        assert len(page2.texts) == 1
        moved_text = page2.texts[0]
        assert moved_text.parent_kind == "page" and moved_text.parent_key == page2_key

        balloon = _add_balloon(page1, "dnd_cross_balloon", page1_key)
        child = _add_text(page1, "dnd_cross_child", page1_key, parent_balloon_id=balloon.id)
        balloon_id = str(balloon.id)
        child_id = str(child.id)
        balloon_uid = layer_stack_utils.target_uid("balloon", f"{page1_key}:{balloon_id}")
        _move_uid_below_parent(context, balloon_uid, page2_coma_uid)
        assert len(page1.balloons) == 0
        assert len(page2.balloons) == 1
        assert not any(getattr(t, "id", "") == child_id for t in page1.texts)
        moved_balloon = page2.balloons[0]
        moved_child = next(t for t in page2.texts if t.id == child_id)
        assert moved_balloon.parent_kind == "coma" and moved_balloon.parent_key == page2_coma_key
        assert moved_child.parent_balloon_id == moved_balloon.id
        assert moved_child.parent_kind == "coma" and moved_child.parent_key == page2_coma_key

        moved_text_id = str(moved_text.id)
        moved_text_uid = layer_stack_utils.target_uid("text", f"{page2_key}:{moved_text_id}")
        _move_uid_below_parent(context, moved_text_uid, outside_uid)
        assert not any(getattr(t, "id", "") == moved_text_id for t in page2.texts)
        assert any(getattr(t, "id", "") == moved_text_id for t in work.shared_texts)
        shared_text_uid = layer_stack_utils.target_uid("text", outside_child_key(moved_text_id))
        _move_uid_below_parent(context, shared_text_uid, page2_uid)
        assert not any(getattr(t, "id", "") == moved_text_id for t in work.shared_texts)
        restored_text = next(t for t in page2.texts if t.id == moved_text_id)
        assert restored_text.parent_kind == "page" and restored_text.parent_key == page2_key

        from bname_dev.utils import gp_layer_parenting as gp_parent

        gp_layer = _add_gp_layer(context, "dnd_cross_gp", page1_key)
        gp_uid = layer_stack_utils.target_uid("gp", layer_stack_utils._node_stack_key(gp_layer))
        _move_uid_below_parent(context, gp_uid, page2_coma_uid)
        assert gp_parent.parent_key(gp_layer) == page2_coma_key

        effect_layer = _add_effect_layer(context, page1_key)
        effect_uid = layer_stack_utils.target_uid("effect", layer_stack_utils._node_stack_key(effect_layer))
        _move_uid_below_parent(context, effect_uid, outside_uid)
        assert gp_parent.parent_key(effect_layer) == ""

        image = _add_image(context, "dnd_cross_image", page1_key)
        image_uid = layer_stack_utils.target_uid("image", image.id)
        _move_uid_below_parent(context, image_uid, page2_coma_uid)
        assert image.parent_kind == "coma" and image.parent_key == page2_coma_key

        raster = _add_raster(context, "dnd_cross_raster", page1_key)
        raster_uid = layer_stack_utils.target_uid("raster", raster.id)
        _move_uid_below_parent(context, raster_uid, page2_uid)
        assert raster.scope == "page" and raster.parent_kind == "page" and raster.parent_key == page2_key
        _move_uid_below_parent(context, raster_uid, outside_uid)
        assert raster.scope == "master" and raster.parent_kind == "none" and raster.parent_key == ""

        coma_balloon = _add_balloon(page1, "dnd_coma_child_balloon", page1_coma_key)
        coma_child = _add_text(
            page1,
            "dnd_coma_child_text",
            page1_coma_key,
            parent_balloon_id=coma_balloon.id,
        )
        coma_text = _add_text(page1, "dnd_coma_direct_text", page1_coma_key)
        coma_image = _add_image(context, "dnd_coma_child_image", page1_coma_key)
        coma_raster = _add_raster(context, "dnd_coma_child_raster", page1_coma_key)
        coma_gp = _add_gp_layer(context, "dnd_coma_child_gp", page1_coma_key)
        coma_effect = _add_effect_layer(context, page1_coma_key)
        coma_balloon_id = str(coma_balloon.id)
        coma_child_id = str(coma_child.id)
        coma_text_id = str(coma_text.id)

        page1_coma_uid = layer_stack_utils.target_uid(COMA_KIND, page1_coma_key)
        before_page2_comas = len(page2.comas)
        before_page2_coma_keys = {coma_stack_key(page2, panel) for panel in page2.comas}
        _move_uid_below_parent(context, page1_coma_uid, page2_uid)
        assert len(page1.comas) == 0
        assert len(page2.comas) == before_page2_comas + 1
        moved_coma_key = next(
            coma_stack_key(page2, panel)
            for panel in page2.comas
            if coma_stack_key(page2, panel) not in before_page2_coma_keys
        )
        moved_coma_balloon = next(b for b in page2.balloons if b.id == coma_balloon_id)
        moved_coma_child = next(t for t in page2.texts if t.id == coma_child_id)
        moved_coma_text = next(t for t in page2.texts if t.id == coma_text_id)
        assert not any(getattr(b, "id", "") == coma_balloon_id for b in page1.balloons)
        assert not any(getattr(t, "id", "") == coma_child_id for t in page1.texts)
        assert moved_coma_balloon.parent_kind == "coma" and moved_coma_balloon.parent_key == moved_coma_key
        assert moved_coma_child.parent_balloon_id == moved_coma_balloon.id
        assert moved_coma_child.parent_kind == "coma" and moved_coma_child.parent_key == moved_coma_key
        assert moved_coma_text.parent_kind == "coma" and moved_coma_text.parent_key == moved_coma_key
        assert coma_image.parent_kind == "coma" and coma_image.parent_key == moved_coma_key
        assert coma_raster.scope == "page" and coma_raster.parent_kind == "coma"
        assert coma_raster.parent_key == moved_coma_key
        assert gp_parent.parent_key(coma_gp) == moved_coma_key
        assert gp_parent.parent_key(coma_effect) == moved_coma_key

        print("BNAME_LAYER_STACK_DND_REPARENT_OK")
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
