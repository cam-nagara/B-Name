"""魚眼レンダリング補助オペレーター."""

from __future__ import annotations

import bpy
from bpy.props import FloatProperty
from bpy.types import Operator

from ..core.fisheye import pencil4_link
from ..core.mode import MODE_COMA, get_mode
from ..utils import coma_camera


def _is_coma_mode(context) -> bool:
    return get_mode(context) == MODE_COMA


class BNAME_OT_fisheye_save_pencil4_widths(Operator):
    bl_idname = "bname.fisheye_save_pencil4_widths"
    bl_label = "Pencil+4 線幅を保存"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _is_coma_mode(context)

    def execute(self, context):
        reduction_enabled = bool(getattr(context.scene, "bname_coma_camera_reduction_mode", False))
        if reduction_enabled:
            pencil4_link.restore()
        count = pencil4_link.save_widths()
        if reduction_enabled:
            scale = float(getattr(context.scene, "bname_coma_camera_preview_scale_percentage", 100.0)) / 100.0
            pencil4_link.apply_scale(scale, ensure_saved=False)
        if count <= 0:
            self.report({"INFO"}, "Pencil+4 線幅ノードは見つかりませんでした")
        else:
            self.report({"INFO"}, f"Pencil+4 線幅を保存しました: {count}件")
        return {"FINISHED"}


class BNAME_OT_fisheye_set_reduction_scale(Operator):
    bl_idname = "bname.fisheye_set_reduction_scale"
    bl_label = "縮小率を設定"
    bl_options = {"REGISTER", "UNDO"}

    percentage: FloatProperty(name="縮小率", default=12.5, min=1.0, max=100.0)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _is_coma_mode(context) and getattr(context, "scene", None) is not None

    def execute(self, context):
        scene = context.scene
        scene.bname_coma_camera_preview_scale_percentage = float(self.percentage)
        coma_camera.apply_reduction_mode(context)
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_fisheye_save_pencil4_widths,
    BNAME_OT_fisheye_set_reduction_scale,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
