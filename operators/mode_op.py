"""紙面編集モード / コマ編集モードの切替 Operator."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..core.mode import MODE_PAGE, MODE_PANEL, get_mode, set_mode
from ..core.work import get_active_page, get_work
from ..utils import log

_logger = log.get_logger(__name__)


class BNAME_OT_enter_panel_mode(Operator):
    """選択中のコマの 3D シーンに入る (コマ編集モード)."""

    bl_idname = "bname.enter_panel_mode"
    bl_label = "コマ編集モードへ"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return (
            page is not None
            and 0 <= page.active_panel_index < len(page.panels)
            and get_mode(context) == MODE_PAGE
        )

    def execute(self, context):
        page = get_active_page(context)
        if page is None or not (0 <= page.active_panel_index < len(page.panels)):
            return {"CANCELLED"}
        entry = page.panels[page.active_panel_index]
        set_mode(MODE_PANEL, context)
        context.scene.bname_current_panel_stem = entry.panel_stem
        # Phase 4 で 3D シーン切替を実装。現段階は状態変更のみ。
        self.report({"INFO"}, f"コマ編集モード: {entry.panel_stem}")
        return {"FINISHED"}


class BNAME_OT_exit_panel_mode(Operator):
    """コマ編集モードを抜けて紙面編集モードへ戻る."""

    bl_idname = "bname.exit_panel_mode"
    bl_label = "紙面編集モードへ戻る"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return get_mode(context) == MODE_PANEL

    def execute(self, context):
        set_mode(MODE_PAGE, context)
        context.scene.bname_current_panel_stem = ""
        # Phase 4 でコマ編集終了時のスクショサムネイル生成を実装する。
        self.report({"INFO"}, "紙面編集モード")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_enter_panel_mode,
    BNAME_OT_exit_panel_mode,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
