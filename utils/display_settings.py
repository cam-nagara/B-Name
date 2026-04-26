"""Scene display/color-management defaults for B-Name files."""

from __future__ import annotations

import bpy


def apply_standard_color_management(scene=None) -> None:
    """Set the active scene color-management view transform to Standard."""
    target = scene or bpy.context.scene
    if target is None:
        return
    view_settings = getattr(target, "view_settings", None)
    if view_settings is None:
        return
    try:
        view_settings.view_transform = "Standard"
    except Exception:  # noqa: BLE001
        pass
    try:
        view_settings.look = "None"
    except Exception:  # noqa: BLE001
        pass
    try:
        view_settings.exposure = 0.0
    except Exception:  # noqa: BLE001
        pass
    try:
        view_settings.gamma = 1.0
    except Exception:  # noqa: BLE001
        pass
