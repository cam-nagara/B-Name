"""operators — bpy.types.Operator 群."""

from __future__ import annotations

from . import mode_op, page_op, panel_op, preset_op, spread_op, work_op

_MODULES = (
    work_op,
    page_op,
    spread_op,
    panel_op,
    mode_op,
    preset_op,
)


def register() -> None:
    for module in _MODULES:
        module.register()


def unregister() -> None:
    for module in reversed(_MODULES):
        try:
            module.unregister()
        except Exception:
            pass
