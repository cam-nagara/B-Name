"""枠線系 modal ツールのアクティブ状態管理."""

from __future__ import annotations

import weakref


_ACTIVE_REFS: dict[str, weakref.ReferenceType | None] = {
    "edge_move": None,
    "knife_cut": None,
}


def get_active(tool_name: str):
    ref = _ACTIVE_REFS.get(tool_name)
    if ref is None:
        return None
    op = ref()
    if op is None:
        _ACTIVE_REFS[tool_name] = None
    return op


def set_active(tool_name: str, op) -> None:
    _ACTIVE_REFS[tool_name] = weakref.ref(op)


def clear_active(tool_name: str, op=None) -> None:
    current = get_active(tool_name)
    if op is not None and current is not op:
        return
    _ACTIVE_REFS[tool_name] = None


def finish_active(tool_name: str, context, *, keep_selection: bool = False) -> bool:
    op = get_active(tool_name)
    if op is None:
        return False
    try:
        op.finish_from_external(context, keep_selection=keep_selection)
    finally:
        clear_active(tool_name, op)
    return True
