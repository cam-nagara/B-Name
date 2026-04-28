"""Blender 実機用: コマblendテンプレートの初回コピー確認."""

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


def _create_template(path: Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.name = "TemplateScene"
    scene.render.engine = "CYCLES"
    scene.render.film_transparent = True
    scene.render.resolution_x = 321
    scene.render.resolution_y = 123

    coll = bpy.data.collections.new("BNAME_TEMPLATE_MARKER_COLLECTION")
    scene.collection.children.link(coll)
    if "BNAME_TEMPLATE_MARKER_VIEW_LAYER" not in scene.view_layers:
        scene.view_layers.new(name="BNAME_TEMPLATE_MARKER_VIEW_LAYER")

    mesh = bpy.data.meshes.new("BNAME_TEMPLATE_MARKER_MESH")
    mesh.from_pydata(
        [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    obj = bpy.data.objects.new("BNAME_TEMPLATE_MARKER_OBJECT", mesh)
    coll.objects.link(obj)

    mat = bpy.data.materials.new("BNAME_TEMPLATE_MARKER_MATERIAL")
    mat.use_nodes = True
    obj.data.materials.append(mat)
    node_group = bpy.data.node_groups.new("BNAME_TEMPLATE_MARKER_NODE_GROUP", "ShaderNodeTree")
    node_group.use_fake_user = True

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.type = "PANO"
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False, compress=True)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_coma_template_"))
    template_path = temp_root / "template.blend"
    work_dir = temp_root / "Template_Test.bname"
    mod = None
    try:
        _create_template(template_path)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        result = bpy.ops.bname.work_new(filepath=str(work_dir))
        assert result == {"FINISHED"}, result

        work = bpy.context.scene.bname_work
        work.coma_blend_template_path = str(template_path)
        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result

        assert Path(bpy.data.filepath).resolve() == (work_dir / "p0001" / "c01" / "c01.blend").resolve()
        assert bpy.context.scene.name == "TemplateScene"
        assert bpy.data.collections.get("BNAME_TEMPLATE_MARKER_COLLECTION") is not None
        assert bpy.data.objects.get("BNAME_TEMPLATE_MARKER_OBJECT") is not None
        assert bpy.context.scene.view_layers.get("BNAME_TEMPLATE_MARKER_VIEW_LAYER") is not None
        assert bpy.data.materials.get("BNAME_TEMPLATE_MARKER_MATERIAL") is not None
        assert bpy.data.node_groups.get("BNAME_TEMPLATE_MARKER_NODE_GROUP") is not None
        assert bpy.context.scene.camera is not None
        assert bpy.context.scene.camera.data.type == "PANO"

        result = bpy.ops.bname.exit_coma_mode()
        assert result == {"FINISHED"}, result

        work = bpy.context.scene.bname_work
        work.active_page_index = 0
        work.pages[0].active_coma_index = 0
        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert bpy.data.collections.get("BNAME_TEMPLATE_MARKER_COLLECTION") is not None
        assert bpy.data.objects.get("BNAME_TEMPLATE_MARKER_OBJECT") is not None
        assert bpy.data.node_groups.get("BNAME_TEMPLATE_MARKER_NODE_GROUP") is not None

        result = bpy.ops.bname.exit_coma_mode()
        assert result == {"FINISHED"}, result
        print("BNAME_COMA_TEMPLATE_OK")
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
