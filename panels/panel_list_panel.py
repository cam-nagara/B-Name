"""コマ一覧パネル (UIList) + Z順序/モード切替 UI."""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from ..core.mode import MODE_PAGE, MODE_PANEL, get_mode
from ..core.work import get_active_page, get_work

B_NAME_CATEGORY = "B-Name"


class BNAME_OT_panel_enter_from_list(bpy.types.Operator):
    """UIList 行の「コマ編集へ」ボタン用: 指定 index のコマを選択してから enter_panel_mode."""

    bl_idname = "bname.panel_enter_from_list"
    bl_label = "このコマを編集"
    bl_options = {"REGISTER"}

    index: bpy.props.IntProperty(default=-1)  # type: ignore[valid-type]

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        if not (0 <= self.index < len(page.panels)):
            self.report({"ERROR"}, "コマ index が不正です")
            return {"CANCELLED"}
        page.active_panel_index = self.index
        # enter_panel_mode.execute は active panel を対象にするので、
        # ここで invoke ではなく execute 経由で呼び出せば ok。
        return bpy.ops.bname.enter_panel_mode("EXEC_DEFAULT")


class BNAME_UL_panels(UIList):
    bl_idname = "BNAME_UL_panels"

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
            row.label(text=item.panel_stem, icon="IMAGE_DATA")
            row.prop(item, "title", text="", emboss=False)
            row.label(text=f"z={item.z_order}")
            # 行内「コマ編集へ」ボタン (overview ダブルクリックと同等の導線)
            op = row.operator(
                "bname.panel_enter_from_list",
                text="",
                icon="PLAY",
                emboss=False,
            )
            op.index = index
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text=item.panel_stem)


class BNAME_PT_panels(Panel):
    bl_idname = "BNAME_PT_panels"
    bl_label = "コマ一覧"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 6
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return get_active_page(context) is not None

    def draw(self, context):
        layout = self.layout
        page = get_active_page(context)
        if page is None:
            layout.label(text="ページを選択してください", icon="INFO")
            return

        # モード表示
        mode = get_mode(context)
        box = layout.box()
        row = box.row(align=True)
        if mode == MODE_PAGE:
            row.label(text="紙面編集モード", icon="FILE_IMAGE")
            row.operator("bname.enter_panel_mode", text="コマ編集へ", icon="PLAY")
        else:
            stem = getattr(context.scene, "bname_current_panel_stem", "")
            row.label(text=f"コマ編集モード: {stem}", icon="IMAGE_DATA")
            row.operator("bname.exit_panel_mode", text="戻る", icon="BACK")

        row = layout.row()
        row.template_list(
            BNAME_UL_panels.bl_idname,
            "",
            page,
            "panels",
            page,
            "active_panel_index",
            rows=6,
        )
        col = row.column(align=True)
        col.operator("bname.panel_add", text="", icon="ADD")
        col.operator("bname.panel_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("bname.panel_duplicate", text="", icon="DUPLICATE")
        col.operator("bname.panel_move_to_page", text="", icon="FORWARD")

        # Z順序操作
        box = layout.box()
        box.label(text="Z順序")
        row = box.row(align=True)
        op = row.operator("bname.panel_z_order", text="最背面", icon="TRIA_DOWN_BAR")
        op.direction = "BACK"
        op = row.operator("bname.panel_z_order", text="背面へ", icon="TRIA_DOWN")
        op.direction = "BACKWARD"
        op = row.operator("bname.panel_z_order", text="前面へ", icon="TRIA_UP")
        op.direction = "FORWARD"
        op = row.operator("bname.panel_z_order", text="最前面", icon="TRIA_UP_BAR")
        op.direction = "FRONT"

        # 分割テンプレート
        box = layout.box()
        box.label(text="分割テンプレート")
        box.operator("bname.panel_split_template", text="縦横均等分割", icon="GRID")

        # 枠線カットツール (選択中コマを 2 つに分割)
        box = layout.box()
        box.label(text="枠線カット (選択中コマを分割)")
        row = box.row(align=True)
        op = row.operator("bname.panel_cut", text="水平カット (上下)", icon="SNAP_EDGE")
        op.axis = 0
        op = row.operator("bname.panel_cut", text="垂直カット (左右)", icon="SNAP_MIDPOINT")
        op.axis = 1
        # ナイフツール (ビューポートでドラッグして切断)
        box.operator(
            "bname.panel_knife_cut",
            text="ナイフツール (ドラッグで切断)",
            icon="SCULPTMODE_HLT",
        )


_CLASSES = (
    BNAME_OT_panel_enter_from_list,
    BNAME_UL_panels,
    BNAME_PT_panels,
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
