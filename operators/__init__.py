"""operators — bpy.types.Operator 群."""

from __future__ import annotations

from . import (
    alt_reparent_op,
    asset_op,
    balloon_op,
    brush_size_op,
    effect_line_op,
    effect_line_link_op,
    fisheye_op,
    gp_layer_op,
    gpencil_op,
    image_layer_op,
    image_plane_op,
    io_op,
    layer_stack_op,
    layer_move_op,
    mode_op,
    object_tool_op,
    outliner_view_op,
    page_op,
    coma_edge_move_op,
    coma_edge_style_op,
    coma_edit_op,
    coma_knife_cut_op,
    coma_op,
    coma_camera_op,
    coma_picker,  # noqa: F401 — ヘルパのみ (register 対象外)
    coma_vertex_edit_op,
    preset_op,
    raster_layer_op,
    shortcut_op,
    snap_op,
    spread_op,
    text_selection_style_op,
    text_op,
    thumbnail_op,
    view_op,
    work_op,
)

_MODULES = (
    work_op,
    page_op,
    spread_op,
    coma_op,
    coma_edit_op,
    coma_camera_op,
    coma_vertex_edit_op,
    coma_knife_cut_op,
    coma_edge_move_op,
    coma_edge_style_op,
    fisheye_op,
    snap_op,
    balloon_op,
    text_selection_style_op,
    text_op,
    effect_line_op,
    effect_line_link_op,
    brush_size_op,
    image_layer_op,
    image_plane_op,
    raster_layer_op,
    layer_stack_op,
    layer_move_op,
    object_tool_op,
    outliner_view_op,
    alt_reparent_op,
    asset_op,
    thumbnail_op,
    mode_op,
    preset_op,
    io_op,
    gpencil_op,
    gp_layer_op,
    view_op,
    shortcut_op,
)


def register() -> None:
    for module in _MODULES:
        module.register()


def unregister() -> None:
    for module in reversed(_MODULES):
        try:
            module.unregister()
        except Exception:
            pass
