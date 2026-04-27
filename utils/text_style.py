"""Helpers for per-range text styling."""

from __future__ import annotations

import os
from pathlib import Path


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


def normalize_font_spans(entry) -> None:
    _write_segments(entry, _normalized_segments(entry))


def font_spans_snapshot(entry) -> tuple[tuple[int, int, str], ...]:
    return tuple(_normalized_segments(entry))


def restore_font_spans(entry, snapshot) -> None:
    segments = []
    body_len = _body_len(entry)
    for start, end, font in snapshot or ():
        start = max(0, min(body_len, int(start)))
        end = max(start, min(body_len, int(end)))
        if start < end and font:
            segments.append((start, end, str(font)))
    _write_segments(entry, segments)


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


def adjust_font_spans_for_replace(entry, start: int, end: int, new_length: int) -> None:
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


def font_for_index(entry, index: int) -> str:
    index = int(index)
    for start, end, font in _normalized_segments(entry):
        if start <= index < end:
            return font
    return str(getattr(entry, "font", "") or "")
