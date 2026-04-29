"""Blender実機用: ページ外(shared)レイヤーの work.json スキーマ確認."""

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


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_shared_schema_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "SharedSchema.bname"))
        assert "FINISHED" in result, result

        from bname_dev.io import schema

        scene = bpy.context.scene
        work = scene.bname_work

        balloon = work.shared_balloons.add()
        balloon.id = "shared_balloon_1"
        balloon.shape = "rect"
        balloon.x_mm = 123.0
        balloon.y_mm = 45.0
        balloon.parent_kind = "none"
        balloon.parent_key = ""

        text = work.shared_texts.add()
        text.id = "shared_text_1"
        text.body = "shared"
        text.x_mm = 130.0
        text.y_mm = 50.0
        text.parent_kind = "none"
        text.parent_key = ""

        coma = work.shared_comas.add()
        coma.id = "shared_coma_1"
        coma.coma_id = "shared_c01"
        coma.title = "ページ外コマ"
        coma.rect_x_mm = 100.0
        coma.rect_y_mm = 40.0
        coma.rect_width_mm = 80.0
        coma.rect_height_mm = 60.0

        image = scene.bname_image_layers.add()
        image.id = "image_shared_1"
        image.title = "参照画像"
        image.filepath = "C:/tmp/reference.png"
        image.parent_kind = "none"
        image.parent_key = ""

        data = schema.work_to_dict(work)
        assert data["schemaVersion"] >= 3
        assert data["shared_balloons"][0]["id"] == "shared_balloon_1"
        assert data["shared_texts"][0]["id"] == "shared_text_1"
        assert data["shared_comas"][0]["comaId"] == "shared_c01"
        assert data["image_layers"][0]["parentKind"] == "none"

        work.shared_balloons.clear()
        work.shared_texts.clear()
        work.shared_comas.clear()
        scene.bname_image_layers.clear()
        schema.work_from_dict(work, data)

        assert len(work.shared_balloons) == 1
        assert work.shared_balloons[0].id == "shared_balloon_1"
        assert work.shared_balloons[0].parent_kind == "none"
        assert len(work.shared_texts) == 1
        assert work.shared_texts[0].body == "shared"
        assert work.shared_texts[0].parent_kind == "none"
        assert len(work.shared_comas) == 1
        assert work.shared_comas[0].coma_id == "shared_c01"
        assert len(scene.bname_image_layers) == 1
        assert scene.bname_image_layers[0].parent_kind == "none"

        print("BNAME_SHARED_LAYER_SCHEMA_OK")
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
