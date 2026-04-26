"""Grease Pencil パネル — master GP (作品全ページ共通) のレイヤー管理 UI.

新仕様:
- 作品全体で 1 つの master GP オブジェクト (bname_master_sketch)
- 各レイヤーは複数ページに横断的に存在 (CSP のレイヤーパネル感覚)
- 「ページ GP 一覧」は廃止 (master GP 1 つだけなので不要)
- 選択中レイヤーの不透明度 / 線色 / 塗り色を上部で調整
- マテリアルは内部実装として隠し、ユーザーにはレイヤー設定だけを見せる
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


def _indent(row, depth: int) -> None:
    if depth > 0:
        row.separator(factor=1.25 * depth)


def _draw_gp_layer_row(col, layer, active_layer, depth: int) -> None:
    row = col.row(align=True)
    _indent(row, depth)
    is_active = (layer == active_layer)
    select_icon = "RADIOBUT_ON" if is_active else "RADIOBUT_OFF"
    op = row.operator(
        "bname.gpencil_layer_select",
        text="",
        icon=select_icon,
        emboss=False,
    )
    op.layer_name = layer.name
    row.prop(layer, "name", text="")
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
    if getattr(layer, "parent_group", None) is not None:
        op = row.operator(
            "bname.gpencil_layer_move_to_folder",
            text="外",
            emboss=False,
        )
        op.layer_name = layer.name
        op.folder_name = ""


def _draw_gp_group_row(col, group, active_layer, depth: int) -> None:
    row = col.row(align=True)
    _indent(row, depth)
    expanded = bool(getattr(group, "is_expanded", True))
    row.prop(
        group,
        "is_expanded",
        text="",
        emboss=False,
        icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
    )
    row.prop(group, "name", text="")
    if hasattr(group, "hide"):
        row.prop(
            group,
            "hide",
            text="",
            emboss=False,
            icon="HIDE_ON" if group.hide else "HIDE_OFF",
        )
    if hasattr(group, "lock"):
        row.prop(
            group,
            "lock",
            text="",
            emboss=False,
            icon="LOCKED" if group.lock else "UNLOCKED",
        )
    if active_layer is not None and getattr(active_layer, "parent_group", None) != group:
        op = row.operator(
            "bname.gpencil_layer_move_to_folder",
            text="入",
            emboss=False,
        )
        op.layer_name = active_layer.name
        op.folder_name = group.name
    op = row.operator("bname.gpencil_folder_add", text="", icon="ADD")
    op.parent_folder_name = group.name
    op = row.operator("bname.gpencil_folder_remove", text="", icon="REMOVE")
    op.folder_name = group.name


def _draw_gp_layer_tree(col, nodes, active_layer, depth: int = 0) -> None:
    for node in nodes:
        if gp_utils.is_layer_group(node):
            _draw_gp_group_row(col, node, active_layer, depth)
            if bool(getattr(node, "is_expanded", True)):
                _draw_gp_layer_tree(col, getattr(node, "children", []), active_layer, depth + 1)
        else:
            _draw_gp_layer_row(col, node, active_layer, depth)


def _active_image_layer(context):
    coll = getattr(context.scene, "bname_image_layers", None)
    if coll is None:
        return None, -1, None
    idx = int(getattr(context.scene, "bname_active_image_layer_index", -1))
    if 0 <= idx < len(coll):
        return coll, idx, coll[idx]
    return coll, idx, None


def _draw_image_layer_row(col, entry, index: int, *, active: bool) -> None:
    row = col.row(align=True)
    select_icon = "RADIOBUT_ON" if active else "RADIOBUT_OFF"
    op = row.operator(
        "bname.image_layer_select",
        text="",
        icon=select_icon,
        emboss=False,
    )
    op.index = index
    row.label(text="", icon="IMAGE_DATA")
    row.prop(entry, "title", text="")
    row.prop(
        entry,
        "visible",
        text="",
        emboss=False,
        icon="HIDE_OFF" if entry.visible else "HIDE_ON",
    )
    row.prop(
        entry,
        "locked",
        text="",
        emboss=False,
        icon="LOCKED" if entry.locked else "UNLOCKED",
    )


def _draw_gp_selected_settings(box, obj, active_layer) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {active_layer.name}")
    if hasattr(active_layer, "opacity"):
        settings.prop(active_layer, "opacity", text="不透明度", slider=True)

    mat = None
    try:
        mat = gp_utils.ensure_layer_material(
            obj,
            active_layer,
            activate=True,
            assign_existing=True,
        )
    except Exception:  # noqa: BLE001
        mat = None
    gp_style = getattr(mat, "grease_pencil", None) if mat is not None else None
    if gp_style is not None:
        settings.prop(gp_style, "color", text="ストローク色")
        if hasattr(gp_style, "fill_color"):
            settings.prop(gp_style, "fill_color", text="塗り色")
        flag_row = settings.row(align=True)
        flag_row.prop(gp_style, "show_stroke", text="線を描く")
        if hasattr(gp_style, "show_fill"):
            flag_row.prop(gp_style, "show_fill", text="塗りを描く")
    else:
        settings.label(text="(レイヤー色を取得できません)", icon="ERROR")


def _draw_image_selected_settings(box, entry) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {entry.title} (画像)")
    settings.prop(entry, "opacity", text="不透明度", slider=True)
    settings.prop(entry, "filepath")

    row = settings.row(align=True)
    row.prop(entry, "x_mm")
    row.prop(entry, "y_mm")
    row = settings.row(align=True)
    row.prop(entry, "width_mm")
    row.prop(entry, "height_mm")
    row = settings.row(align=True)
    row.prop(entry, "rotation_deg")
    row.prop(entry, "flip_x", toggle=True)
    row.prop(entry, "flip_y", toggle=True)

    settings.prop(entry, "blend_mode")
    settings.prop(entry, "tint_color")
    settings.prop(entry, "brightness")
    settings.prop(entry, "contrast")
    settings.prop(entry, "binarize_enabled")
    sub = settings.row()
    sub.enabled = entry.binarize_enabled
    sub.prop(entry, "binarize_threshold")


def _draw_image_only_layer_list(layout, context) -> None:
    box = layout.box()
    box.label(text="レイヤー", icon="RENDERLAYERS")
    image_layers, active_image_idx, active_image = _active_image_layer(context)
    if active_image is not None:
        _draw_image_selected_settings(box, active_image)
        box.separator()

    if image_layers is None or len(image_layers) == 0:
        box.label(text="(レイヤーがありません)")
    else:
        col = box.column(align=True)
        header = col.row(align=True)
        header.label(text="画像レイヤー", icon="IMAGE_DATA")
        for i, entry in enumerate(image_layers):
            _draw_image_layer_row(
                col,
                entry,
                i,
                active=i == active_image_idx,
            )

    row = box.row(align=True)
    row.operator("bname.image_layer_add", text="画像", icon="IMAGE_DATA")
    if active_image is not None:
        row.operator("bname.image_layer_remove", text="削除", icon="REMOVE")


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
            _draw_image_only_layer_list(layout, context)
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

        # --- レイヤー一覧 ---
        box = layout.box()
        box.label(text="レイヤー", icon="RENDERLAYERS")
        layers = getattr(gp_data, "layers", None)
        if layers is None:
            box.label(text="(GP v3 layers にアクセスできません)", icon="ERROR")
            return

        active_layer = getattr(layers, "active", None)
        if active_layer is None and len(layers) > 0:
            try:
                layers.active = layers[0]
                active_layer = getattr(layers, "active", None)
            except Exception:  # noqa: BLE001
                active_layer = layers[0]
        image_layers, active_image_idx, active_image = _active_image_layer(context)
        active_kind = getattr(context.scene, "bname_active_layer_kind", "gp")
        image_selected = active_kind == "image" and active_image is not None
        if image_selected:
            _draw_image_selected_settings(box, active_image)
            box.separator()
        elif active_layer is not None:
            _draw_gp_selected_settings(box, obj, active_layer)
            box.separator()

        has_image_layers = image_layers is not None and len(image_layers) > 0
        if len(layers) == 0 and not has_image_layers:
            box.label(text="(レイヤーがありません)")
        else:
            col = box.column(align=True)
            if len(layers) > 0:
                gp_selected_layer = None if image_selected else active_layer
                root_nodes = getattr(gp_data, "root_nodes", None)
                if root_nodes is None:
                    for layer in layers:
                        _draw_gp_layer_row(col, layer, gp_selected_layer, 0)
                else:
                    _draw_gp_layer_tree(col, root_nodes, gp_selected_layer)
            if has_image_layers:
                if len(layers) > 0:
                    col.separator()
                header = col.row(align=True)
                header.label(text="画像レイヤー", icon="IMAGE_DATA")
                for i, entry in enumerate(image_layers):
                    _draw_image_layer_row(
                        col,
                        entry,
                        i,
                        active=image_selected and i == active_image_idx,
                    )

        row = box.row(align=True)
        row.operator("bname.gpencil_layer_add", text="GP", icon="OUTLINER_OB_GREASEPENCIL")
        row.operator("bname.image_layer_add", text="画像", icon="IMAGE_DATA")
        row.operator("bname.gpencil_folder_add", text="フォルダ", icon="FILE_FOLDER")
        if image_selected:
            row.operator("bname.image_layer_remove", text="削除", icon="REMOVE")
        else:
            row.operator("bname.gpencil_layer_remove", text="削除", icon="REMOVE")

        # アクティブレイヤーの追加プロパティ
        if active_layer is not None and not image_selected:
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
