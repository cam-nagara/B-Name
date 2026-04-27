"""Viewport overlay visibility predicates."""

from __future__ import annotations

from ..utils.layer_hierarchy import entry_center, panel_containing_point


def page_visible(page) -> bool:
    return bool(getattr(page, "visible", True))


def panel_visible(panel) -> bool:
    return bool(getattr(panel, "visible", True))


def entry_in_visible_panel(page, entry) -> bool:
    try:
        panel = panel_containing_point(page, *entry_center(entry))
    except Exception:  # noqa: BLE001
        panel = None
    return panel is None or panel_visible(panel)
