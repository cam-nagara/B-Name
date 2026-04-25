"""フキダシ / テキストパネル (Phase 3 ページ単位対応)."""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from ..core.work import get_active_page

B_NAME_CATEGORY = "B-Name"


class BNAME_UL_balloons(UIList):
    bl_idname = "BNAME_UL_balloons"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            icon_name = "OUTLINER_OB_FONT" if item.shape == "none" else "MOD_FLUID"
            row.label(text=item.id, icon=icon_name)
            row.prop(item, "shape", text="", emboss=False)


class BNAME_UL_texts(UIList):
    bl_idname = "BNAME_UL_texts"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            row.label(text=item.id, icon="FONT_DATA")
            row.prop(item, "body", text="", emboss=False)
            if item.parent_balloon_id:
                row.label(text=f"→{item.parent_balloon_id}", icon="LINKED")
            else:
                row.label(text="独立", icon="UNLINKED")


class BNAME_PT_balloons(Panel):
    bl_idname = "BNAME_PT_balloons"
    bl_label = "フキダシ"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 10
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

        layout.label(
            text=f"ページ {page.id} のフキダシ: {len(page.balloons)} 件",
            icon="FILE_IMAGE",
        )

        row = layout.row()
        row.template_list(
            BNAME_UL_balloons.bl_idname,
            "",
            page,
            "balloons",
            page,
            "active_balloon_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("bname.balloon_add", text="", icon="ADD")
        col.operator("bname.balloon_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("bname.balloon_tail_add", text="", icon="PARTICLE_POINT")

        idx = page.active_balloon_index
        if not (0 <= idx < len(page.balloons)):
            return
        entry = page.balloons[idx]

        # 親子連動ヘルプ
        child_count = sum(1 for t in page.texts if t.parent_balloon_id == entry.id)
        if child_count > 0:
            layout.label(
                text=f"子テキスト {child_count} 件が連動",
                icon="LINKED",
            )

        box = layout.box()
        box.prop(entry, "shape")
        row = box.row(align=True)
        row.prop(entry, "x_mm")
        row.prop(entry, "y_mm")
        row = box.row(align=True)
        row.prop(entry, "width_mm")
        row.prop(entry, "height_mm")
        box.prop(entry, "rotation_deg")
        box.prop(entry, "rounded_corner_enabled")
        sub = box.row()
        sub.enabled = entry.rounded_corner_enabled
        sub.prop(entry, "rounded_corner_radius_mm")

        # 親子連動つき平行移動
        box = layout.box()
        box.label(text="親子連動移動 (子テキストも追随)", icon="CON_TRACKTO")
        row = box.row(align=True)
        op = row.operator("bname.balloon_move", text="← 5mm")
        op.delta_x_mm = -5.0
        op = row.operator("bname.balloon_move", text="→ 5mm")
        op.delta_x_mm = 5.0
        op = row.operator("bname.balloon_move", text="↑ 5mm")
        op.delta_y_mm = 5.0
        op = row.operator("bname.balloon_move", text="↓ 5mm")
        op.delta_y_mm = -5.0

        box = layout.box()
        box.label(text="線・塗り")
        box.prop(entry, "line_style")
        box.prop(entry, "line_width_mm")
        box.prop(entry, "line_color")
        box.prop(entry, "fill_color")

        # 形状別パラメータ
        sp = entry.shape_params
        if entry.shape == "cloud":
            box = layout.box()
            box.label(text="雲パラメータ")
            box.prop(sp, "cloud_wave_count")
            box.prop(sp, "cloud_wave_amplitude_mm")
        elif entry.shape in ("spike_curve", "spike_straight"):
            box = layout.box()
            box.label(text="トゲパラメータ")
            box.prop(sp, "spike_count")
            box.prop(sp, "spike_depth_mm")
            box.prop(sp, "spike_jitter")

        # 尻尾
        box = layout.box()
        box.label(text=f"尻尾 ({len(entry.tails)})")
        for i, tail in enumerate(entry.tails):
            sub = box.box()
            sub.label(text=f"尻尾 {i + 1}")
            sub.prop(tail, "type")
            sub.prop(tail, "direction_deg")
            sub.prop(tail, "length_mm")
            row = sub.row(align=True)
            row.prop(tail, "root_width_mm")
            row.prop(tail, "tip_width_mm")
            if tail.type == "curve":
                sub.prop(tail, "curve_bend")


class BNAME_PT_texts(Panel):
    bl_idname = "BNAME_PT_texts"
    bl_label = "テキスト"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 11
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

        layout.label(
            text=f"ページ {page.id} のテキスト: {len(page.texts)} 件",
            icon="FONT_DATA",
        )

        row = layout.row()
        row.template_list(
            BNAME_UL_texts.bl_idname,
            "",
            page,
            "texts",
            page,
            "active_text_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("bname.text_add", text="", icon="ADD")
        col.operator("bname.text_remove", text="", icon="REMOVE")

        idx = page.active_text_index
        if not (0 <= idx < len(page.texts)):
            return
        entry = page.texts[idx]

        box = layout.box()
        box.prop(entry, "body")
        box.prop(entry, "speaker_type")
        row = box.row(align=True)
        row.prop(entry, "x_mm")
        row.prop(entry, "y_mm")
        row = box.row(align=True)
        row.prop(entry, "width_mm")
        row.prop(entry, "height_mm")

        # 組版
        box = layout.box()
        box.label(text="組版", icon="FONT_DATA")
        box.prop(entry, "writing_mode")
        box.prop(entry, "font_size_pt")
        box.prop(entry, "color")
        row = box.row(align=True)
        row.prop(entry, "line_height")
        row.prop(entry, "letter_spacing")

        # 白フチ
        box = layout.box()
        box.prop(entry, "stroke_enabled")
        sub = box.column()
        sub.enabled = entry.stroke_enabled
        sub.prop(entry, "stroke_width_mm")
        sub.prop(entry, "stroke_color")

        # 親子連動
        box = layout.box()
        box.label(text="親フキダシ", icon="LINKED")
        row = box.row(align=True)
        row.prop(entry, "parent_balloon_id", text="ID")
        # 既存フキダシ一覧からのクイック選択
        if len(page.balloons) > 0:
            row = box.row(align=True)
            row.label(text="紐付け:")
            for b in page.balloons:
                op = row.operator("bname.text_attach_to_balloon", text=b.id)
                op.balloon_id = b.id
            # 独立化ボタン
            op = box.operator(
                "bname.text_attach_to_balloon",
                text="独立テキストにする",
                icon="UNLINKED",
            )
            op.balloon_id = ""


_CLASSES = (
    BNAME_UL_balloons,
    BNAME_UL_texts,
    BNAME_PT_balloons,
    BNAME_PT_texts,
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
