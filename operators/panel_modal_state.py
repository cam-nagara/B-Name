"""B-Name modal ツールのアクティブ状態管理."""

from __future__ import annotations

import weakref


_ACTIVE_REFS: dict[str, weakref.ReferenceType | None] = {
    "edge_move": None,
    "knife_cut": None,
    "layer_move": None,
}


def tag_tool_ui_redraw(context) -> None:
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def get_active(tool_name: str):
    ref = _ACTIVE_REFS.get(tool_name)
    if ref is None:
        return None
    op = ref()
    if op is None:
        _ACTIVE_REFS[tool_name] = None
    return op


def is_active(tool_name: str) -> bool:
    return get_active(tool_name) is not None


def set_active(tool_name: str, op, context=None) -> None:
    _ACTIVE_REFS[tool_name] = weakref.ref(op)
    tag_tool_ui_redraw(context)


def clear_active(tool_name: str, op=None, context=None) -> None:
    current = get_active(tool_name)
    if op is not None and current is not op:
        return
    _ACTIVE_REFS[tool_name] = None
    tag_tool_ui_redraw(context)


def finish_active(tool_name: str, context, *, keep_selection: bool = False) -> bool:
    op = get_active(tool_name)
    if op is None:
        return False
    try:
        op.finish_from_external(context, keep_selection=keep_selection)
    finally:
        clear_active(tool_name, op, context)
    return True


def set_modal_cursor(context, cursor: str) -> bool:
    window = getattr(context, "window", None) if context is not None else None
    if window is None:
        return False
    try:
        window.cursor_modal_set(cursor)
        return True
    except Exception:  # noqa: BLE001
        return False


def restore_modal_cursor(context) -> None:
    window = getattr(context, "window", None) if context is not None else None
    if window is None:
        return
    try:
        window.cursor_modal_restore()
    except Exception:  # noqa: BLE001
        pass
