"""Blender 実機用: 汎用レイヤーフォルダの保存とD&D所属確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

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
    expected_parent = str(getattr(stack[parent_index], "key", "") or "")
    target_index = parent_index + 1
    if from_index < target_index:
        target_index -= 1
    stack.move(from_index, max(0, min(len(stack) - 1, target_index)))
    layer_stack_utils.apply_stack_order_if_ui_changed(context, moved_uid=uid)
    layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    current_parent = _maybe_stack_parent(context, uid)
    if current_parent is not None and current_parent != expected_parent:
        layer_stack_utils.apply_stack_drop_hint(context, uid, nesting_delta=1)
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)


def _maybe_stack_parent(context, uid: str) -> str | None:
    from bname_dev.utils import layer_stack as layer_stack_utils

    for item in _stack(context):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return str(getattr(item, "parent_key", "") or "")
    return None


def _stack_parent(context, uid: str) -> str:
    parent = _maybe_stack_parent(context, uid)
    if parent is not None:
        return parent
    raise AssertionError(f"stack item not found: {uid}")


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


def _add_balloon(page, balloon_id: str, parent_key: str):
    entry = page.balloons.add()
    entry.id = balloon_id
    entry.shape = "rect"
    entry.x_mm = 10.0
    entry.y_mm = 20.0
    entry.width_mm = 30.0
    entry.height_mm = 18.0
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_text(page, text_id: str, parent_key: str):
    entry = page.texts.add()
    entry.id = text_id
    entry.body = text_id
    entry.x_mm = 14.0
    entry.y_mm = 24.0
    entry.width_mm = 20.0
    entry.height_mm = 10.0
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_layer_folder_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "LayerFolder.bname"))
        assert "FINISHED" in result, result
        assert "FINISHED" in bpy.ops.bname.page_add("EXEC_DEFAULT")

        from bname_dev.io import schema
        from bname_dev.utils import layer_folder as layer_folder_utils
        from bname_dev.utils import layer_stack as layer_stack_utils
        from bname_dev.utils.layer_hierarchy import COMA_KIND, coma_stack_key, page_stack_key

        context = bpy.context
        work = context.scene.bname_work
        page1 = work.pages[0]
        page2 = work.pages[1]
        page1_key = page_stack_key(page1)
        page2_key = page_stack_key(page2)
        page2_coma_key = coma_stack_key(page2, page2.comas[0])

        folder_id = "folder_page2"
        folder = work.layer_folders.add()
        folder.id = folder_id
        folder.title = "汎用フォルダ"
        folder.parent_key = page2_key
        folder.expanded = True
        folder_uid = layer_stack_utils.target_uid("layer_folder", folder_id)
        page2_uid = layer_stack_utils.target_uid("page", page2_key)
        assert _stack_parent(context, folder_uid) == page2_key

        same_page_folder_id = "folder_page1"
        same_page_folder = work.layer_folders.add()
        same_page_folder.id = same_page_folder_id
        same_page_folder.title = "同一ページ確認"
        same_page_folder.parent_key = page1_key
        same_page_folder.expanded = True
        dup_balloon_page1 = _add_balloon(page1, "dup_balloon", page1_key)
        dup_balloon_page2 = _add_balloon(page2, "dup_balloon", page2_key)
        dup_text_page1 = _add_text(page1, "dup_text", page1_key)
        dup_text_page2 = _add_text(page2, "dup_text", page2_key)
        assert layer_folder_utils.assign_item_to_folder(
            context,
            SimpleNamespace(kind="balloon", key=f"{page1_key}:{dup_balloon_page1.id}"),
            same_page_folder_id,
        )
        assert layer_folder_utils.assign_item_to_folder(
            context,
            SimpleNamespace(kind="text", key=f"{page1_key}:{dup_text_page1.id}"),
            same_page_folder_id,
        )
        assert dup_balloon_page1.folder_key == same_page_folder_id
        assert dup_text_page1.folder_key == same_page_folder_id
        assert dup_balloon_page2.folder_key == ""
        assert dup_text_page2.folder_key == ""
        cross_balloon_src = _add_balloon(page1, "cross_dup_balloon", page1_key)
        cross_balloon_existing = _add_balloon(page2, "cross_dup_balloon", page2_key)
        cross_text_src = _add_text(page1, "cross_dup_text", page1_key)
        cross_text_existing = _add_text(page2, "cross_dup_text", page2_key)
        before_page2_foldered = sum(1 for entry in page2.balloons if entry.folder_key == folder_id)
        assert layer_folder_utils.assign_item_to_folder(
            context,
            SimpleNamespace(kind="balloon", key=f"{page1_key}:{cross_balloon_src.id}"),
            folder_id,
        )
        assert cross_balloon_existing.folder_key == ""
        assert sum(1 for entry in page2.balloons if entry.folder_key == folder_id) == before_page2_foldered + 1
        before_page2_foldered = sum(1 for entry in page2.texts if entry.folder_key == folder_id)
        assert layer_folder_utils.assign_item_to_folder(
            context,
            SimpleNamespace(kind="text", key=f"{page1_key}:{cross_text_src.id}"),
            folder_id,
        )
        assert cross_text_existing.folder_key == ""
        assert sum(1 for entry in page2.texts if entry.folder_key == folder_id) == before_page2_foldered + 1

        image = _add_image(context, "folder_image", page1_key)
        raster = _add_raster(context, "folder_raster", page1_key)
        balloon = _add_balloon(page1, "folder_balloon", page1_key)
        text = _add_text(page1, "folder_text", page1_key)

        image_uid = layer_stack_utils.target_uid("image", image.id)
        raster_uid = layer_stack_utils.target_uid("raster", raster.id)
        balloon_uid = layer_stack_utils.target_uid("balloon", f"{page1_key}:{balloon.id}")
        text_uid = layer_stack_utils.target_uid("text", f"{page1_key}:{text.id}")

        for uid in (image_uid, raster_uid, balloon_uid, text_uid):
            _move_uid_below_parent(context, uid, folder_uid)

        assert image.folder_key == folder_id
        assert image.parent_kind == "page" and image.parent_key == page2_key
        assert raster.folder_key == folder_id
        assert raster.scope == "page" and raster.parent_kind == "page" and raster.parent_key == page2_key
        assert not any(entry.id == "folder_balloon" for entry in page1.balloons)
        assert not any(entry.id == "folder_text" for entry in page1.texts)
        moved_balloon = next(entry for entry in page2.balloons if entry.id == "folder_balloon")
        moved_text = next(entry for entry in page2.texts if entry.id == "folder_text")
        assert moved_balloon.folder_key == folder_id
        assert moved_balloon.parent_kind == "page" and moved_balloon.parent_key == page2_key
        assert moved_text.folder_key == folder_id
        assert moved_text.parent_kind == "page" and moved_text.parent_key == page2_key

        assert _stack_parent(context, image_uid) == folder_id
        assert _stack_parent(context, raster_uid) == folder_id
        assert _stack_parent(context, layer_stack_utils.target_uid("balloon", f"{page2_key}:{moved_balloon.id}")) == folder_id
        assert _stack_parent(context, layer_stack_utils.target_uid("text", f"{page2_key}:{moved_text.id}")) == folder_id

        child_folder_id = "folder_child"
        child_folder = work.layer_folders.add()
        child_folder.id = child_folder_id
        child_folder.title = "子フォルダ"
        child_folder.parent_key = folder_id
        child_folder.expanded = True

        assert layer_folder_utils.assign_item_to_folder(
            context,
            SimpleNamespace(kind="image", key=image.id),
            child_folder_id,
        )
        _stack(context)
        assert image.folder_key == child_folder_id
        assert _stack_parent(context, image_uid) == child_folder_id

        coma_folder_id = "folder_coma"
        coma_folder = work.layer_folders.add()
        coma_folder.id = coma_folder_id
        coma_folder.title = "コマ内フォルダ"
        coma_folder.parent_key = page2_coma_key
        coma_folder.expanded = True
        coma_folder_uid = layer_stack_utils.target_uid("layer_folder", coma_folder_id)
        _stack(context)
        _move_uid_below_parent(context, raster_uid, coma_folder_uid)
        assert raster.folder_key == coma_folder_id
        assert raster.parent_kind == "coma" and raster.parent_key == page2_coma_key
        assert _stack_parent(context, raster_uid) == coma_folder_id

        _move_uid_below_parent(context, image_uid, page2_uid)
        assert image.folder_key == ""
        assert image.parent_kind == "page" and image.parent_key == page2_key
        assert _stack_parent(context, image_uid) == page2_key

        work_data = schema.work_to_dict(work)
        assert any(item["id"] == folder_id for item in work_data["layer_folders"])
        assert next(item for item in work_data["raster_layers"] if item["id"] == raster.id)["folderKey"] == coma_folder_id
        page_data = schema.page_to_dict(page2)
        balloon_data = next(item for item in page_data["balloons"] if item["id"] == moved_balloon.id)
        text_data = next(item for item in page_data["texts"] if item["id"] == moved_text.id)
        assert balloon_data["folderKey"] == folder_id, balloon_data
        assert text_data["folderKey"] == folder_id, text_data

        work.layer_folders.clear()
        context.scene.bname_raster_layers.clear()
        schema.work_from_dict(work, work_data)
        assert any(item.id == folder_id for item in work.layer_folders)
        restored_raster = next(entry for entry in context.scene.bname_raster_layers if entry.id == "folder_raster")
        assert restored_raster.folder_key == coma_folder_id
        page2.balloons.clear()
        page2.texts.clear()
        schema.page_from_dict(page2, page_data)
        assert next(entry for entry in page2.balloons if entry.id == "folder_balloon").folder_key == folder_id
        assert next(entry for entry in page2.texts if entry.id == "folder_text").folder_key == folder_id

        print("BNAME_LAYER_FOLDER_OK")
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
