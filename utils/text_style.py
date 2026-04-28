"""Helpers for per-range text styling."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

StyleTuple = tuple[str, float, tuple[float, float, float, float], bool, bool]
StyleSegment = tuple[int, int, StyleTuple]

_FONT_DROPDOWN_ITEMS: list[tuple[str, str, str]] | None = None
_FONT_DROPDOWN_PATHS: dict[str, str] = {}
_DEFAULT_FONT_CHOICE = "__DEFAULT__"


def _abspath_maybe(path: str) -> str:
    path = str(path or "").strip()
    if not path:
        return ""
    try:
        import bpy

        return bpy.path.abspath(path)
    except Exception:  # noqa: BLE001
        return path


def font_candidates() -> list[str]:
    if os.name == "nt":
        return [
            r"C:\Windows\Fonts\YuGothM.ttc",
            r"C:\Windows\Fonts\YuGothR.ttc",
            r"C:\Windows\Fonts\meiryo.ttc",
            r"C:\Windows\Fonts\msgothic.ttc",
        ]
    return [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    ]


def _font_search_dirs() -> list[Path]:
    if os.name == "nt":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        return [Path(windir) / "Fonts"]
    return [
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
    ]


def available_font_paths() -> list[str]:
    """Return stable font paths for UI dropdowns."""
    paths: dict[str, str] = {}
    for candidate in font_candidates():
        path = _abspath_maybe(candidate)
        if path and Path(path).is_file():
            paths[str(Path(path))] = str(Path(path))
    for directory in _font_search_dirs():
        if not directory.exists():
            continue
        try:
            files = list(directory.rglob("*")) if os.name != "nt" else list(directory.glob("*"))
        except OSError:
            continue
        for path in files:
            if path.suffix.lower() not in {".ttf", ".ttc", ".otf"}:
                continue
            paths[str(path)] = str(path)
            if len(paths) >= 300:
                break
        if len(paths) >= 300:
            break
    return sorted(paths.values(), key=lambda item: (Path(item).stem.lower(), item.lower()))


def _font_choice_key(path: str) -> str:
    digest = hashlib.sha1(str(path).encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"FONT_{digest}"


def font_dropdown_items(_self=None, _context=None) -> list[tuple[str, str, str]]:
    """EnumProperty items for selecting installed fonts from a dropdown."""
    global _FONT_DROPDOWN_ITEMS
    if _FONT_DROPDOWN_ITEMS is not None:
        return _FONT_DROPDOWN_ITEMS
    items = [(_DEFAULT_FONT_CHOICE, "基本フォント", "テキストレイヤーの基本フォントを使う")]
    _FONT_DROPDOWN_PATHS.clear()
    for path in available_font_paths():
        key = _font_choice_key(path)
        _FONT_DROPDOWN_PATHS[key] = path
        label = Path(path).stem
        items.append((key, label, path))
    _FONT_DROPDOWN_ITEMS = items
    return items


def font_path_from_dropdown_choice(choice: str) -> str:
    if choice == _DEFAULT_FONT_CHOICE:
        return ""
    if _FONT_DROPDOWN_ITEMS is None:
        font_dropdown_items()
    return _FONT_DROPDOWN_PATHS.get(str(choice or ""), "")


def dropdown_choice_for_font_path(path: str) -> str:
    path = _abspath_maybe(path)
    if not path:
        return _DEFAULT_FONT_CHOICE
    if _FONT_DROPDOWN_ITEMS is None:
        font_dropdown_items()
    normalized = str(Path(path))
    for key, item_path in _FONT_DROPDOWN_PATHS.items():
        try:
            if Path(item_path).resolve() == Path(normalized).resolve():
                return key
        except OSError:
            if item_path == normalized:
                return key
    return _DEFAULT_FONT_CHOICE


def resolve_font_path(preferred: str = "") -> str:
    preferred = _abspath_maybe(preferred)
    if preferred and Path(preferred).is_file():
        return preferred
    for candidate in font_candidates():
        if Path(candidate).is_file():
            return candidate
    return preferred


def _body_len(entry) -> int:
    return len(str(getattr(entry, "body", "") or ""))


def _default_style(entry) -> StyleTuple:
    color = getattr(entry, "color", (0.0, 0.0, 0.0, 1.0))
    return (
        str(getattr(entry, "font", "") or ""),
        float(getattr(entry, "font_size_q", 20.0)),
        (
            float(color[0]),
            float(color[1]),
            float(color[2]),
            float(color[3]),
        ),
        bool(getattr(entry, "font_bold", False)),
        bool(getattr(entry, "font_italic", False)),
    )


def _normalized_segments(entry) -> list[tuple[int, int, str]]:
    spans = getattr(entry, "font_spans", None)
    if spans is None:
        return []
    body_len = _body_len(entry)
    segments: list[tuple[int, int, str]] = []
    for span in spans:
        font = str(getattr(span, "font", "") or "").strip()
        if not font:
            continue
        start = max(0, min(body_len, int(getattr(span, "start", 0))))
        end = max(start, min(body_len, start + int(getattr(span, "length", 0))))
        if start >= end:
            continue
        segments = _exclude_range(segments, start, end)
        segments.append((start, end, font))
    return _merge_segments(sorted(segments, key=lambda item: (item[0], item[1], item[2])))


def _exclude_range(
    segments: list[tuple[int, int, str]],
    start: int,
    end: int,
) -> list[tuple[int, int, str]]:
    kept: list[tuple[int, int, str]] = []
    for seg_start, seg_end, font in segments:
        if seg_end <= start or seg_start >= end:
            kept.append((seg_start, seg_end, font))
            continue
        if seg_start < start:
            kept.append((seg_start, start, font))
        if end < seg_end:
            kept.append((end, seg_end, font))
    return kept


def _merge_segments(segments: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    merged: list[tuple[int, int, str]] = []
    for start, end, font in segments:
        if start >= end:
            continue
        if merged and merged[-1][1] == start and merged[-1][2] == font:
            merged[-1] = (merged[-1][0], end, font)
        else:
            merged.append((start, end, font))
    return merged


def _write_segments(entry, segments: list[tuple[int, int, str]]) -> None:
    spans = getattr(entry, "font_spans", None)
    if spans is None:
        return
    spans.clear()
    for start, end, font in _merge_segments(sorted(segments, key=lambda item: item[0])):
        if start >= end or not font:
            continue
        span = spans.add()
        span.start = int(start)
        span.length = int(end - start)
        span.font = font


def _style_from_span(span) -> StyleTuple:
    color = getattr(span, "color", (0.0, 0.0, 0.0, 1.0))
    return (
        str(getattr(span, "font", "") or ""),
        float(getattr(span, "font_size_q", 20.0)),
        (
            float(color[0]),
            float(color[1]),
            float(color[2]),
            float(color[3]),
        ),
        bool(getattr(span, "font_bold", False)),
        bool(getattr(span, "font_italic", False)),
    )


def _normalized_style_segments(entry) -> list[StyleSegment]:
    spans = getattr(entry, "style_spans", None)
    if spans is None:
        return []
    body_len = _body_len(entry)
    segments: list[StyleSegment] = []
    for span in spans:
        start = max(0, min(body_len, int(getattr(span, "start", 0))))
        end = max(start, min(body_len, start + int(getattr(span, "length", 0))))
        if start >= end:
            continue
        segments = _exclude_style_range(segments, start, end)
        segments.append((start, end, _style_from_span(span)))
    return _merge_style_segments(sorted(segments, key=lambda item: (item[0], item[1], item[2])))


def _exclude_style_range(segments: list[StyleSegment], start: int, end: int) -> list[StyleSegment]:
    kept: list[StyleSegment] = []
    for seg_start, seg_end, style in segments:
        if seg_end <= start or seg_start >= end:
            kept.append((seg_start, seg_end, style))
            continue
        if seg_start < start:
            kept.append((seg_start, start, style))
        if end < seg_end:
            kept.append((end, seg_end, style))
    return kept


def _merge_style_segments(segments: list[StyleSegment]) -> list[StyleSegment]:
    merged: list[StyleSegment] = []
    for start, end, style in segments:
        if start >= end:
            continue
        if merged and merged[-1][1] == start and merged[-1][2] == style:
            merged[-1] = (merged[-1][0], end, style)
        else:
            merged.append((start, end, style))
    return merged


def _write_style_segments(entry, segments: list[StyleSegment]) -> None:
    spans = getattr(entry, "style_spans", None)
    if spans is None:
        return
    spans.clear()
    for start, end, style in _merge_style_segments(sorted(segments, key=lambda item: item[0])):
        if start >= end:
            continue
        font, font_size_q, color, bold, italic = style
        span = spans.add()
        span.start = int(start)
        span.length = int(end - start)
        span.font = font
        span.font_size_q = float(font_size_q)
        span.color = color
        span.font_bold = bool(bold)
        span.font_italic = bool(italic)


def normalize_font_spans(entry) -> None:
    _write_segments(entry, _normalized_segments(entry))


def normalize_style_spans(entry) -> None:
    _write_style_segments(entry, _normalized_style_segments(entry))


def font_spans_snapshot(entry) -> tuple[tuple[int, int, str], ...]:
    return tuple(_normalized_segments(entry))


def style_spans_snapshot(entry) -> tuple[StyleSegment, ...]:
    return tuple(_normalized_style_segments(entry))


def all_spans_snapshot(entry):
    return (font_spans_snapshot(entry), style_spans_snapshot(entry))


def restore_font_spans(entry, snapshot) -> None:
    segments = []
    body_len = _body_len(entry)
    for start, end, font in snapshot or ():
        start = max(0, min(body_len, int(start)))
        end = max(start, min(body_len, int(end)))
        if start < end and font:
            segments.append((start, end, str(font)))
    _write_segments(entry, segments)


def restore_style_spans(entry, snapshot) -> None:
    body_len = _body_len(entry)
    segments: list[StyleSegment] = []
    for item in snapshot or ():
        start, end, style = item
        start = max(0, min(body_len, int(start)))
        end = max(start, min(body_len, int(end)))
        if start < end:
            segments.append((start, end, style))
    _write_style_segments(entry, segments)


def restore_all_spans(entry, snapshot) -> None:
    font_snapshot, style_snapshot = snapshot or ((), ())
    restore_font_spans(entry, font_snapshot)
    restore_style_spans(entry, style_snapshot)


def apply_font_span(entry, start: int, end: int, font: str) -> bool:
    body_len = _body_len(entry)
    start = max(0, min(body_len, int(start)))
    end = max(start, min(body_len, int(end)))
    if start >= end:
        return False
    segments = _exclude_range(_normalized_segments(entry), start, end)
    font = str(font or "").strip()
    if font:
        segments.append((start, end, font))
    _write_segments(entry, segments)
    return True


def apply_style_span(
    entry,
    start: int,
    end: int,
    *,
    font: str,
    font_size_q: float,
    color,
    bold: bool,
    italic: bool,
) -> bool:
    body_len = _body_len(entry)
    start = max(0, min(body_len, int(start)))
    end = max(start, min(body_len, int(end)))
    if start >= end:
        return False
    color_tuple = (
        float(color[0]),
        float(color[1]),
        float(color[2]),
        float(color[3]),
    )
    style: StyleTuple = (
        str(font or "").strip(),
        max(1.0, float(font_size_q)),
        color_tuple,
        bool(bold),
        bool(italic),
    )
    segments = _exclude_style_range(_normalized_style_segments(entry), start, end)
    segments.append((start, end, style))
    _write_style_segments(entry, segments)
    return True


def _adjust_font_spans_for_replace(entry, start: int, end: int, new_length: int) -> None:
    body_len = _body_len(entry)
    start = max(0, min(body_len, int(start)))
    end = max(start, min(body_len, int(end)))
    new_length = max(0, int(new_length))
    delta = new_length - (end - start)
    old_segments = _normalized_segments(entry)
    inherited_font = ""
    if start < end and new_length > 0:
        for seg_start, seg_end, font in old_segments:
            if seg_start <= start < seg_end:
                inherited_font = font
                break
    adjusted: list[tuple[int, int, str]] = []
    for seg_start, seg_end, font in old_segments:
        if start == end:
            if seg_end <= start:
                adjusted.append((seg_start, seg_end, font))
            elif seg_start >= start:
                adjusted.append((seg_start + delta, seg_end + delta, font))
            else:
                adjusted.append((seg_start, seg_end + delta, font))
            continue
        if seg_end <= start:
            adjusted.append((seg_start, seg_end, font))
        elif seg_start >= end:
            adjusted.append((seg_start + delta, seg_end + delta, font))
        else:
            if seg_start < start:
                adjusted.append((seg_start, start, font))
            if end < seg_end:
                adjusted.append((start + new_length, seg_end + delta, font))
    if inherited_font:
        adjusted = _exclude_range(adjusted, start, start + new_length)
        adjusted.append((start, start + new_length, inherited_font))
    _write_segments(entry, adjusted)


def _adjust_style_spans_for_replace(entry, start: int, end: int, new_length: int) -> None:
    body_len = _body_len(entry)
    start = max(0, min(body_len, int(start)))
    end = max(start, min(body_len, int(end)))
    new_length = max(0, int(new_length))
    delta = new_length - (end - start)
    old_segments = _normalized_style_segments(entry)
    inherited_style: StyleTuple | None = None
    if start < end and new_length > 0:
        for seg_start, seg_end, style in old_segments:
            if seg_start <= start < seg_end:
                inherited_style = style
                break
    adjusted: list[StyleSegment] = []
    for seg_start, seg_end, style in old_segments:
        if start == end:
            if seg_end <= start:
                adjusted.append((seg_start, seg_end, style))
            elif seg_start >= start:
                adjusted.append((seg_start + delta, seg_end + delta, style))
            else:
                adjusted.append((seg_start, seg_end + delta, style))
            continue
        if seg_end <= start:
            adjusted.append((seg_start, seg_end, style))
        elif seg_start >= end:
            adjusted.append((seg_start + delta, seg_end + delta, style))
        else:
            if seg_start < start:
                adjusted.append((seg_start, start, style))
            if end < seg_end:
                adjusted.append((start + new_length, seg_end + delta, style))
    if inherited_style is not None:
        adjusted = _exclude_style_range(adjusted, start, start + new_length)
        adjusted.append((start, start + new_length, inherited_style))
    _write_style_segments(entry, adjusted)


def adjust_spans_for_replace(entry, start: int, end: int, new_length: int) -> None:
    _adjust_font_spans_for_replace(entry, start, end, new_length)
    _adjust_style_spans_for_replace(entry, start, end, new_length)


def adjust_font_spans_for_replace(entry, start: int, end: int, new_length: int) -> None:
    adjust_spans_for_replace(entry, start, end, new_length)


def font_for_index(entry, index: int) -> str:
    index = int(index)
    for start, end, style in _normalized_style_segments(entry):
        if start <= index < end:
            font = style[0]
            return font if font else str(getattr(entry, "font", "") or "")
    for start, end, font in _normalized_segments(entry):
        if start <= index < end:
            return font
    return str(getattr(entry, "font", "") or "")


def style_for_index(entry, index: int) -> StyleTuple:
    index = int(index)
    for start, end, style in _normalized_style_segments(entry):
        if start <= index < end:
            font, font_size_q, color, bold, italic = style
            return (
                font if font else str(getattr(entry, "font", "") or ""),
                font_size_q,
                color,
                bold,
                italic,
            )
    font, font_size_q, color, bold, italic = _default_style(entry)
    legacy_font = font_for_index(entry, index)
    return (legacy_font or font, font_size_q, color, bold, italic)


def font_size_q_for_index(entry, index: int) -> float:
    return float(style_for_index(entry, index)[1])


def color_for_index(entry, index: int) -> tuple[float, float, float, float]:
    return style_for_index(entry, index)[2]


def bold_for_index(entry, index: int) -> bool:
    return bool(style_for_index(entry, index)[3])


def italic_for_index(entry, index: int) -> bool:
    return bool(style_for_index(entry, index)[4])
