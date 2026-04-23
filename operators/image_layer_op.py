"""画像レイヤー追加/削除/読み込み Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper

from ..utils import log

_logger = log.get_logger(__name__)


def _get_collection(scene):
    return getattr(scene, "bname_image_layers", None)


class BNAME_OT_image_layer_add(Operator, ImportHelper):
    """画像ファイルを選択して新規画像レイヤーを追加."""

    bl_idname = "bname.image_layer_add"
    bl_label = "画像レイヤーを追加"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(  # type: ignore[valid-type]
        default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.psd;*.bmp",
        options={"HIDDEN"},
    )

    def execute(self, context):
        coll = _get_collection(context.scene)
        if coll is None:
            self.report({"ERROR"}, "image_layers 未初期化")
            return {"CANCELLED"}
        path = Path(self.filepath)
        if not path.is_file():
            self.report({"ERROR"}, f"ファイルが見つかりません: {path}")
            return {"CANCELLED"}
        entry = coll.add()
        entry.id = f"image_{len(coll):04d}"
        entry.title = path.stem
        entry.filepath = str(path)
        context.scene.bname_active_image_layer_index = len(coll) - 1

        # Blender 側に Image を読み込み (draw_handler 側で gpu.texture として使う)
        try:
            img = bpy.data.images.load(str(path), check_existing=True)
            entry.width_mm = max(1.0, img.size[0] / 6.0)  # 概算 (600dpi想定: px→mm)
            entry.height_mm = max(1.0, img.size[1] / 6.0)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("failed to load image: %s", exc)

        self.report({"INFO"}, f"画像レイヤー追加: {entry.title}")
        return {"FINISHED"}


class BNAME_OT_image_layer_remove(Operator):
    bl_idname = "bname.image_layer_remove"
    bl_label = "画像レイヤーを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        coll = _get_collection(context.scene)
        if coll is None:
            return False
        idx = getattr(context.scene, "bname_active_image_layer_index", -1)
        return 0 <= idx < len(coll)

    def execute(self, context):
        coll = _get_collection(context.scene)
        idx = context.scene.bname_active_image_layer_index
        if not (0 <= idx < len(coll)):
            return {"CANCELLED"}
        name = coll[idx].title
        coll.remove(idx)
        if len(coll) == 0:
            context.scene.bname_active_image_layer_index = -1
        elif idx >= len(coll):
            context.scene.bname_active_image_layer_index = len(coll) - 1
        self.report({"INFO"}, f"画像レイヤー削除: {name}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_image_layer_add,
    BNAME_OT_image_layer_remove,
)


def register() -> None:
    from ..core.image_layer import BNameImageLayer

    bpy.types.Scene.bname_image_layers = bpy.props.CollectionProperty(type=BNameImageLayer)
    bpy.types.Scene.bname_active_image_layer_index = bpy.props.IntProperty(default=-1, min=-1)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    for attr in ("bname_active_image_layer_index", "bname_image_layers"):
        try:
            delattr(bpy.types.Scene, attr)
        except AttributeError:
            pass
