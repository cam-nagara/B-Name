"""N-Panel の B-Name タブ: 作品情報・作品操作."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.work import get_work

B_NAME_CATEGORY = "B-Name"


class BNAME_PT_work(Panel):
    bl_idname = "BNAME_PT_work"
    bl_label = "作品"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        work = get_work(context)

        # ツールバー: 新規 / 開く / 保存 / 閉じる
        row = layout.row(align=True)
        row.operator("bname.work_new", text="新規", icon="FILE_NEW")
        row.operator("bname.work_open", text="開く", icon="FILE_FOLDER")
        row.operator("bname.work_save", text="保存", icon="FILE_TICK")
        row.operator("bname.work_close", text="閉じる", icon="X")

        if work is None or not work.loaded:
            layout.label(text="作品が開かれていません", icon="INFO")
            return

        box = layout.box()
        box.label(text="作品情報", icon="WORDWRAP_ON")
        info = work.work_info
        box.prop(info, "work_name")
        box.prop(info, "episode_number")
        box.prop(info, "subtitle")
        box.prop(info, "author")

        box = layout.box()
        box.label(text="原稿上の表示")
        _draw_display_item(box, "作品名", info.display_work_name)
        _draw_display_item(box, "話数", info.display_episode)
        _draw_display_item(box, "サブタイトル", info.display_subtitle)
        _draw_display_item(box, "作者名", info.display_author)
        # ページ番号 (旧ノンブル UI の後継)
        _draw_display_item(box, "ページ番号", info.display_page_number)
        sub = box.row(align=True)
        sub.enabled = info.display_page_number.enabled
        sub.prop(info, "page_number_start")

        # コマ間隔 (作品共通、旧「コマ間隔」セクション)
        box = layout.box()
        box.label(text="コマ間隔")
        g = work.panel_gap
        row = box.row(align=True)
        row.prop(g, "vertical_mm")
        row.prop(g, "horizontal_mm")


def _draw_display_item(layout, label: str, item) -> None:
    row = layout.row(align=True)
    row.prop(item, "enabled", text=label)
    sub = row.row(align=True)
    sub.enabled = item.enabled
    sub.prop(item, "position", text="")
    sub.prop(item, "font_size_q", text="")


_CLASSES = (
    BNAME_PT_work,
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
