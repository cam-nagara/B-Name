"""統合レイヤーリストの選択詳細 UI."""

from __future__ import annotations

import re

from ..core.work import get_active_page, get_work
from ..utils import balloon_shapes
from ..utils import gpencil as gp_utils


def _zero_based_layer_name(prefix: str, value: str, width: int) -> str:
    text = str(value or "")
    match = re.search(r"(\d+)(?!.*\d)", text)
    if match is None:
        return f"{prefix}{text}" if text else f"{prefix}{0:0{width}d}"
    number = max(0, int(match.group(1)) - 1)
    return f"{prefix}{number:0{width}d}"


def page_layer_name(target, work=None) -> str:
    if work is not None and target is not None:
        try:
            start = int(getattr(work.work_info, "page_number_start", 1))
        except Exception:  # noqa: BLE001
            start = 1
        target_id = str(getattr(target, "id", "") or "")
        for i, page in enumerate(getattr(work, "pages", [])):
            if str(getattr(page, "id", "") or "") == target_id:
                return f"p{max(0, start + i):03d}"
    return _zero_based_layer_name("p", str(getattr(target, "id", "") or ""), 3)


def coma_layer_name(target) -> str:
    stem = str(getattr(target, "coma_id", "") or getattr(target, "id", "") or "")
    return _zero_based_layer_name("c", stem, 2)


def _draw_gp_selected_settings(box, obj, active_layer) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {active_layer.name}")
    settings.prop(active_layer, "name", text="名前")
    if hasattr(active_layer, "hide"):
        settings.prop(active_layer, "hide", text="非表示")
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
    settings.prop(entry, "visible", text="表示")
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


def _draw_raster_selected_settings(box, entry) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {entry.title or entry.id} (ラスター)", icon="BRUSH_DATA")
    settings.prop(entry, "title", text="名前")
    settings.prop(entry, "visible", text="表示")
    settings.prop(entry, "locked", text="ロック")
    settings.prop(entry, "opacity", text="不透明度", slider=True)
    settings.label(text=f"DPI: {int(getattr(entry, 'dpi', 0))}")
    settings.operator("bname.raster_layer_resample", text="リサンプル...", icon="IMAGE_DATA")

    bit_box = box.box()
    bit_box.label(text=f"階調: {getattr(entry, 'bit_depth', 'gray8')}")
    row = bit_box.row(align=True)
    op = row.operator("bname.raster_layer_set_bit_depth", text="グレー 8bit")
    op.bit_depth = "gray8"
    op = row.operator("bname.raster_layer_set_bit_depth", text="1bit")
    op.bit_depth = "gray1"

    settings.prop(entry, "line_color", text="線色")
    settings.label(text=f"所属: {entry.scope or 'page'}")
    settings.label(text=f"親: {entry.parent_kind or 'none'} / {entry.parent_key or '-'}")
    row = settings.row(align=True)
    op = row.operator("bname.raster_layer_paint_enter", text="Texture Paint へ入る", icon="TPAINT_HLT")
    op.raster_id = entry.id
    op = row.operator("bname.raster_layer_save_png", text="", icon="FILE_TICK")
    op.raster_id = entry.id
    op.force = True


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
    settings.prop(entry, "blend_mode")
    if getattr(entry, "merge_group_id", ""):
        settings.label(text=f"結合: {entry.merge_group_id}", icon="FILE_FOLDER")
    page = get_active_page(context)
    if page is not None and sum(1 for b in page.balloons if getattr(b, "selected", False)) >= 2:
        settings.operator("bname.balloon_merge_selected", text="フキダシを結合", icon="FILE_FOLDER")
    if balloon_shapes.normalize_shape(entry.shape) == "rect":
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
    if balloon_shapes.is_dynamic_meldex_shape(entry.shape):
        shape_box = box.box()
        shape_box.label(text="Meldex形状パラメータ")
        shape_box.prop(sp, "cloud_bump_width_mm")
        shape_box.prop(sp, "cloud_bump_height_mm")
        shape_box.prop(sp, "cloud_offset_percent")
        row = shape_box.row(align=True)
        row.prop(sp, "cloud_sub_width_ratio")
        row.prop(sp, "cloud_sub_height_ratio")

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
    type_box.prop(entry, "font", text="基本フォント")
    type_box.prop(entry, "font_size_q")
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


def _draw_effect_type_settings(box, params) -> None:
    param_box = box.box()
    param_box.label(text="種類 / 基準", icon="STROKE")
    param_box.prop(params, "effect_type")
    if params.effect_type == "speed":
        return
    param_box.prop(params, "base_shape")
    if params.base_shape == "polygon":
        param_box.prop(params, "base_vertex_count")
    param_box.prop(params, "start_from_center")
    param_box.prop(params, "rotation_deg")


def _draw_effect_line_settings(box, params) -> None:
    line_box = box.box()
    line_box.label(text="線")
    line_box.prop(params, "brush_size_mm")
    row = line_box.row(align=True)
    row.prop(params, "brush_jitter_enabled", text="乱れ")
    sub = row.row()
    sub.enabled = params.brush_jitter_enabled
    sub.prop(params, "brush_jitter_amount", text="")


def _draw_effect_position_settings(box, params) -> None:
    position_box = box.box()
    position_box.label(text="描画位置")
    position_box.prop(params, "length_mm")
    row = position_box.row(align=True)
    row.prop(params, "length_jitter_enabled", text="乱れ")
    sub = row.row()
    sub.enabled = params.length_jitter_enabled
    sub.prop(params, "length_jitter_amount", text="")
    position_box.prop(params, "extend_past_coma")
    if params.effect_type == "speed":
        return
    position_box.prop(params, "base_position")
    row = position_box.row(align=True)
    row.prop(params, "base_position_offset_enabled", text="基準位置のずれ")
    sub = row.row()
    sub.enabled = params.base_position_offset_enabled
    sub.prop(params, "base_position_offset", text="")
    position_box.prop(params, "base_jagged_enabled")
    sub = position_box.column(align=True)
    sub.enabled = params.base_jagged_enabled
    row = sub.row(align=True)
    row.prop(params, "base_jagged_count")
    row.prop(params, "base_jagged_height_mm")


def _draw_effect_interval_settings(box, params) -> None:
    interval_box = box.box()
    interval_box.label(text="描画間隔")
    interval_box.prop(params, "spacing_mode")
    if params.spacing_mode == "angle":
        interval_box.prop(params, "spacing_angle_deg")
    else:
        interval_box.prop(params, "spacing_distance_mm")
    row = interval_box.row(align=True)
    row.prop(params, "spacing_jitter_enabled", text="乱れ")
    sub = row.row()
    sub.enabled = params.spacing_jitter_enabled
    sub.prop(params, "spacing_jitter_amount", text="")
    interval_box.prop(params, "bundle_enabled")
    sub = interval_box.column(align=True)
    sub.enabled = params.bundle_enabled
    row = sub.row(align=True)
    row.prop(params, "bundle_line_count")
    row.prop(params, "bundle_gap_mm")
    sub.prop(params, "bundle_jitter_amount")
    interval_box.prop(params, "max_line_count")


def _draw_effect_tail_settings(box, params) -> None:
    if params.effect_type == "speed":
        speed_box = box.box()
        speed_box.label(text="流線")
        speed_box.prop(params, "speed_angle_deg")
        speed_box.prop(params, "speed_line_count")

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

    _draw_effect_type_settings(box, params)
    _draw_effect_line_settings(box, params)
    _draw_effect_position_settings(box, params)
    _draw_effect_interval_settings(box, params)
    _draw_effect_tail_settings(box, params)
    box.operator("bname.effect_line_generate", text="効果線を追加", icon="STROKE")


def _draw_page_selected_settings(box, context, entry) -> None:
    settings = box.column(align=True)
    settings.label(
        text=f"選択中: {page_layer_name(entry, get_work(context))} (ページ)",
        icon="FILE_BLANK",
    )
    settings.prop(entry, "title", text="表示名")
    if hasattr(entry, "visible"):
        settings.prop(entry, "visible", text="表示")
    row = settings.row(align=True)
    row.prop(entry, "offset_x_mm", text="表示X")
    row.prop(entry, "offset_y_mm", text="表示Y")


def _draw_coma_selected_settings(box, context, entry) -> None:
    settings = box.column(align=True)
    settings.label(text=f"選択中: {coma_layer_name(entry)} (コマ)", icon="MOD_WIREFRAME")
    settings.prop(entry, "title", text="表示名")
    if hasattr(entry, "visible"):
        settings.prop(entry, "visible", text="表示")
    box.operator("bname.enter_coma_mode", text="コマ編集へ", icon="PLAY")

    from . import coma_detail_panel

    shape_box = box.box()
    shape_box.label(text="形状")
    coma_detail_panel.draw_coma_shape_settings(shape_box, context, entry)

    border_box = box.box()
    border_box.label(text="枠線")
    coma_detail_panel.draw_coma_border_settings(border_box, context, entry)

    white_box = box.box()
    white_box.label(text="白フチ")
    coma_detail_panel.draw_coma_white_margin_settings(white_box, entry)


def draw_stack_item_detail(layout, context, item, resolved) -> bool:
    if resolved is None or resolved.get("target") is None:
        return False
    box = layout.box()
    kind = item.kind
    target = resolved["target"]
    obj = resolved.get("object")
    if kind == "page":
        _draw_page_selected_settings(box, context, target)
    elif kind == "coma":
        _draw_coma_selected_settings(box, context, target)
    elif kind == "gp":
        _draw_gp_selected_settings(box, obj, target)
    elif kind == "image":
        _draw_image_selected_settings(box, target)
    elif kind == "raster":
        _draw_raster_selected_settings(box, target)
    elif kind == "balloon":
        _draw_balloon_selected_settings(box, context, target)
    elif kind == "text":
        _draw_text_selected_settings(box, context, target)
    elif kind == "effect":
        _draw_effect_selected_settings(box, context, obj, target)
    elif kind == "gp_folder":
        box.label(text=f"選択中: {target.name} (フォルダ)", icon="FILE_FOLDER")
        box.prop(target, "name", text="名前")
        if hasattr(target, "hide"):
            box.prop(target, "hide", text="非表示")
        if hasattr(target, "lock"):
            box.prop(target, "lock", text="ロック")
    return True
