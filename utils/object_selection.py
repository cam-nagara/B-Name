"""Viewport object selection shared by B-Name object-style tools."""

from __future__ import annotations

import json


SELECTION_PROP = "bname_object_selection_keys"


def make_key(kind: str, page_id: str = "", item_id: str = "") -> str:
    return "|".join((str(kind or ""), str(page_id or ""), str(item_id or "")))


def parse_key(key: str) -> tuple[str, str, str]:
    parts = str(key or "").split("|", 2)
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def coma_key(page, panel) -> str:
    return make_key("coma", getattr(page, "id", ""), _coma_id(panel))


def balloon_key(page, entry) -> str:
    return make_key("balloon", getattr(page, "id", ""), getattr(entry, "id", ""))


def text_key(page, entry) -> str:
    return make_key("text", getattr(page, "id", ""), getattr(entry, "id", ""))


def effect_key(layer) -> str:
    return make_key("effect", "", str(getattr(layer, "name", "") or ""))


def _coma_id(panel) -> str:
    return str(getattr(panel, "coma_id", "") or getattr(panel, "id", "") or "")


def _wm(context):
    return getattr(context, "window_manager", None) if context is not None else None


def get_keys(context) -> list[str]:
    wm = _wm(context)
    if wm is None or not hasattr(wm, SELECTION_PROP):
        return []
    raw = str(getattr(wm, SELECTION_PROP, "") or "")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(parsed, list):
        return []
    keys = []
    for item in parsed:
        key = str(item or "")
        if key and key not in keys:
            keys.append(key)
    return keys


def set_keys(context, keys) -> None:
    wm = _wm(context)
    if wm is None or not hasattr(wm, SELECTION_PROP):
        return
    unique = []
    for key in keys or []:
        text = str(key or "")
        if text and text not in unique:
            unique.append(text)
    setattr(wm, SELECTION_PROP, json.dumps(unique, ensure_ascii=False, separators=(",", ":")))
    _sync_balloon_flags(context, unique)
    tag_view3d_redraw(context)


def clear(context) -> None:
    set_keys(context, [])


def is_selected(context, key: str) -> bool:
    return str(key or "") in set(get_keys(context))


def select_key(context, key: str, *, mode: str = "single") -> list[str]:
    current = get_keys(context)
    key = str(key or "")
    if not key:
        return current
    if mode == "toggle":
        if key in current:
            current = [item for item in current if item != key]
        else:
            current.append(key)
    elif mode == "add":
        if key not in current:
            current.append(key)
    else:
        current = [key]
    set_keys(context, current)
    return current


def tag_view3d_redraw(context) -> None:
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is None:
        return
    for area in getattr(screen, "areas", []):
        if getattr(area, "type", "") == "VIEW_3D":
            try:
                area.tag_redraw()
            except Exception:  # noqa: BLE001
                pass


def selected_coma_refs(context) -> list[tuple[int, object, int, object]]:
    refs: list[tuple[int, object, int, object]] = []
    keys = set(get_keys(context))
    if not keys:
        return refs
    try:
        from ..core.work import get_work

        work = get_work(context)
    except Exception:  # noqa: BLE001
        work = None
    if work is None:
        return refs
    for page_index, page in enumerate(getattr(work, "pages", []) or []):
        page_id = str(getattr(page, "id", "") or "")
        for coma_index, panel in enumerate(getattr(page, "comas", []) or []):
            if make_key("coma", page_id, _coma_id(panel)) in keys:
                refs.append((page_index, page, coma_index, panel))
    return refs


def selected_coma_count(context) -> int:
    return len(selected_coma_refs(context))


def is_coma_selected(context, page, panel) -> bool:
    return coma_key(page, panel) in set(get_keys(context))


def is_balloon_selected(context, page, entry) -> bool:
    return balloon_key(page, entry) in set(get_keys(context))


def is_text_selected(context, page, entry) -> bool:
    return text_key(page, entry) in set(get_keys(context))


def selected_effect_names(context) -> set[str]:
    names = set()
    for key in get_keys(context):
        kind, _page_id, item_id = parse_key(key)
        if kind == "effect" and item_id:
            names.add(item_id)
    return names


def _sync_balloon_flags(context, keys: list[str]) -> None:
    try:
        from ..core.work import get_work

        work = get_work(context)
    except Exception:  # noqa: BLE001
        work = None
    if work is None:
        return
    key_set = set(keys)
    for page in getattr(work, "pages", []) or []:
        page_id = str(getattr(page, "id", "") or "")
        for balloon in getattr(page, "balloons", []) or []:
            if hasattr(balloon, "selected"):
                balloon.selected = make_key("balloon", page_id, getattr(balloon, "id", "")) in key_set
