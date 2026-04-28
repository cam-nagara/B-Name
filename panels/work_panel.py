"""N-Panel の B-Name タブ: 作品情報・作品操作."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_PAGE, MODE_COMA, get_mode
from ..core.work import get_work

B_NAME_CATEGORY = "B-Name"


class BNAME_PT_work(Panel):
    bl_idname = "BNAME_PT_work"
    bl_label = "作品"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return get_mode(context) != MODE_COMA

    def draw(self, context):
        layout = self.layout
        work = get_work(context)

        # ツールバー: 新規 / 開く
        row = layout.row(align=True)
        row.operator("bname.work_new", text="新規", icon="FILE_NEW")
        row.operator("bname.work_open", text="開く", icon="FILE_FOLDER")

        if work is None or not work.loaded:
            layout.label(text="作品が開かれていません", icon="INFO")
            return

        mode = get_mode(context)

        box = layout.box()
        box.label(text="作品情報", icon="WORDWRAP_ON")
        info = work.work_info
        box.prop(info, "work_name")
        box.prop(info, "episode_number")
        box.prop(info, "subtitle")
        box.prop(info, "author")
        box.label(text="ページ数")
        row = box.row(align=True)
        row.enabled = mode == MODE_PAGE
        row.prop(info, "page_number_start", text="開始")
        row.prop(info, "page_number_end", text="終了")

        box = layout.box()
        box.label(text="コマ3Dテンプレート", icon="FILE_BLEND")
        box.enabled = mode == MODE_PAGE
        box.prop(work, "coma_blend_template_path", text="")


class BNAME_PT_coma_return(Panel):
    bl_idname = "BNAME_PT_coma_return"
    bl_label = "ページ一覧に戻る"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 0

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded and get_mode(context) == MODE_COMA)

    def draw(self, context):
        layout = self.layout
        layout.operator("bname.exit_coma_mode", text="ページ一覧に戻る", icon="BACK")
        layout.separator()
        layout.prop(context.scene, "bname_page_browser_position", text="ページ一覧位置")
        layout.prop(context.scene, "bname_page_browser_size", text="サイズ")
        layout.prop(context.scene, "bname_page_browser_fit", text="フィット")
        layout.operator("bname.page_browser_workspace", text="ページ一覧ビューを開く", icon="WINDOW")


_CLASSES = (
    BNAME_PT_work,
    BNAME_PT_coma_return,
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
