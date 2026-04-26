"""画像レイヤー追加/削除/読み込み Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper

from ..utils import log

_logger = log.get_logger(__name__)


def _get_collection(scene):
    return getattr(scene, "bname_image_layers", None)


def _allocate_image_id(coll) -> str:
    used = {entry.id for entry in coll}
    i = 1
    while True:
        candidate = f"image_{i:04d}"
        if candidate not in used:
            return candidate
        i += 1


class BNAME_OT_image_layer_add(Operator, ImportHelper):
    """画像ファイルを選択して新規画像レイヤーを追加.

    ``bl_label`` はファイル選択ダイアログの確定ボタン表記にも流用される
    ため「画像を選択」とする。N パネル側の起動ボタンはアイコンのみ
    (text="") で呼び出しているので、このラベル変更は UI に悪影響しない。
    """

    bl_idname = "bname.image_layer_add"
    bl_label = "画像を選択"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(  # type: ignore[valid-type]
        default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.psd;*.bmp",
        options={"HIDDEN"},
    )

    def invoke(self, context, event):
        # ImportHelper 既定挙動では ``self.filepath`` に現在の .blend (``work.blend``)
        # の絶対パスが流用されダイアログのファイル名欄に "work.blend" が
        # 表示される。画像フィルタでは選択できない拡張子なのでユーザーを
        # 混乱させるため、空に差し替える。
        self.filepath = ""
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

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
        entry.id = _allocate_image_id(coll)
        entry.title = path.stem
        entry.filepath = str(path)
        context.scene.bname_active_image_layer_index = len(coll) - 1
        if hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "image"

        # Blender 側に Image を読み込み (draw_handler 側で gpu.texture として使う)
        try:
            img = bpy.data.images.load(str(path), check_existing=True)
            entry.width_mm = max(1.0, img.size[0] / 6.0)  # 概算 (600dpi想定: px→mm)
            entry.height_mm = max(1.0, img.size[1] / 6.0)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("failed to load image: %s", exc)
        try:
            from ..core.work import get_active_page
            from .balloon_op import _creation_violates_layer_scope

            page = get_active_page(context)
            blocked = (
                page is not None
                and _creation_violates_layer_scope(
                    context,
                    page,
                    entry.x_mm,
                    entry.y_mm,
                    entry.width_mm,
                    entry.height_mm,
                )
            )
        except Exception:  # noqa: BLE001
            blocked = False
        if blocked:
            coll.remove(len(coll) - 1)
            context.scene.bname_active_image_layer_index = len(coll) - 1 if len(coll) else -1
            self.report({"ERROR"}, "このモードではその位置に画像レイヤーを作成できません")
            return {"CANCELLED"}

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
        if len(coll) == 0 and hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "gp"
        self.report({"INFO"}, f"画像レイヤー削除: {name}")
        return {"FINISHED"}


class BNAME_OT_image_layer_select(Operator):
    bl_idname = "bname.image_layer_select"
    bl_label = "画像レイヤーを選択"
    bl_options = {"REGISTER"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _get_collection(context.scene) is not None

    def execute(self, context):
        coll = _get_collection(context.scene)
        if coll is None or not (0 <= self.index < len(coll)):
            return {"CANCELLED"}
        context.scene.bname_active_image_layer_index = self.index
        if hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "image"
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_image_layer_add,
    BNAME_OT_image_layer_remove,
    BNAME_OT_image_layer_select,
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
    for attr in (
        "bname_active_image_layer_index",
        "bname_image_layers",
    ):
        try:
            delattr(bpy.types.Scene, attr)
        except AttributeError:
            pass
