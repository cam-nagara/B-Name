"""選択コマを各種ガイドへスナップする Operator."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..utils import snap
from ..utils.geom import Rect


class BNAME_OT_snap_active_coma(Operator):
    """選択中コマを用紙/他コマへスナップ (4 辺一括)."""

    bl_idname = "bname.snap_active_coma"
    bl_label = "選択コマをスナップ"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        idx = page.active_coma_index
        return 0 <= idx < len(page.comas) and page.comas[idx].shape_type == "rect"

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        entry = page.comas[page.active_coma_index]
        rect = Rect(
            entry.rect_x_mm,
            entry.rect_y_mm,
            entry.rect_width_mm,
            entry.rect_height_mm,
        )
        snapped = snap.snap_rect(
            rect,
            work.paper,
            page.comas,
            gap_h_mm=work.coma_gap.horizontal_mm,
            gap_v_mm=work.coma_gap.vertical_mm,
            exclude_stem=entry.coma_id,
        )
        entry.rect_x_mm = snapped.x
        entry.rect_y_mm = snapped.y
        entry.rect_width_mm = snapped.width
        entry.rect_height_mm = snapped.height
        self.report({"INFO"}, f"スナップ適用: {entry.coma_id}")
        return {"FINISHED"}


_CLASSES = (BNAME_OT_snap_active_coma,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
