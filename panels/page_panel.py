"""N-Panel の B-Name タブ: ページ一覧 (UIList) + 操作ボタン."""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from ..core.work import get_work

B_NAME_CATEGORY = "B-Name"


class BNAME_UL_pages(UIList):
    """ページ一覧 (サムネイルは Phase 1-E でテクスチャ化、Phase 1-D は文字表示)."""

    bl_idname = "BNAME_UL_pages"

    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_propname,
        index,
    ):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            icon_name = "FILE_IMAGE" if not item.spread else "IMGDISPLAY"
            row.label(text=f"{item.id}", icon=icon_name)
            row.prop(item, "title", text="", emboss=False)
            if item.spread:
                row.label(text="見開き", icon="ARROW_LEFTRIGHT")
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text=item.id)


class BNAME_PT_pages(Panel):
    bl_idname = "BNAME_PT_pages"
    bl_label = "ページ一覧"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 5
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded)

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        if work is None:
            return

        row = layout.row()
        row.template_list(
            BNAME_UL_pages.bl_idname,
            "",
            work,
            "pages",
            work,
            "active_page_index",
            rows=6,
        )
        col = row.column(align=True)
        col.operator("bname.page_add", text="", icon="ADD")
        col.operator("bname.page_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("bname.page_duplicate", text="", icon="DUPLICATE")
        col.separator()
        op = col.operator("bname.page_move", text="", icon="TRIA_UP")
        op.direction = -1
        op = col.operator("bname.page_move", text="", icon="TRIA_DOWN")
        op.direction = 1

        # 見開き操作
        box = layout.box()
        box.label(text="見開き")
        row = box.row(align=True)
        row.operator("bname.pages_merge_spread", text="変更", icon="ARROW_LEFTRIGHT")
        row.operator("bname.pages_split_spread", text="解除", icon="UNLINKED")

        # アクティブページ情報
        idx = work.active_page_index
        if 0 <= idx < len(work.pages):
            entry = work.pages[idx]
            box = layout.box()
            box.label(text=f"選択: {entry.id}  コマ数: {entry.panel_count}")
            if entry.spread:
                box.label(text=f"見開き: 間隔 {entry.tombo_gap_mm:.2f}mm")

        # ビュー操作 (真正面表示 / 全ページ一覧)
        scene = context.scene
        box = layout.box()
        box.label(text="ビュー", icon="VIEW_CAMERA")
        row = box.row(align=True)
        row.operator("bname.view_fit_page", text="ページに合わせる", icon="ZOOM_SELECTED")
        row.operator("bname.view_fit_all", text="全ページを一覧", icon="IMGDISPLAY")
        if getattr(scene, "bname_overview_mode", False):
            row = box.row(align=True)
            row.prop(scene, "bname_overview_cols", text="列数")
            row.prop(scene, "bname_overview_gap_mm", text="間隔mm")
            box.operator(
                "bname.view_overview_toggle",
                text="一覧モードを終了",
                icon="CANCEL",
            )


_CLASSES = (
    BNAME_UL_pages,
    BNAME_PT_pages,
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
