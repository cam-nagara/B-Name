"""panels — N-Panel (View3D > UI region) の B-Name タブ."""

from __future__ import annotations

from . import page_panel, paper_panel, work_panel

_MODULES = (
    work_panel,
    paper_panel,
    page_panel,
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
