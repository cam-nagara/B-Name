"""N-Panel の B-Name タブ: 作品情報・作品操作."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import Panel

from ..core.mode import MODE_PAGE, MODE_COMA, get_mode
from ..core.work import get_work
from ..utils import paths as _paths

B_NAME_CATEGORY = "B-Name"


def _current_blend_is_coma_blend() -> bool:
    """現在開いている .blend が ``pNNNN/cNN/cNN.blend`` 形式なら True.

    ``bname_mode`` / ``work.loaded`` が load_post の遅延や同期失敗で正しく
    セットされない場合の救済用。ファイルパス自体から「コマ編集中」かを
    判定して、ページ一覧へ戻る経路を必ず提供する。
    """
    fp = bpy.data.filepath
    if not fp:
        return False
    try:
        path = Path(fp).resolve()
    except OSError:
        return False
    parts = path.parts
    if len(parts) < 3:
        return False
    page_id, coma_id, fname = parts[-3], parts[-2], parts[-1]
    return (
        _paths.is_valid_page_id(page_id)
        and _paths.is_valid_coma_id(coma_id)
        and fname == f"{coma_id}.blend"
    )


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
        # 通常: モードが MODE_COMA + work.loaded
        work = get_work(context)
        if work and work.loaded and get_mode(context) == MODE_COMA:
            return True
        # フォールバック: load_post の遅延等でモードが同期できなくても、
        # 開いている .blend のパスが cNN.blend ならパネルを表示する。
        return _current_blend_is_coma_blend()

    def draw(self, context):
        layout = self.layout
        layout.operator(
            "bname.exit_coma_mode_safe",
            text="ページ一覧に戻る",
            icon="BACK",
        )
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
