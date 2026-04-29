"""カスタム右クリックコンテキストメニュー (計画書 3.4.5 / 8.13)."""

from __future__ import annotations

import bpy
from bpy.types import Menu

from ..utils import layer_stack as layer_stack_utils


def _active_stack_kind(context) -> str:
    item = layer_stack_utils.active_stack_item(context)
    return str(getattr(item, "kind", "") or "") if item is not None else ""


def _has_active_stack_item(context) -> bool:
    return bool(_active_stack_kind(context))


def _active_stack_index(context) -> int:
    return int(getattr(context.scene, "bname_active_layer_stack_index", -1))


def selection_command_items(context) -> list[dict[str, object]]:
    item = layer_stack_utils.active_stack_item(context)
    if item is None:
        return [{"label": "対象が選択されていません", "enabled": False, "kind": "label"}]
    active_kind = str(getattr(item, "kind", "") or "")
    return [
        {"label": "詳細設定", "enabled": True, "kind": "operator", "operator": "bname.layer_stack_detail"},
        {"label": "複製", "enabled": True, "kind": "operator", "operator": "bname.layer_stack_duplicate"},
        {
            "label": "リンク複製",
            "enabled": active_kind == "effect",
            "kind": "operator" if active_kind == "effect" else "label",
            "operator": "bname.effect_line_create_linked",
        },
        {"label": "削除", "enabled": True, "kind": "operator", "operator": "bname.layer_stack_delete"},
    ]


def _draw_selection_commands(layout, context) -> None:
    items = selection_command_items(context)
    if len(items) == 1 and items[0].get("kind") == "label":
        layout.label(text=str(items[0]["label"]), icon="INFO")
        return
    active_index = _active_stack_index(context)
    column = layout.column()
    detail_row = column.row()
    detail_row.operator_context = "INVOKE_DEFAULT"
    detail = detail_row.operator("bname.layer_stack_detail", text="詳細設定", icon="PREFERENCES")
    detail.index = active_index
    detail.preserve_edge_selection = True

    column.operator("bname.layer_stack_duplicate", text="複製", icon="DUPLICATE")
    link_item = next((item for item in items if item.get("operator") == "bname.effect_line_create_linked"), None)
    if link_item is not None and bool(link_item.get("enabled", False)):
        column.operator("bname.effect_line_create_linked", text="リンク複製", icon="LINKED")
    else:
        link_row = column.row()
        link_row.enabled = False
        link_row.label(text="リンク複製", icon="LINKED")

    delete_row = column.row()
    delete_row.operator_context = "INVOKE_DEFAULT"
    delete_row.operator("bname.layer_stack_delete", text="削除", icon="TRASH")


class BNAME_MT_selection_context(Menu):
    bl_idname = "BNAME_MT_selection_context"
    bl_label = "B-Name"

    def draw(self, context):
        _draw_selection_commands(self.layout, context)


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
        _draw_selection_commands(layout, context)
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
