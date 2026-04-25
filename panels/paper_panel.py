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

        # 「色・線数」セクションは UI からは削除 (色情報は書き出し処理が
        # 内部で参照するためデータ層は維持。書き出しダイアログから個別指定可)
        # 綴じ / 読む方向
        box = layout.box()
        box.label(text="綴じ / 読む方向")
        box.prop(p, "start_side")
        box.prop(p, "read_direction")


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
