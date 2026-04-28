"""カスタム右クリックコンテキストメニュー (計画書 3.4.5 / 8.13).

3D View のオブジェクト右クリックメニューに B-Name サブメニューを
追加し、「リンク元を開く」「リンクを記録」オペレータを呼び出せるように
する。
"""

from __future__ import annotations

import bpy
from bpy.types import Menu

from ..utils import layer_stack as layer_stack_utils


def _active_stack_kind(context) -> str:
    item = layer_stack_utils.active_stack_item(context)
    return str(getattr(item, "kind", "") or "") if item is not None else ""


def _has_active_stack_item(context) -> bool:
    return bool(_active_stack_kind(context))


class BNAME_MT_selection_context(Menu):
    bl_idname = "BNAME_MT_selection_context"
    bl_label = "B-Name"

    def draw(self, context):
        layout = self.layout
        enabled = _has_active_stack_item(context)
        column = layout.column()
        column.enabled = enabled
        column.operator("bname.layer_stack_duplicate", text="複製", icon="DUPLICATE")
        column.operator("bname.layer_stack_delete", text="削除", icon="TRASH")
        if _active_stack_kind(context) == "effect":
            layout.separator()
            layout.operator("bname.effect_line_create_linked", text="リンク効果線を作成", icon="LINKED")


def open_selection_context_menu() -> bool:
    try:
        bpy.ops.wm.call_menu(name=BNAME_MT_selection_context.bl_idname)
        return True
    except Exception:  # noqa: BLE001
        return False


class BNAME_MT_object_context(Menu):
    bl_idname = "BNAME_MT_object_context"
    bl_label = "B-Name"

    def draw(self, context):
        layout = self.layout
        layout.menu(BNAME_MT_selection_context.bl_idname, icon="RESTRICT_SELECT_OFF")
        layout.separator()
        layout.operator("bname.open_link_source", icon="FILE_BLEND")
        layout.operator("bname.record_asset_link", icon="LINKED")
        layout.separator()
        layout.operator("bname.coma_update_thumb", icon="IMAGE")
        layout.operator("bname.coma_generate_preview", icon="RESTRICT_RENDER_OFF")


def _draw_in_object_context(self, context):
    self.layout.separator()
    self.layout.menu(BNAME_MT_object_context.bl_idname, icon="OUTLINER_OB_GROUP_INSTANCE")


_CLASSES = (BNAME_MT_selection_context, BNAME_MT_object_context)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_object_context_menu.append(_draw_in_object_context)


def unregister() -> None:
    try:
        bpy.types.VIEW3D_MT_object_context_menu.remove(_draw_in_object_context)
    except (ValueError, AttributeError):
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
