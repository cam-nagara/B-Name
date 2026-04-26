"""operators — bpy.types.Operator 群."""

from __future__ import annotations

from . import (
    asset_op,
    balloon_op,
    effect_line_op,
    gpencil_op,
    image_layer_op,
    io_op,
    layer_stack_op,
    mode_op,
    page_op,
    panel_edge_move_op,
    panel_edge_style_op,
    panel_edit_op,
    panel_knife_cut_op,
    panel_op,
    panel_camera_op,
    panel_picker,  # noqa: F401 — ヘルパのみ (register 対象外)
    panel_vertex_edit_op,
    preset_op,
    shortcut_op,
    snap_op,
    spread_op,
    text_op,
    thumbnail_op,
    view_op,
    work_op,
)

_MODULES = (
    work_op,
    page_op,
    spread_op,
    panel_op,
    panel_edit_op,
    panel_camera_op,
    panel_vertex_edit_op,
    panel_knife_cut_op,
    panel_edge_move_op,
    panel_edge_style_op,
    snap_op,
    balloon_op,
    text_op,
    effect_line_op,
    image_layer_op,
    layer_stack_op,
    asset_op,
    thumbnail_op,
    mode_op,
    preset_op,
    io_op,
    gpencil_op,
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
