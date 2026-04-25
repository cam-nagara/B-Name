"""Grease Pencil パネル — master GP (作品全ページ共通) のレイヤー管理 UI.

新仕様:
- 作品全体で 1 つの master GP オブジェクト (bname_master_sketch)
- 各レイヤーは複数ページに横断的に存在 (CSP のレイヤーパネル感覚)
- 「ページ GP 一覧」は廃止 (master GP 1 つだけなので不要)
- レイヤー一覧で各レイヤーの不透明度をスライダー調整可能
"""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.work import get_work
from ..utils import gpencil as gp_utils

B_NAME_CATEGORY = "B-Name"
_GP_OBJECT_TYPE = "GREASEPENCIL"
_GP_PAINT_MODE = "PAINT_GREASE_PENCIL"
_GP_EDIT_MODE = "EDIT"
_GP_OBJECT_MODE = "OBJECT"


def _master_gp_object():
    """master GP オブジェクト (なければ None)."""
    return gp_utils.get_master_gpencil()


def _get_prefs():
    try:
        from ..preferences import get_preferences

        return get_preferences()
    except Exception:  # noqa: BLE001
        return None


class BNAME_PT_gpencil(Panel):
    """master GP のレイヤー / モード / 描画色管理 UI."""

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

        # --- カーソル追従トグル (active_page_index 追従用) ---
        prefs = _get_prefs()
        if prefs is not None:
            box = layout.box()
            row = box.row(align=True)
            row.label(text="カーソル追従", icon="RESTRICT_SELECT_OFF")
            row.prop(prefs, "gpencil_follow_cursor", text="")
            row.operator("bname.gpencil_follow_cursor", text="切替")

        if work is None or not work.loaded:
            layout.label(text="作品を開いてください", icon="INFO")
            return

        # master GP の確保ボタン
        layout.operator(
            "bname.gpencil_master_ensure",
            text="マスター GP を用意",
            icon="OUTLINER_OB_GREASEPENCIL",
        )

        obj = _master_gp_object()
        if obj is None:
            layout.label(text="(マスター GP が未生成です)", icon="INFO")
            return

        row = layout.row(align=True)
        row.label(text=obj.name, icon="OUTLINER_OB_GREASEPENCIL")

        # モード切替 (3 ボタン) — wrapper 経由で必ず master GP を active 化
        row = layout.row(align=True)
        op = row.operator(
            "bname.gpencil_master_mode_set",
            text="オブジェクト", depress=(obj.mode == _GP_OBJECT_MODE),
        )
        op.mode = _GP_OBJECT_MODE
        op = row.operator(
            "bname.gpencil_master_mode_set",
            text="描画", depress=(obj.mode == _GP_PAINT_MODE),
        )
        op.mode = _GP_PAINT_MODE
        op = row.operator(
            "bname.gpencil_master_mode_set",
            text="編集", depress=(obj.mode == _GP_EDIT_MODE),
        )
        op.mode = _GP_EDIT_MODE

        gp_data = obj.data

        # --- レイヤー一覧 (各レイヤーで不透明度スライダー) ---
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
                # 不透明度スライダー (レイヤー行内)
                if hasattr(layer, "opacity"):
                    sub = col.row(align=True)
                    sub.separator(factor=2.0)
                    sub.prop(layer, "opacity", text="不透明度", slider=True)

        row = box.row(align=True)
        row.operator("bname.gpencil_layer_add", text="追加", icon="ADD")
        row.operator("bname.gpencil_layer_remove", text="削除", icon="REMOVE")

        # 描画色 (アクティブマテリアル直結)
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
            color_box.label(text="マテリアルスロット")
            color_box.template_list(
                "MATERIAL_UL_matslots", "", obj, "material_slots",
                obj, "active_material_index", rows=3,
            )

        # アクティブレイヤーの追加プロパティ
        if active_layer is not None:
            prop_box = layout.box()
            prop_box.label(text=f"アクティブ: {active_layer.name}")
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


class BNAME_OT_gpencil_master_ensure(bpy.types.Operator):
    """master GP オブジェクトを ensure (生成 or 既存取得) して active 化."""

    bl_idname = "bname.gpencil_master_ensure"
    bl_label = "マスター GP を用意"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        if scene is None:
            return {"CANCELLED"}
        try:
            obj = gp_utils.ensure_master_gpencil(scene)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"master GP 生成失敗: {exc}")
            return {"CANCELLED"}
        vl = context.view_layer
        if vl is not None and obj is not None:
            try:
                vl.objects.active = obj
                obj.select_set(True)
            except Exception:  # noqa: BLE001
                pass
        return {"FINISHED"}


class BNAME_OT_gpencil_master_mode_set(bpy.types.Operator):
    """master GP を必ず active 化してからモード切替する wrapper.

    UI のモード切替ボタンは ``bpy.ops.object.mode_set`` を直接呼ぶと、
    view_layer.objects.active が master GP でない場合に意図しない
    オブジェクトのモードが切り替わる。この wrapper で必ず master GP を
    active 化してから mode_set を呼ぶ。
    """

    bl_idname = "bname.gpencil_master_mode_set"
    bl_label = "マスター GP モード切替"
    bl_options = {"REGISTER", "INTERNAL"}

    mode: bpy.props.StringProperty(default="OBJECT")  # type: ignore[valid-type]

    def execute(self, context):
        obj = gp_utils.get_master_gpencil()
        if obj is None:
            try:
                obj = gp_utils.ensure_master_gpencil(context.scene)
            except Exception:  # noqa: BLE001
                return {"CANCELLED"}
        vl = context.view_layer
        if vl is not None:
            try:
                vl.objects.active = obj
                obj.select_set(True)
            except Exception:  # noqa: BLE001
                pass
        try:
            bpy.ops.object.mode_set(mode=self.mode)
        except Exception as exc:  # noqa: BLE001
            self.report({"WARNING"}, f"モード切替失敗: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_gpencil_master_ensure,
    BNAME_OT_gpencil_master_mode_set,
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
