"""Viewport overlay visibility predicates."""

from __future__ import annotations

from ..utils import page_range
from ..utils.layer_hierarchy import entry_center, coma_containing_point


def page_visible(page) -> bool:
    return page_range.page_visible_in_work(page)


def coma_visible(panel) -> bool:
    return bool(getattr(panel, "visible", True))


def entry_in_visible_coma(page, entry) -> bool:
    try:
        panel = coma_containing_point(page, *entry_center(entry))
    except Exception:  # noqa: BLE001
        panel = None
    return panel is None or coma_visible(panel)
