"""Blender実機用: 効果線以外の詳細設定反映を確認する."""

from __future__ import annotations

import importlib.util
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


def _value_node_default(material) -> float:
    for node in material.node_tree.nodes:
        if node.bl_idname == "ShaderNodeValue":
            return float(node.outputs[0].default_value)
    raise AssertionError("raster alpha value node not found")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_detail_settings_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "Detail_Settings.bname"))
        assert result == {"FINISHED"}, result

        result = bpy.ops.bname.raster_layer_add(dpi=300, bit_depth="gray8")
        assert result == {"FINISHED"}, result
        entry = bpy.context.scene.bname_raster_layers[0]

        from bname_dev.operators import raster_layer_op
        from bname_dev.core.work import get_work
        from bname_dev.io import balloon_presets
        from bname_dev.ui import overlay_image
        from bname_dev.utils import balloon_shapes
        from bname_dev.utils.geom import Rect

        obj = bpy.data.objects.get(raster_layer_op.raster_plane_name(entry.id))
        assert obj is not None

        entry.visible = False
        assert obj.hide_viewport is True
        assert obj.hide_render is True
        entry.visible = True
        assert obj.hide_viewport is False
        assert obj.hide_render is False

        entry.opacity = 0.25
        entry.line_color = (0.8, 0.1, 0.1, 0.5)
        mat = bpy.data.materials.get(raster_layer_op.raster_material_name(entry.id))
        assert mat is not None
        assert abs(_value_node_default(mat) - 0.125) < 1e-5

        quad = overlay_image.image_quad_points_mm(
            SimpleNamespace(x_mm=0.0, y_mm=0.0, width_mm=10.0, height_mm=20.0, rotation_deg=90.0)
        )
        assert len(quad) == 4
        assert round(quad[0][0], 3) == 15.0
        assert round(quad[0][1], 3) == 5.0

        work = get_work(bpy.context)
        assert work is not None
        work_dir = Path(work.work_dir)
        balloon_presets.save_local_preset(
            work_dir,
            "detail_custom_balloon",
            "",
            [(0.0, 0.0), (10.0, 20.0), (20.0, 0.0)],
        )
        outline = balloon_shapes.outline_for_entry(
            SimpleNamespace(
                shape="custom",
                custom_preset_name="detail_custom_balloon",
                rounded_corner_enabled=False,
                rounded_corner_radius_mm=0.0,
                shape_params=SimpleNamespace(),
            ),
            Rect(10.0, 20.0, 100.0, 50.0),
        )
        assert len(outline) == 3
        assert outline[0] == (10.0, 20.0)
        assert outline[1] == (60.0, 70.0)
        assert outline[2] == (110.0, 20.0)
    finally:
        if mod is not None:
            mod.unregister()

    print("BNAME_DETAIL_SETTINGS_RUNTIME_OK")


if __name__ == "__main__":
    main()
