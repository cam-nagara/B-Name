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
from bpy.types import Panel, UIList

from ..core.work import get_active_page, get_work
from ..utils import gpencil as gp_utils
from ..utils import layer_stack as layer_stack_utils

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


def _kind_icon(kind: str) -> str:
    return {
        "page": "FILE_IMAGE",
        "panel": "IMAGE_DATA",
        "gp": "OUTLINER_OB_GREASEPENCIL",
        "gp_folder": "FILE_FOLDER",
        "image": "IMAGE_DATA",
        "balloon": "MOD_FLUID",
        "text": "FONT_DATA",
        "effect": "STROKE",
    }.get(kind, "RENDERLAYERS")


def _draw_stack_gp_row(row, item, resolved) -> None:
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        row.label(text=item.label, icon=_kind_icon(item.kind))
        return
    if item.kind == "gp_folder":
        expanded = bool(getattr(target, "is_expanded", True))
        row.prop(
            target,
            "is_expanded",
            text="",
            emboss=False,
            icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
        )
        row.prop(target, "name", text="")
    else:
        row.label(text="", icon=_kind_icon(item.kind))
        row.prop(target, "name", text="")
    if hasattr(target, "hide"):
        row.prop(
            target,
            "hide",
            text="",
            emboss=False,
            icon="HIDE_ON" if target.hide else "HIDE_OFF",
        )
    if hasattr(target, "lock"):
        row.prop(
            target,
            "lock",
            text="",
            emboss=False,
            icon="LOCKED" if target.lock else "UNLOCKED",
        )


def _draw_stack_page_row(row, item, resolved) -> None:
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        row.label(text=item.label, icon="FILE_IMAGE")
        return
    expanded = bool(getattr(target, "stack_expanded", True))
    row.prop(
        target,
        "stack_expanded",
        text="",
        emboss=False,
        icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
    )
    row.label(text=getattr(target, "id", ""), icon="IMGDISPLAY" if target.spread else "FILE_IMAGE")
    row.prop(target, "title", text="")


def _draw_stack_panel_row(row, item, resolved, index: int) -> None:
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        row.label(text=item.label, icon="IMAGE_DATA")
        return
    row.label(text=getattr(target, "panel_stem", ""), icon="IMAGE_DATA")
    row.prop(target, "title", text="")
    op = row.operator(
        "bname.layer_stack_enter_panel",
        text="",
        icon="PLAY",
        emboss=False,
    )
    op.stack_index = index


def _draw_stack_data_row(row, item, resolved) -> None:
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        row.label(text=item.label, icon=_kind_icon(item.kind))
        return
    if item.kind == "image":
        row.label(text="", icon="IMAGE_DATA")
        row.prop(target, "title", text="")
        row.prop(
            target,
            "visible",
            text="",
            emboss=False,
            icon="HIDE_OFF" if target.visible else "HIDE_ON",
        )
        row.prop(
            target,
            "locked",
            text="",
            emboss=False,
            icon="LOCKED" if target.locked else "UNLOCKED",
        )
    elif item.kind == "balloon":
        row.label(text=target.id, icon="MOD_FLUID")
        row.prop(target, "shape", text="")
    elif item.kind == "text":
        row.label(text="", icon="FONT_DATA")
        row.prop(target, "body", text="")
    elif item.kind == "effect":
        _draw_stack_gp_row(row, item, resolved)
    else:
        row.label(text=item.label, icon=_kind_icon(item.kind))


class BNAME_UL_layer_stack(UIList):
    """統合レイヤーリスト。UIList の実CollectionをD&D並び替え対象にする."""

    bl_idname = "BNAME_UL_layer_stack"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type not in {"DEFAULT", "COMPACT"}:
            layout.label(text=item.label, icon=_kind_icon(item.kind))
            return
        row = layout.row(align=True)
        _indent(row, int(getattr(item, "depth", 0)))
        active = int(getattr(context.scene, "bname_active_layer_stack_index", -1)) == index
        op = row.operator(
            "bname.layer_stack_select",
            text="",
            icon="RADIOBUT_ON" if active else "RADIOBUT_OFF",
            emboss=False,
        )
        op.index = index
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        if item.kind == "page":
            _draw_stack_page_row(row, item, resolved)
        elif item.kind == "panel":
            _draw_stack_panel_row(row, item, resolved, index)
        elif item.kind in {"gp", "gp_folder", "effect"}:
            _draw_stack_gp_row(row, item, resolved)
        else:
            _draw_stack_data_row(row, item, resolved)


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
    if hasattr(active_layer, "blend_mode"):
        settings.prop(active_layer, "blend_mode", text="ブレンド")
    if hasattr(active_layer, "tint_color"):
        settings.prop(active_layer, "tint_color", text="色合い")
    if hasattr(active_layer, "tint_factor"):
        settings.prop(active_layer, "tint_factor", text="色合い量")


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


def _draw_balloon_selected_settings(box, context, entry) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {entry.id} (フキダシ)")
    settings.prop(entry, "shape")
    row = settings.row(align=True)
    row.prop(entry, "x_mm")
    row.prop(entry, "y_mm")
    row = settings.row(align=True)
    row.prop(entry, "width_mm")
    row.prop(entry, "height_mm")
    settings.prop(entry, "rotation_deg")
    row = settings.row(align=True)
    row.prop(entry, "flip_h", toggle=True)
    row.prop(entry, "flip_v", toggle=True)
    settings.prop(entry, "opacity", slider=True)
    settings.prop(entry, "rounded_corner_enabled")
    sub = settings.row()
    sub.enabled = entry.rounded_corner_enabled
    sub.prop(entry, "rounded_corner_radius_mm")

    line_box = box.box()
    line_box.label(text="線・塗り")
    line_box.prop(entry, "line_style")
    line_box.prop(entry, "line_width_mm")
    line_box.prop(entry, "line_color")
    line_box.prop(entry, "fill_color")

    sp = entry.shape_params
    if entry.shape == "cloud":
        shape_box = box.box()
        shape_box.label(text="雲パラメータ")
        shape_box.prop(sp, "cloud_wave_count")
        shape_box.prop(sp, "cloud_wave_amplitude_mm")
    elif entry.shape in ("spike_curve", "spike_straight"):
        shape_box = box.box()
        shape_box.label(text="トゲパラメータ")
        shape_box.prop(sp, "spike_count")
        shape_box.prop(sp, "spike_depth_mm")
        shape_box.prop(sp, "spike_jitter")

    move_box = box.box()
    move_box.label(text="親子連動移動", icon="CON_TRACKTO")
    row = move_box.row(align=True)
    op = row.operator("bname.balloon_move", text="← 5mm")
    op.delta_x_mm = -5.0
    op = row.operator("bname.balloon_move", text="→ 5mm")
    op.delta_x_mm = 5.0
    op = row.operator("bname.balloon_move", text="↑ 5mm")
    op.delta_y_mm = 5.0
    op = row.operator("bname.balloon_move", text="↓ 5mm")
    op.delta_y_mm = -5.0

    tail_box = box.box()
    row = tail_box.row(align=True)
    row.label(text=f"尻尾 ({len(entry.tails)})")
    row.operator("bname.balloon_tail_add", text="", icon="ADD")
    for i, tail in enumerate(entry.tails):
        sub = tail_box.box()
        sub.label(text=f"尻尾 {i + 1}")
        sub.prop(tail, "type")
        sub.prop(tail, "direction_deg")
        sub.prop(tail, "length_mm")
        row = sub.row(align=True)
        row.prop(tail, "root_width_mm")
        row.prop(tail, "tip_width_mm")
        if tail.type == "curve":
            sub.prop(tail, "curve_bend")


def _draw_text_selected_settings(box, context, entry) -> None:
    page = get_active_page(context)
    settings = box.column(align=True)
    settings.label(text=f"選択中: {entry.id} (テキスト)")
    settings.prop(entry, "body")
    settings.prop(entry, "speaker_type")
    row = settings.row(align=True)
    row.prop(entry, "x_mm")
    row.prop(entry, "y_mm")
    row = settings.row(align=True)
    row.prop(entry, "width_mm")
    row.prop(entry, "height_mm")

    type_box = box.box()
    type_box.label(text="組版", icon="FONT_DATA")
    type_box.prop(entry, "writing_mode")
    type_box.prop(entry, "font_size_pt")
    row = type_box.row(align=True)
    row.prop(entry, "font_bold", toggle=True)
    row.prop(entry, "font_italic", toggle=True)
    type_box.prop(entry, "color")
    row = type_box.row(align=True)
    row.prop(entry, "line_height")
    row.prop(entry, "letter_spacing")

    stroke_box = box.box()
    stroke_box.prop(entry, "stroke_enabled")
    sub = stroke_box.column()
    sub.enabled = entry.stroke_enabled
    sub.prop(entry, "stroke_width_mm")
    sub.prop(entry, "stroke_color")

    parent_box = box.box()
    parent_box.label(text="親フキダシ", icon="LINKED")
    parent_box.prop(entry, "parent_balloon_id", text="ID")
    if page is not None and len(page.balloons) > 0:
        row = parent_box.row(align=True)
        row.label(text="紐付け:")
        for balloon in page.balloons:
            op = row.operator("bname.text_attach_to_balloon", text=balloon.id)
            op.balloon_id = balloon.id
        op = parent_box.operator(
            "bname.text_attach_to_balloon",
            text="独立テキストにする",
            icon="UNLINKED",
        )
        op.balloon_id = ""


def _draw_effect_selected_settings(box, context, obj, active_layer) -> None:
    settings = box.column(align=True)
    name = getattr(active_layer, "name", "効果線")
    settings.label(text=f"選択中: {name} (効果線)")
    if active_layer is not None and hasattr(active_layer, "opacity"):
        settings.prop(active_layer, "opacity", text="不透明度", slider=True)
    if active_layer is not None and hasattr(active_layer, "hide"):
        settings.prop(active_layer, "hide", text="非表示")
    params = getattr(context.scene, "bname_effect_line_params", None)
    if params is None:
        settings.label(text="効果線パラメータが未初期化です", icon="ERROR")
        return

    param_box = box.box()
    param_box.label(text="効果線設定", icon="STROKE")
    param_box.prop(params, "effect_type")
    param_box.prop(params, "base_shape")
    if params.base_shape == "polygon":
        param_box.prop(params, "base_vertex_count")
    param_box.prop(params, "start_from_center")
    param_box.prop(params, "rotation_deg")

    line_box = box.box()
    line_box.label(text="線")
    line_box.prop(params, "brush_size_mm")
    row = line_box.row(align=True)
    row.prop(params, "brush_jitter_enabled", text="乱れ")
    sub = row.row()
    sub.enabled = params.brush_jitter_enabled
    sub.prop(params, "brush_jitter_amount", text="")
    line_box.prop(params, "spacing_mode")
    if params.spacing_mode == "angle":
        line_box.prop(params, "spacing_angle_deg")
    else:
        line_box.prop(params, "spacing_distance_mm")
    line_box.prop(params, "length_mm")
    line_box.prop(params, "extend_past_panel")

    base_box = box.box()
    base_box.label(text="基準位置 / ギザ")
    base_box.prop(params, "base_position")
    base_box.prop(params, "base_position_offset")
    base_box.prop(params, "base_jagged_enabled")
    sub = base_box.column()
    sub.enabled = params.base_jagged_enabled
    sub.prop(params, "base_jagged_count")
    sub.prop(params, "base_jagged_height_mm")

    inout_box = box.box()
    inout_box.label(text="入り抜き")
    inout_box.prop(params, "inout_apply")
    row = inout_box.row(align=True)
    row.prop(params, "in_percent")
    row.prop(params, "out_percent")

    color_box = box.box()
    color_box.label(text="色")
    color_box.prop(params, "line_color")
    if params.effect_type == "beta_flash":
        color_box.prop(params, "fill_color")
        color_box.prop(params, "fill_opacity")
        color_box.prop(params, "fill_base_shape")
    if params.effect_type == "speed":
        speed_box = box.box()
        speed_box.label(text="流線")
        speed_box.prop(params, "speed_angle_deg")
        speed_box.prop(params, "speed_line_count")
    box.operator("bname.effect_line_generate", text="効果線を追加", icon="STROKE")


def _draw_page_selected_settings(box, context, entry) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {entry.id} (ページ)", icon="FILE_IMAGE")
    settings.prop(entry, "title", text="表示名")
    row = settings.row(align=True)
    row.prop(entry, "offset_x_mm", text="表示X")
    row.prop(entry, "offset_y_mm", text="表示Y")


def _draw_panel_selected_settings(box, context, entry) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {entry.panel_stem} (コマ)", icon="IMAGE_DATA")
    settings.prop(entry, "title", text="表示名")
    row = settings.row(align=True)
    row.prop(entry, "rect_x_mm", text="X")
    row.prop(entry, "rect_y_mm", text="Y")
    row = settings.row(align=True)
    row.prop(entry, "rect_width_mm", text="幅")
    row.prop(entry, "rect_height_mm", text="高さ")
    box.operator("bname.enter_panel_mode", text="コマ編集へ", icon="PLAY")


def _draw_selected_stack_settings(box, context) -> None:
    item = layer_stack_utils.active_stack_item(context)
    if item is None:
        return
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    if resolved is None or resolved.get("target") is None:
        return
    kind = item.kind
    target = resolved["target"]
    obj = resolved.get("object")
    if kind == "page":
        _draw_page_selected_settings(box, context, target)
        box.separator()
    elif kind == "panel":
        _draw_panel_selected_settings(box, context, target)
        box.separator()
    elif kind == "gp":
        _draw_gp_selected_settings(box, obj, target)
        box.separator()
    elif kind == "image":
        _draw_image_selected_settings(box, target)
        box.separator()
    elif kind == "balloon":
        _draw_balloon_selected_settings(box, context, target)
        box.separator()
    elif kind == "text":
        _draw_text_selected_settings(box, context, target)
        box.separator()
    elif kind == "effect":
        _draw_effect_selected_settings(box, context, obj, target)
        box.separator()
    elif kind == "gp_folder":
        box.label(text=f"選択中: {target.name} (フォルダ)", icon="FILE_FOLDER")
        if hasattr(target, "hide"):
            box.prop(target, "hide", text="非表示")
        if hasattr(target, "lock"):
            box.prop(target, "lock", text="ロック")
        box.separator()


def _draw_layer_add_buttons(box) -> None:
    page_row = box.row(align=True)
    page_row.operator("bname.page_add", text="", icon="ADD")
    page_row.operator("bname.page_remove", text="", icon="REMOVE")
    page_row.operator("bname.page_duplicate", text="", icon="DUPLICATE")
    page_row.separator()
    page_row.operator("bname.pages_merge_spread", text="", icon="ARROW_LEFTRIGHT")
    page_row.operator("bname.pages_split_spread", text="", icon="UNLINKED")

    panel_row = box.row(align=True)
    panel_row.operator("bname.panel_add", text="", icon="ADD")
    panel_row.operator("bname.panel_remove", text="", icon="REMOVE")
    panel_row.operator("bname.panel_duplicate", text="", icon="DUPLICATE")
    panel_row.operator("bname.panel_move_to_page", text="", icon="FORWARD")

    row = box.row(align=True)
    row.operator("bname.gpencil_layer_add", text="GP", icon="OUTLINER_OB_GREASEPENCIL")
    row.operator("bname.image_layer_add", text="画像", icon="IMAGE_DATA")
    row.operator("bname.balloon_add", text="フキダシ", icon="MOD_FLUID")
    row.operator("bname.text_add", text="テキスト", icon="FONT_DATA")
    row.operator("bname.effect_line_generate", text="効果線", icon="STROKE")
    row.operator("bname.gpencil_folder_add", text="フォルダ", icon="FILE_FOLDER")


def _draw_layer_stack_box(layout, context) -> None:
    layer_stack_utils.sync_layer_stack(context)
    # UIList のD&Dで stack の順序だけが変わった場合も、次の描画で実データ順へ反映する。
    layer_stack_utils.apply_stack_order(context)
    scene = context.scene
    box = layout.box()
    box.label(text="レイヤー", icon="RENDERLAYERS")
    _draw_selected_stack_settings(box, context)

    stack = getattr(scene, "bname_layer_stack", None)
    if stack is None or len(stack) == 0:
        box.label(text="(レイヤーがありません)")
    else:
        row = box.row()
        row.template_list(
            BNAME_UL_layer_stack.bl_idname,
            "",
            scene,
            "bname_layer_stack",
            scene,
            "bname_active_layer_stack_index",
            rows=8,
        )
        col = row.column(align=True)
        op = col.operator("bname.layer_stack_move", text="", icon="TRIA_UP_BAR")
        op.direction = "FRONT"
        op = col.operator("bname.layer_stack_move", text="", icon="TRIA_UP")
        op.direction = "UP"
        op = col.operator("bname.layer_stack_move", text="", icon="TRIA_DOWN")
        op.direction = "DOWN"
        op = col.operator("bname.layer_stack_move", text="", icon="TRIA_DOWN_BAR")
        op.direction = "BACK"
        col.separator()
        col.operator("bname.layer_stack_delete", text="", icon="REMOVE")

    _draw_layer_add_buttons(box)


class BNAME_PT_layer_stack(Panel):
    """統合レイヤーリスト。画像/GP/フキダシ/テキスト/効果線をここに集約する."""

    bl_idname = "BNAME_PT_layer_stack"
    bl_label = "レイヤー"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 12

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        if work is None or not work.loaded:
            layout.label(text="作品を開いてください", icon="INFO")
            return
        _draw_layer_stack_box(layout, context)


class BNAME_PT_gpencil(Panel):
    """master GP のモード / 描画色管理 UI."""

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
    BNAME_UL_layer_stack,
    BNAME_PT_layer_stack,
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
