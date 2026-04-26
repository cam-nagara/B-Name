"""統合レイヤーリストの選択・並び替え・削除 Operator."""

from __future__ import annotations

import bpy
from bpy.props import EnumProperty, IntProperty
from bpy.types import Operator

from ..utils import layer_stack as layer_stack_utils


class BNAME_OT_layer_stack_select(Operator):
    bl_idname = "bname.layer_stack_select"
    bl_label = "レイヤーを選択"
    bl_options = {"REGISTER"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bname_layer_stack", None) is not None

    def execute(self, context):
        if not layer_stack_utils.select_stack_index(context, self.index):
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_layer_stack_move(Operator):
    bl_idname = "bname.layer_stack_move"
    bl_label = "レイヤー順を変更"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(  # type: ignore[valid-type]
        items=(
            ("FRONT", "最前面", ""),
            ("UP", "前面へ", ""),
            ("DOWN", "背面へ", ""),
            ("BACK", "最背面", ""),
        ),
        default="UP",
    )

    @classmethod
    def poll(cls, context):
        stack = getattr(context.scene, "bname_layer_stack", None)
        return stack is not None and len(stack) > 0

    def execute(self, context):
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        stack = context.scene.bname_layer_stack
        idx = int(getattr(context.scene, "bname_active_layer_stack_index", -1))
        if not (0 <= idx < len(stack)):
            return {"CANCELLED"}
        if self.direction == "FRONT":
            new_idx = 0
        elif self.direction == "BACK":
            new_idx = len(stack) - 1
        elif self.direction == "UP":
            new_idx = idx - 1
        else:
            new_idx = idx + 1
        if not layer_stack_utils.move_stack_item(context, idx, new_idx):
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_layer_stack_delete(Operator):
    bl_idname = "bname.layer_stack_delete"
    bl_label = "レイヤーを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        stack = getattr(context.scene, "bname_layer_stack", None)
        idx = int(getattr(context.scene, "bname_active_layer_stack_index", -1))
        return stack is not None and 0 <= idx < len(stack)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        idx = int(getattr(context.scene, "bname_active_layer_stack_index", -1))
        if not layer_stack_utils.delete_stack_index(context, idx):
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_layer_stack_enter_panel(Operator):
    bl_idname = "bname.layer_stack_enter_panel"
    bl_label = "コマ編集へ"
    bl_options = {"REGISTER"}

    stack_index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bname_layer_stack", None) is not None

    def execute(self, context):
        if not layer_stack_utils.select_stack_index(context, self.stack_index):
            return {"CANCELLED"}
        item = layer_stack_utils.active_stack_item(context)
        if item is None or item.kind != "panel":
            return {"CANCELLED"}
        return bpy.ops.bname.enter_panel_mode("EXEC_DEFAULT")


_CLASSES = (
    BNAME_OT_layer_stack_select,
    BNAME_OT_layer_stack_move,
    BNAME_OT_layer_stack_delete,
    BNAME_OT_layer_stack_enter_panel,
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
