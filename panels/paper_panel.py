"""N-Panel の B-Name タブ: 用紙設定・セーフラインオーバーレイ."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.work import get_work

B_NAME_CATEGORY = "B-Name"


class BNAME_PT_paper(Panel):
    bl_idname = "BNAME_PT_paper"
    bl_label = "用紙"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 2
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
        p = work.paper

        # プリセット操作 (ドロップダウンから選択 → 即時適用)
        row = layout.row(align=True)
        row.label(text="プリセット", icon="PRESET")
        wm = context.window_manager
        row.prop(wm, "bname_paper_preset_selector", text="")
        row.operator("bname.paper_preset_save_local", text="", icon="FILE_TICK")

        box = layout.box()
        box.label(text="キャンバス")
        row = box.row(align=True)
        row.prop(p, "canvas_width_mm")
        row.prop(p, "canvas_height_mm")
        row = box.row(align=True)
        row.prop(p, "dpi")
        row.prop(p, "unit", text="")

        box = layout.box()
        box.label(text="仕上がり / 裁ち落とし")
        row = box.row(align=True)
        row.prop(p, "finish_width_mm")
        row.prop(p, "finish_height_mm")
        box.prop(p, "bleed_mm")

        box = layout.box()
        box.label(text="基本枠")
        row = box.row(align=True)
        row.prop(p, "inner_frame_width_mm")
        row.prop(p, "inner_frame_height_mm")
        row = box.row(align=True)
        row.prop(p, "inner_frame_offset_x_mm")
        row.prop(p, "inner_frame_offset_y_mm")

        box = layout.box()
        box.label(text="セーフライン")
        row = box.row(align=True)
        row.prop(p, "safe_top_mm")
        row.prop(p, "safe_bottom_mm")
        row = box.row(align=True)
        row.prop(p, "safe_gutter_mm")
        row.prop(p, "safe_fore_edge_mm")
        # セーフライン外塗り (旧「セーフライン外オーバーレイ」パネル) を統合
        sa = work.safe_area_overlay
        row = box.row(align=True)
        row.prop(sa, "enabled", text="セーフライン外を塗る")
        sub = box.row(align=True)
        sub.enabled = sa.enabled
        sub.prop(sa, "color", text="塗りつぶし色")

        box = layout.box()
        box.label(text="色")
        box.prop(p, "paper_color", text="用紙色")

        # 綴じ / 読む方向
        box = layout.box()
        box.label(text="綴じ / 読む方向")
        box.prop(p, "start_side")
        box.prop(p, "read_direction")

        box = layout.box()
        box.label(text="原稿上の表示")
        info = work.work_info
        _draw_display_item(box, "作品名", info.display_work_name)
        _draw_display_item(box, "話数", info.display_episode)
        _draw_display_item(box, "サブタイトル", info.display_subtitle)
        _draw_display_item(box, "作者名", info.display_author)
        _draw_display_item(box, "ページ番号", info.display_page_number)

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
    BNAME_PT_paper,
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
