"""operators — bpy.types.Operator 群."""

from __future__ import annotations

from . import (
    asset_op,
    balloon_op,
    effect_line_op,
    image_layer_op,
    io_op,
    mode_op,
    page_op,
    panel_edit_op,
    panel_op,
    preset_op,
    snap_op,
    spread_op,
    thumbnail_op,
    work_op,
)

_MODULES = (
    work_op,
    page_op,
    spread_op,
    panel_op,
    panel_edit_op,
    snap_op,
    balloon_op,
    effect_line_op,
    image_layer_op,
    asset_op,
    thumbnail_op,
    mode_op,
    preset_op,
    io_op,
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
