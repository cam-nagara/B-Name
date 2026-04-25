"""Grease Pencil パネル — ページ単位 GP (Phase 2) の管理 UI.

- アクティブページの GP を確保するボタン
- カーソル追従 (follow_cursor) トグル
- アクティブ GP のレイヤー一覧・モード切替・ブラシ選択
- 全ページ GP の一覧 (各行で選択 → view_layer.objects.active を切替)
"""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.work import get_active_page, get_work
from ..utils import gpencil as gp_utils

B_NAME_CATEGORY = "B-Name"
_GP_OBJECT_TYPE = "GREASEPENCIL"
_GP_PAINT_MODE = "PAINT_GREASE_PENCIL"
_GP_EDIT_MODE = "EDIT"
_GP_OBJECT_MODE = "OBJECT"


def _active_gp_object(context):
    obj = context.active_object
    if obj is not None and obj.type == _GP_OBJECT_TYPE:
        return obj
    return None


def _get_prefs():
    try:
        from ..preferences import get_preferences

        return get_preferences()
    except Exception:  # noqa: BLE001
        return None


class BNAME_OT_gpencil_select_page(bpy.types.Operator):
    """指定ページの GP オブジェクトを view_layer.active に切替 + active_page_index 更新."""

    bl_idname = "bname.gpencil_select_page"
    bl_label = "このページの GP を選択"
    bl_options = {"REGISTER"}

    page_id: bpy.props.StringProperty(default="")  # type: ignore[valid-type]

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        for i, pg in enumerate(work.pages):
            if pg.id == self.page_id:
                work.active_page_index = i
                break
        else:
            return {"CANCELLED"}
        # ページ GP が未生成ならここで生成 (ensure)
        obj = gp_utils.get_page_gpencil(self.page_id)
        if obj is None:
            try:
                obj = gp_utils.ensure_page_gpencil(context.scene, self.page_id)
            except Exception:  # noqa: BLE001
                return {"CANCELLED"}
        vl = context.view_layer
        if vl is not None:
            try:
                vl.objects.active = obj
                obj.select_set(True)
            except Exception:  # noqa: BLE001
                pass
        return {"FINISHED"}


class BNAME_PT_gpencil(Panel):
    """ネーム作画用 Grease Pencil パネル (Phase 2 ページ単位)."""

    bl_idname = "BNAME_PT_gpencil"
    bl_label = "Grease Pencil"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 13
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        page = get_active_page(context)

        # --- カーソル追従トグル ---
        prefs = _get_prefs()
        box = layout.box()
        row = box.row(align=True)
        row.label(text="カーソル追従", icon="RESTRICT_SELECT_OFF")
        if prefs is not None:
            row.prop(prefs, "gpencil_follow_cursor", text="")
        row.operator("bname.gpencil_follow_cursor", text="切替")

        # --- アクティブページ用ボタン ---
        if work is None or not work.loaded:
            layout.label(text="作品を開いてください", icon="INFO")
            return
        if page is None:
            layout.label(text="ページを選択してください", icon="INFO")
            return

        row = layout.row(align=True)
        row.operator(
            "bname.gpencil_page_ensure",
            text=f"{page.id} の GP を用意",
            icon="OUTLINER_OB_GREASEPENCIL",
        )

        # --- 全ページ GP 一覧 ---
        if len(work.pages) > 0:
            list_box = layout.box()
            list_box.label(text="ページ GP 一覧", icon="FILE_IMAGE")
            col = list_box.column(align=True)
            active_obj = _active_gp_object(context)
            active_name = active_obj.name if active_obj is not None else ""
            for pg in work.pages:
                obj_name = gp_utils.page_gp_object_name(pg.id)
                exists = bpy.data.objects.get(obj_name) is not None
                row = col.row(align=True)
                is_active = (obj_name == active_name)
                select_icon = "RADIOBUT_ON" if is_active else "RADIOBUT_OFF"
                op = row.operator(
                    "bname.gpencil_select_page",
                    text=pg.id + ("" if exists else " (未生成)"),
                    icon=select_icon,
                    emboss=False,
                )
                op.page_id = pg.id

        # --- アクティブ GP の詳細 ---
        obj = _active_gp_object(context)
        if obj is None:
            layout.label(text="(GP がアクティブではありません)", icon="INFO")
            return

        row = layout.row(align=True)
        row.label(text=obj.name, icon="OUTLINER_OB_GREASEPENCIL")

        # モード切替 (3 ボタン)
        row = layout.row(align=True)
        op = row.operator("object.mode_set", text="オブジェクト", depress=(obj.mode == _GP_OBJECT_MODE))
        op.mode = _GP_OBJECT_MODE
        op = row.operator("object.mode_set", text="描画", depress=(obj.mode == _GP_PAINT_MODE))
        op.mode = _GP_PAINT_MODE
        op = row.operator("object.mode_set", text="編集", depress=(obj.mode == _GP_EDIT_MODE))
        op.mode = _GP_EDIT_MODE

        gp_data = obj.data

        # レイヤー一覧
        box = layout.box()
        box.label(text="レイヤー", icon="RENDERLAYERS")
        layers = getattr(gp_data, "layers", None)
        if layers is None:
            box.label(text="(GP v3 layers にアクセスできません)", icon="ERROR")
            return

        active_layer = getattr(layers, "active", None)
        if len(layers) == 0:
            box.label(text="(レイヤーがありません)")
        else:
            col = box.column(align=True)
            for layer in layers:
                row = col.row(align=True)
                is_active = (layer == active_layer)
                select_icon = "RADIOBUT_ON" if is_active else "RADIOBUT_OFF"
                op = row.operator(
                    "bname.gpencil_layer_select",
                    text="",
                    icon=select_icon,
                    emboss=False,
                )
                op.layer_name = layer.name
                row.prop(layer, "name", text="", emboss=False)
                if hasattr(layer, "hide"):
                    row.prop(
                        layer,
                        "hide",
                        text="",
                        emboss=False,
                        icon="HIDE_ON" if layer.hide else "HIDE_OFF",
                    )
                if hasattr(layer, "lock"):
                    row.prop(
                        layer,
                        "lock",
                        text="",
                        emboss=False,
                        icon="LOCKED" if layer.lock else "UNLOCKED",
                    )

        row = box.row(align=True)
        row.operator("bname.gpencil_layer_add", text="追加", icon="ADD")
        row.operator("bname.gpencil_layer_remove", text="削除", icon="REMOVE")

        # 描画色 (アクティブマテリアル直結. 次以降のストロークに反映される)
        color_box = layout.box()
        color_box.label(text="描画色 (アクティブマテリアル)", icon="COLOR")
        mats = getattr(obj.data, "materials", None)
        active_mat = None
        if mats is not None and len(mats) > 0:
            idx = getattr(obj, "active_material_index", 0)
            if 0 <= idx < len(mats):
                active_mat = mats[idx]
        if active_mat is None:
            color_box.label(text="(マテリアルなし)", icon="INFO")
        else:
            color_box.label(text=f"マテリアル: {active_mat.name}")
            gp_style = getattr(active_mat, "grease_pencil", None)
            if gp_style is not None:
                color_box.prop(gp_style, "color", text="ストローク色")
                color_box.prop(gp_style, "show_stroke", text="線を描く")
                if hasattr(gp_style, "fill_color"):
                    color_box.prop(gp_style, "fill_color", text="塗り色")
                if hasattr(gp_style, "show_fill"):
                    color_box.prop(gp_style, "show_fill", text="塗りを描く")
            else:
                color_box.label(text="(GP マテリアル未対応)", icon="ERROR")
            # マテリアルスロット一覧 + 追加
            color_box.label(text="マテリアルスロット")
            color_box.template_list(
                "MATERIAL_UL_matslots", "", obj, "material_slots",
                obj, "active_material_index", rows=3,
            )

        # アクティブレイヤーのプロパティ
        if active_layer is not None:
            prop_box = layout.box()
            prop_box.label(text=f"アクティブ: {active_layer.name}")
            if hasattr(active_layer, "opacity"):
                prop_box.prop(active_layer, "opacity")
            if hasattr(active_layer, "blend_mode"):
                prop_box.prop(active_layer, "blend_mode")
            if hasattr(active_layer, "tint_color"):
                prop_box.prop(active_layer, "tint_color")
            if hasattr(active_layer, "tint_factor"):
                prop_box.prop(active_layer, "tint_factor")

        # ブラシ (描画モード時のみ)
        if obj.mode == _GP_PAINT_MODE:
            ts = context.tool_settings
            paint = None
            for attr in (
                "gpencil_paint",
                "grease_pencil_paint",
                "gpencil_v3_paint",
            ):
                paint = getattr(ts, attr, None)
                if paint is not None:
                    break
            if paint is not None:
                brush_box = layout.box()
                brush_box.label(text="ブラシ", icon="BRUSH_DATA")
                try:
                    brush_box.template_ID(paint, "brush")
                except Exception:  # noqa: BLE001
                    if getattr(paint, "brush", None) is not None:
                        brush_box.label(text=paint.brush.name)
                brush = getattr(paint, "brush", None)
                if brush is not None:
                    if hasattr(brush, "size"):
                        brush_box.prop(brush, "size")
                    if hasattr(brush, "strength"):
                        brush_box.prop(brush, "strength")


_CLASSES = (
    BNAME_OT_gpencil_select_page,
    BNAME_PT_gpencil,
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
