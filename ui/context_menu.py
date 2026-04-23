"""カスタム右クリックコンテキストメニュー (計画書 3.4.5 / 8.13).

3D View のオブジェクト右クリックメニューに B-Name サブメニューを
追加し、「リンク元を開く」「リンクを記録」オペレータを呼び出せるように
する。
"""

from __future__ import annotations

import bpy
from bpy.types import Menu


class BNAME_MT_object_context(Menu):
    bl_idname = "BNAME_MT_object_context"
    bl_label = "B-Name"

    def draw(self, context):
        layout = self.layout
        layout.operator("bname.open_link_source", icon="FILE_BLEND")
        layout.operator("bname.record_asset_link", icon="LINKED")
        layout.separator()
        layout.operator("bname.panel_update_thumb", icon="IMAGE")
        layout.operator("bname.panel_generate_preview", icon="RESTRICT_RENDER_OFF")


def _draw_in_object_context(self, context):
    self.layout.separator()
    self.layout.menu(BNAME_MT_object_context.bl_idname, icon="OUTLINER_OB_GROUP_INSTANCE")


_CLASSES = (BNAME_MT_object_context,)


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
