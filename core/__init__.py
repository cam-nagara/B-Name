"""core — 作品/ページ/コマのデータモデルと PropertyGroup.

register 順序は PointerProperty の前方参照を避けるため、参照先→参照元の
順で行う。

階層:
  DisplayItem → WorkInfo
  (Nombre / PaperSettings / SafeAreaOverlay / PageEntry は相互独立)
  PanelGap + WorkData (他をすべて参照する集約)
"""

from __future__ import annotations

from ..utils import log
from . import (
    balloon,
    effect_line,
    mode,
    panel,
    panel_border,
    paper,
    page,
    safe_area_overlay,
    text_entry,
    work,
    work_info,
)

_logger = log.get_logger(__name__)

_MODULES = (
    paper,
    work_info,
    safe_area_overlay,
    panel_border,  # panel が PointerProperty で参照するため先に
    panel,         # page が CollectionProperty で参照するため page より先
    page,
    text_entry,
    balloon,
    effect_line,
    work,          # 他すべてを参照する集約なので最後
    mode,          # Scene.bname_mode を別途 attach (work とは独立)
)


def register() -> None:
    for module in _MODULES:
        module.register()


def unregister() -> None:
    for module in reversed(_MODULES):
        try:
            module.unregister()
        except Exception:
            # 他モジュールの unregister が続行できるよう例外は握り潰すが、
            # 原因追跡のため必ずログに残す
            _logger.exception("core: unregister failed for %s", module.__name__)
