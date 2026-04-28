"""選択中要素向け右クリックメニューの起動ヘルパ."""

from __future__ import annotations

import bpy

from ..utils import layer_stack as layer_stack_utils, object_selection


def _call_selection_menu(context) -> bool:
    if layer_stack_utils.active_stack_item(context) is None:
        return False
    try:
        bpy.ops.wm.call_menu(name="BNAME_MT_selection_context")
    except Exception:  # noqa: BLE001
        return False
    return True


def open_for_object_tool(op, context, event) -> bool:
    from . import view_event_region

    if not view_event_region.is_view3d_window_event(context, event):
        return False
    hit = op._hit_object(context, event)
    if hit is not None:
        op._activate_hit(context, hit, mode="single")
    return _call_selection_menu(context)


def open_for_balloon_tool(context, event) -> bool:
    from . import balloon_op

    work, page, lx, ly = balloon_op._resolve_page_from_event(context, event)
    if work is not None and page is not None and lx is not None and ly is not None:
        hit_index, hit_entry, _hit_part = balloon_op._hit_balloon_entry(page, lx, ly)
        if hit_entry is not None and hit_index >= 0:
            balloon_op._select_balloon_index(context, work, page, hit_index, mode="single")
    return _call_selection_menu(context)


def open_for_text_tool(context, event) -> bool:
    from . import text_op

    work, page, _lx, _ly, hit_index, hit_entry, _hit_part, _can_create = (
        text_op._resolve_text_hit_from_event(context, event)
    )
    if work is not None and page is not None and hit_entry is not None and hit_index >= 0:
        text_op._select_text_index(context, work, page, hit_index)
        object_selection.select_key(
            context,
            object_selection.text_key(page, hit_entry),
            mode="single",
        )
    return _call_selection_menu(context)


def open_for_effect_tool(context, event) -> bool:
    from . import effect_line_op

    x_mm, y_mm = effect_line_op._event_world_xy_mm(context, event)
    if x_mm is not None and y_mm is not None:
        obj, layer, bounds, _part = effect_line_op._hit_effect_layer(context, x_mm, y_mm)
        if obj is not None and layer is not None and bounds is not None:
            effect_line_op._select_effect_layer(context, obj, layer)
            object_selection.select_key(
                context,
                object_selection.effect_key(layer),
                mode="single",
            )
    return _call_selection_menu(context)


def open_for_coma_edge_tool(op, context, event) -> bool:
    from . import coma_edge_move_op

    mx, my = op._to_window(event)
    hit = coma_edge_move_op._pick_edge_or_vertex(op._work, op._region, op._rv3d, mx, my)
    if hit is not None:
        op._selection = hit
        op._update_wm_selection(context)
        page = op._work.pages[int(hit["page"])]
        panel = page.comas[int(hit["coma"])]
        object_selection.select_key(
            context,
            object_selection.coma_key(page, panel),
            mode="single",
        )
    elif op._selection is not None:
        op._update_wm_selection(context)
    return _call_selection_menu(context)
