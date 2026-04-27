"""Viewport overlay drawing for B-Name text entries."""

from __future__ import annotations

from collections.abc import Callable

from ..utils.geom import Rect

EntryVisiblePredicate = Callable[[object], bool]
_TEXT_HANDLE_SIZE_MM = 2.0
_TEXT_CARET_MIN_THICKNESS_MM = 0.18
_TEXT_CARET_COLOR = (1.0, 0.55, 0.0, 1.0)


def _text_handle_rects(rect: Rect) -> list[Rect]:
    half = _TEXT_HANDLE_SIZE_MM * 0.5
    points = (
        (rect.x, rect.y),
        (rect.x + rect.width * 0.5, rect.y),
        (rect.x2, rect.y),
        (rect.x, rect.y + rect.height * 0.5),
        (rect.x2, rect.y + rect.height * 0.5),
        (rect.x, rect.y2),
        (rect.x + rect.width * 0.5, rect.y2),
        (rect.x2, rect.y2),
    )
    return [
        Rect(x - half, y - half, _TEXT_HANDLE_SIZE_MM, _TEXT_HANDLE_SIZE_MM)
        for x, y in points
    ]


def _text_content(entry) -> str:
    return str(getattr(entry, "body", "") or "")


def _text_em_mm(entry) -> float:
    from ..utils.geom import q_to_mm

    try:
        q = float(getattr(entry, "font_size_q", 20.0))
    except Exception:  # noqa: BLE001
        q = 20.0
    return max(0.25, q_to_mm(q))


def _text_line_height(entry) -> float:
    try:
        return max(0.1, float(getattr(entry, "line_height", 1.4)))
    except Exception:  # noqa: BLE001
        return 1.4


def _text_letter_spacing(entry) -> float:
    try:
        return float(getattr(entry, "letter_spacing", 0.0))
    except Exception:  # noqa: BLE001
        return 0.0


def _text_inner_rect(rect: Rect) -> Rect:
    padded = rect.inset(1.0)
    return padded if padded.width > 0.0 and padded.height > 0.0 else rect


def _vertical_caret_rect(entry, rect: Rect) -> Rect:
    """縦書き用の横向きキャレット矩形を返す。"""
    region = _text_inner_rect(rect)
    em = _text_em_mm(entry)
    line_pitch = em * _text_line_height(entry)
    char_pitch = em * max(0.1, 1.0 + _text_letter_spacing(entry))
    col = 0
    row = 0
    for ch in _text_content(entry):
        if ch == "\n":
            col += 1
            row = 0
            continue
        row += 1
        if region.y2 - row * char_pitch < region.y:
            col += 1
            row = 0
    x_center = region.x2 - em * 0.5 - col * line_pitch
    y = region.y2 - row * char_pitch
    half_width = min(em * 0.45, max(0.6, region.width * 0.5))
    thickness = max(_TEXT_CARET_MIN_THICKNESS_MM, em * 0.08)
    x = max(region.x, min(region.x2, x_center)) - half_width
    x = max(region.x, min(region.x2 - half_width * 2.0, x))
    y = max(region.y, min(region.y2, y)) - thickness * 0.5
    return Rect(x, y, half_width * 2.0, thickness)


def _horizontal_caret_rect(entry, rect: Rect) -> Rect:
    region = _text_inner_rect(rect)
    em = _text_em_mm(entry)
    line_pitch = em * _text_line_height(entry)
    char_pitch = em * max(0.1, 1.0 + _text_letter_spacing(entry))
    row = 0
    col = 0
    for ch in _text_content(entry):
        if ch == "\n":
            row += 1
            col = 0
            continue
        col += 1
        if region.x + col * char_pitch > region.x2:
            row += 1
            col = 0
    x = region.x + col * char_pitch
    y = region.y2 - em - row * line_pitch
    thickness = max(_TEXT_CARET_MIN_THICKNESS_MM, em * 0.08)
    x = max(region.x, min(region.x2, x)) - thickness * 0.5
    y = max(region.y, min(region.y2 - em, y))
    return Rect(x, y, thickness, min(em, region.height))


def text_caret_rect(entry, rect: Rect) -> Rect:
    if getattr(entry, "writing_mode", "vertical") == "horizontal":
        return _horizontal_caret_rect(entry, rect)
    return _vertical_caret_rect(entry, rect)


def _is_editing_entry(context, page, entry) -> bool:
    if context is None:
        return False
    try:
        from ..operators import panel_modal_state

        op = panel_modal_state.get_active("text_tool")
    except Exception:  # noqa: BLE001
        return False
    if op is None or not bool(getattr(op, "_editing", False)):
        return False
    return (
        str(getattr(op, "_page_id", "") or "") == str(getattr(page, "id", "") or "")
        and str(getattr(op, "_text_id", "") or "") == str(getattr(entry, "id", "") or "")
    )


def draw_text_guides(
    page,
    *,
    context=None,
    ox_mm: float = 0.0,
    oy_mm: float = 0.0,
    active: bool = False,
    entry_visible: EntryVisiblePredicate,
    draw_rect_fill: Callable[..., None],
    draw_rect_outline: Callable[..., None],
) -> None:
    texts = getattr(page, "texts", None)
    if texts is None:
        return
    active_idx = getattr(page, "active_text_index", -1) if active else -1
    for i, entry in enumerate(texts):
        if not entry_visible(entry):
            continue
        rect = Rect(entry.x_mm + ox_mm, entry.y_mm + oy_mm, entry.width_mm, entry.height_mm)
        draw_rect_fill(rect, (1.0, 1.0, 1.0, 0.55))
        color = (0.2, 0.7, 1.0, 1.0) if entry.parent_balloon_id else (0.95, 0.85, 0.1, 1.0)
        draw_rect_outline(rect, color, width_mm=0.30)
        if i == active_idx:
            draw_rect_outline(rect.inset(-1.0), (1.0, 0.6, 0.0, 1.0), width_mm=0.50)
            for handle in _text_handle_rects(rect):
                draw_rect_fill(handle, (1.0, 1.0, 1.0, 0.95))
                draw_rect_outline(handle, (1.0, 0.6, 0.0, 1.0), width_mm=0.25)
        if _is_editing_entry(context, page, entry):
            caret = text_caret_rect(entry, rect)
            draw_rect_fill(caret, _TEXT_CARET_COLOR)


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
        draw_text_in_rect(context, rect, entry)
