"""Viewport overlay drawing for B-Name text entries."""

from __future__ import annotations

from collections.abc import Callable

from ..utils.geom import Rect

EntryVisiblePredicate = Callable[[object], bool]


def draw_text_guides(
    page,
    *,
    ox_mm: float = 0.0,
    oy_mm: float = 0.0,
    entry_visible: EntryVisiblePredicate,
    draw_rect_fill: Callable[..., None],
    draw_rect_outline: Callable[..., None],
) -> None:
    texts = getattr(page, "texts", None)
    if texts is None:
        return
    active_idx = getattr(page, "active_text_index", -1)
    for i, entry in enumerate(texts):
        if not entry_visible(entry):
            continue
        rect = Rect(entry.x_mm + ox_mm, entry.y_mm + oy_mm, entry.width_mm, entry.height_mm)
        draw_rect_fill(rect, (1.0, 1.0, 1.0, 0.55))
        color = (0.2, 0.7, 1.0, 1.0) if entry.parent_balloon_id else (0.95, 0.85, 0.1, 1.0)
        draw_rect_outline(rect, color, width_mm=0.30)
        if i == active_idx:
            draw_rect_outline(rect.inset(-1.0), (1.0, 0.6, 0.0, 1.0), width_mm=0.50)


def draw_text_pixels(
    context,
    page,
    *,
    ox_mm: float,
    oy_mm: float,
    entry_visible: EntryVisiblePredicate,
    draw_text_in_rect: Callable[..., None],
) -> None:
    texts = getattr(page, "texts", None)
    if texts is None:
        return
    for entry in texts:
        if not entry_visible(entry):
            continue
        rect = Rect(entry.x_mm + ox_mm, entry.y_mm + oy_mm, entry.width_mm, entry.height_mm)
        body = (getattr(entry, "body", "") or "").strip() or "(空のテキスト)"
        draw_text_in_rect(context, rect, body, color=(0.0, 0.0, 0.0, 1.0))
