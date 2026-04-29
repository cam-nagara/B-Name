"""Blender実機用: ラスターレイヤーの初期透明化と描画モード遷移を確認する."""

from __future__ import annotations

import importlib.util
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


def _pixel_at(image, x: int, y: int) -> tuple[float, float, float, float]:
    width = int(image.size[0])
    offset = (y * width + x) * 4
    return tuple(float(v) for v in image.pixels[offset : offset + 4])


def _alpha_value_node(material) -> float:
    for node in material.node_tree.nodes:
        if node.bl_idname == "ShaderNodeValue":
            return float(node.outputs[0].default_value)
    raise AssertionError("raster alpha value node not found")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_raster_paint_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        result = bpy.ops.bname.work_new(filepath=str(temp_root / "Raster_Paint.bname"))
        assert result == {"FINISHED"}, result
        result = bpy.ops.bname.raster_layer_add(dpi=150, bit_depth="gray8")
        assert result == {"FINISHED"}, result

        scene = bpy.context.scene
        entry = scene.bname_raster_layers[0]

        from bname_dev.operators import raster_layer_op
        from bname_dev.core.work import get_work

        work = get_work(bpy.context)
        assert work is not None
        png_path = Path(work.work_dir) / entry.filepath_rel
        assert png_path.is_file(), png_path

        image = bpy.data.images.get(entry.image_name)
        assert image is not None
        width, height = int(image.size[0]), int(image.size[1])
        for x, y in (
            (0, 0),
            (width - 1, 0),
            (0, height - 1),
            (width - 1, height - 1),
        ):
            assert _pixel_at(image, x, y) == (0.0, 0.0, 0.0, 0.0)
        saved_image = bpy.data.images.load(str(png_path), check_existing=False)
        try:
            saved_width, saved_height = int(saved_image.size[0]), int(saved_image.size[1])
            for x, y in (
                (0, 0),
                (saved_width - 1, 0),
                (0, saved_height - 1),
                (saved_width - 1, saved_height - 1),
            ):
                assert _pixel_at(saved_image, x, y) == (0.0, 0.0, 0.0, 0.0)
        finally:
            bpy.data.images.remove(saved_image)

        obj = bpy.data.objects.get(raster_layer_op.raster_plane_name(entry.id))
        assert obj is not None
        mat = bpy.data.materials.get(raster_layer_op.raster_material_name(entry.id))
        assert mat is not None
        assert (
            mat.get(raster_layer_op.RASTER_MATERIAL_VERSION_PROP)
            == raster_layer_op.RASTER_MATERIAL_VERSION
        )
        assert abs(float(mat.diffuse_color[3])) < 1e-6

        transparent_node = mat.node_tree.nodes.get(
            raster_layer_op.RASTER_TRANSPARENT_NODE
        )
        assert transparent_node is not None
        mat.node_tree.nodes.remove(transparent_node)
        mat = raster_layer_op.ensure_raster_material(entry, image)
        assert (
            mat.node_tree.nodes.get(raster_layer_op.RASTER_TRANSPARENT_NODE)
            is not None
        )
        node_ptrs = sorted(node.as_pointer() for node in mat.node_tree.nodes)

        entry.opacity = 0.5
        entry.line_color = (0.2, 0.2, 0.2, 0.75)
        mat_after = bpy.data.materials.get(
            raster_layer_op.raster_material_name(entry.id)
        )
        assert mat_after is mat
        assert sorted(node.as_pointer() for node in mat.node_tree.nodes) == node_ptrs
        assert abs(_alpha_value_node(mat) - 0.375) < 1e-5

        result = bpy.ops.bname.raster_layer_paint_enter()
        assert result == {"FINISHED"}, result
        assert bpy.context.object is obj
        assert obj.mode == "TEXTURE_PAINT"
        paint = bpy.context.tool_settings.image_paint
        assert paint.canvas is image
        assert paint.brush is not None
        assert tuple(round(float(c), 6) for c in paint.brush.color) == (
            0.0,
            0.0,
            0.0,
        )

        entry.opacity = 0.25
        entry.line_color = (0.4, 0.4, 0.4, 1.0)
        assert bpy.context.object is obj
        assert obj.mode == "TEXTURE_PAINT"
        assert bpy.context.tool_settings.image_paint.canvas is image
        assert sorted(node.as_pointer() for node in mat.node_tree.nodes) == node_ptrs
        assert abs(_alpha_value_node(mat) - 0.25) < 1e-5

        paint.brush.color = (1.0, 0.0, 0.0)
        assert raster_layer_op.force_active_brush_grayscale(bpy.context) is True
        assert tuple(round(float(c), 6) for c in paint.brush.color) == (
            0.333333,
            0.333333,
            0.333333,
        )

        result = bpy.ops.bname.raster_layer_paint_exit()
        assert result == {"FINISHED"}, result
        assert obj.mode == "OBJECT"

        result = bpy.ops.bname.raster_layer_mode_set(mode="TEXTURE_PAINT")
        assert result == {"FINISHED"}, result
        assert bpy.context.object is obj
        assert obj.mode == "TEXTURE_PAINT"
        result = bpy.ops.bname.raster_layer_mode_set(mode="OBJECT")
        assert result == {"FINISHED"}, result
        assert obj.mode == "OBJECT"

        entry.locked = True
        result = bpy.ops.bname.raster_layer_paint_enter()
        assert result == {"CANCELLED"}, result
        entry.locked = False

        entry.visible = False
        result = bpy.ops.bname.raster_layer_paint_enter()
        assert result == {"CANCELLED"}, result
        entry.visible = True
    finally:
        if mod is not None:
            mod.unregister()

    print("BNAME_RASTER_LAYER_PAINT_OK")


if __name__ == "__main__":
    main()
