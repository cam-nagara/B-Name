"""Runtime helpers for B-Name inline text editing."""

from __future__ import annotations

import math

from ..utils.geom import Rect, q_to_mm

_TEXT_PADDING_MM = 1.0
_TEXT_CARET_MIN_THICKNESS_MM = 0.18
_IME_CONTROL_TYPES = {
    "ACCENT_GRAVE",
    "GRLESS",
    "HENKAN",
    "MUHENKAN",
    "KANA",
    "KATAKANA",
    "HIRAGANA",
    "EISU",
    "KANJI",
    "ZENKAKU_HANKAKU",
    "HANKAKU_ZENKAKU",
    "IME_ON",
    "IME_OFF",
    "IME_CONVERT",
    "IME_NONCONVERT",
    "LANGUAGE",
    "OSKEY",
}


def text_body(entry) -> str:
    return str(getattr(entry, "body", "") or "")


def clamp_cursor(entry, index: int) -> int:
    return max(0, min(len(text_body(entry)), int(index)))


def text_em_mm(entry) -> float:
    try:
        q = float(getattr(entry, "font_size_q", 20.0))
    except Exception:  # noqa: BLE001
        q = 20.0
    return max(0.25, q_to_mm(q))


def text_line_height(entry) -> float:
    try:
        return max(0.1, float(getattr(entry, "line_height", 1.4)))
    except Exception:  # noqa: BLE001
        return 1.4


def text_letter_spacing(entry) -> float:
    try:
        return float(getattr(entry, "letter_spacing", 0.0))
    except Exception:  # noqa: BLE001
        return 0.0


def text_inner_rect(rect: Rect) -> Rect:
    padded = rect.inset(_TEXT_PADDING_MM)
    return padded if padded.width > 0.0 and padded.height > 0.0 else rect


def text_rect(entry) -> Rect:
    return Rect(
        float(getattr(entry, "x_mm", 0.0)),
        float(getattr(entry, "y_mm", 0.0)),
        float(getattr(entry, "width_mm", 0.0)),
        float(getattr(entry, "height_mm", 0.0)),
    )


def _layout_cursor_state(entry, rect: Rect, cursor_index: int) -> tuple[Rect, float, float, int, int]:
    region = text_inner_rect(rect)
    em = text_em_mm(entry)
    line_pitch = em * text_line_height(entry)
    char_pitch = em * max(0.1, 1.0 + text_letter_spacing(entry))
    cursor_index = clamp_cursor(entry, cursor_index)
    col = 0
    row = 0
    writing_mode = getattr(entry, "writing_mode", "vertical")
    for ch in text_body(entry)[:cursor_index]:
        if ch == "\n":
            if writing_mode == "horizontal":
                row += 1
                col = 0
            else:
                col += 1
                row = 0
            continue
        if writing_mode == "horizontal":
            col += 1
            if region.x + col * char_pitch > region.x2:
                row += 1
                col = 0
        else:
            row += 1
            if region.y2 - row * char_pitch < region.y:
                col += 1
                row = 0
    return region, em, char_pitch, row, col


def caret_rect(entry, rect: Rect, cursor_index: int) -> Rect:
    region, em, char_pitch, row, col = _layout_cursor_state(entry, rect, cursor_index)
    line_pitch = em * text_line_height(entry)
    thickness = max(_TEXT_CARET_MIN_THICKNESS_MM, em * 0.08)
    if getattr(entry, "writing_mode", "vertical") == "horizontal":
        x = region.x + col * char_pitch
        y = region.y2 - em - row * line_pitch
        x = max(region.x, min(region.x2, x)) - thickness * 0.5
        y = max(region.y, min(region.y2 - em, y))
        return Rect(x, y, thickness, min(em, region.height))

    x_center = region.x2 - em * 0.5 - col * line_pitch
    y = region.y2 - row * char_pitch
    half_width = min(em * 0.45, max(0.6, region.width * 0.5))
    x = max(region.x, min(region.x2, x_center)) - half_width
    x = max(region.x, min(region.x2 - half_width * 2.0, x))
    y = max(region.y, min(region.y2, y)) - thickness * 0.5
    return Rect(x, y, half_width * 2.0, thickness)


def cursor_index_from_point(entry, x_mm: float, y_mm: float) -> int:
    rect = text_rect(entry)
    best_index = 0
    best_distance = math.inf
    for index in range(len(text_body(entry)) + 1):
        caret = caret_rect(entry, rect, index)
        cx, cy = caret.center
        distance = math.hypot(float(x_mm) - cx, float(y_mm) - cy)
        if distance < best_distance:
            best_distance = distance
            best_index = index
    return best_index


def selection_bounds(cursor_index: int, selection_anchor: int) -> tuple[int, int] | None:
    if selection_anchor < 0 or selection_anchor == cursor_index:
        return None
    start = min(int(cursor_index), int(selection_anchor))
    end = max(int(cursor_index), int(selection_anchor))
    return start, end


def selected_text(entry, cursor_index: int, selection_anchor: int) -> str:
    bounds = selection_bounds(cursor_index, selection_anchor)
    if bounds is None:
        return ""
    start, end = bounds
    return text_body(entry)[start:end]


def replace_selection(entry, cursor_index: int, selection_anchor: int, text: str) -> int:
    body = text_body(entry)
    bounds = selection_bounds(cursor_index, selection_anchor)
    if bounds is None:
        index = clamp_cursor(entry, cursor_index)
        entry.body = body[:index] + text + body[index:]
        return index + len(text)
    start, end = bounds
    entry.body = body[:start] + text + body[end:]
    return start + len(text)


def delete_backward(entry, cursor_index: int, selection_anchor: int) -> int:
    body = text_body(entry)
    bounds = selection_bounds(cursor_index, selection_anchor)
    if bounds is not None:
        start, end = bounds
        entry.body = body[:start] + body[end:]
        return start
    index = clamp_cursor(entry, cursor_index)
    if index <= 0:
        return 0
    entry.body = body[: index - 1] + body[index:]
    return index - 1


def delete_forward(entry, cursor_index: int, selection_anchor: int) -> int:
    body = text_body(entry)
    bounds = selection_bounds(cursor_index, selection_anchor)
    if bounds is not None:
        start, end = bounds
        entry.body = body[:start] + body[end:]
        return start
    index = clamp_cursor(entry, cursor_index)
    if index >= len(body):
        return index
    entry.body = body[:index] + body[index + 1:]
    return index


def move_cursor(entry, cursor_index: int, direction: str) -> int:
    body = text_body(entry)
    index = clamp_cursor(entry, cursor_index)
    vertical = getattr(entry, "writing_mode", "vertical") != "horizontal"
    if direction == "LEFT":
        if vertical:
            return _move_cursor_visual(entry, index, 1)
        return max(0, index - 1)
    if direction == "RIGHT":
        if vertical:
            return _move_cursor_visual(entry, index, -1)
        return min(len(body), index + 1)
    if direction == "UP" and vertical:
        return max(0, index - 1)
    if direction == "DOWN" and vertical:
        return min(len(body), index + 1)
    if direction == "HOME":
        return 0
    if direction == "END":
        return len(body)
    if direction in {"UP", "DOWN"}:
        return _move_cursor_visual(entry, index, -1 if direction == "UP" else 1)
    return index


def _move_cursor_visual(entry, index: int, delta_line: int) -> int:
    body = text_body(entry)
    lines = body.split("\n")
    line_start = 0
    for current_line, line in enumerate(lines):
        line_end = line_start + len(line)
        if line_start <= index <= line_end:
            col = index - line_start
            target_line = max(0, min(len(lines) - 1, current_line + delta_line))
            target_start = sum(len(lines[i]) + 1 for i in range(target_line))
            return min(target_start + col, target_start + len(lines[target_line]))
        line_start = line_end + 1
    return max(0, min(len(body), index))


def event_is_ime_control(event) -> bool:
    event_type = str(getattr(event, "type", "") or "")
    if event_type in _IME_CONTROL_TYPES:
        return True
    if bool(getattr(event, "alt", False)) and event_type in {"ACCENT_GRAVE", "SPACE"}:
        return True
    return False
