"""コマ枠の高度な編集 Operator (Phase 2.5).

計画書 3.2.5.2 / Phase 2.5 参照。実装は骨格レベル (シグネチャと最小限の
動作) とし、詳細な対話的編集 (ドラッグ分割・ベジェ編集) は将来の拡張。
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import FloatProperty, IntProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..io import page_io, panel_io
from ..utils import log, paths

_logger = log.get_logger(__name__)


class BNAME_OT_panel_cut(Operator):
    """選択中のコマを水平/垂直で分割 (枠線カットツール簡易版)."""

    bl_idname = "bname.panel_cut"
    bl_label = "コマを分割"
    bl_options = {"REGISTER", "UNDO"}

    axis: IntProperty(  # type: ignore[valid-type]
        name="軸",
        description="0=水平カット (上下分割), 1=垂直カット (左右分割)",
        default=0,
        min=0,
        max=1,
    )
    ratio: FloatProperty(  # type: ignore[valid-type]
        name="分割比",
        description="0.0-1.0 で分割位置を指定 (0.5 で中央)",
        default=0.5,
        min=0.05,
        max=0.95,
    )

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return (
            page is not None
            and 0 <= page.active_panel_index < len(page.panels)
            and page.panels[page.active_panel_index].shape_type == "rect"
        )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        idx = page.active_panel_index
        src = page.panels[idx]
        work_dir = Path(work.work_dir)
        gap_v = work.panel_gap.vertical_mm
        gap_h = work.panel_gap.horizontal_mm

        try:
            if self.axis == 0:
                # 水平カット: 上下に分割
                total_h = src.rect_height_mm - gap_v
                top_h = total_h * (1.0 - self.ratio)
                bot_h = total_h * self.ratio
                new_stem = panel_io.allocate_new_panel_stem(work_dir, page.id)
                new_entry = page.panels.add()
                from .panel_op import _copy_panel_entry

                _copy_panel_entry(src, new_entry)
                new_entry.panel_stem = new_stem
                new_entry.id = new_stem.split("_", 1)[1]
                new_entry.title = f"{src.title} 下"
                new_entry.rect_x_mm = src.rect_x_mm
                new_entry.rect_y_mm = src.rect_y_mm
                new_entry.rect_width_mm = src.rect_width_mm
                new_entry.rect_height_mm = bot_h
                new_entry.z_order = max((p.z_order for p in page.panels), default=0) + 1
                # src を上側に縮小
                src.rect_y_mm += bot_h + gap_v
                src.rect_height_mm = top_h
            else:
                # 垂直カット: 左右に分割
                total_w = src.rect_width_mm - gap_h
                left_w = total_w * self.ratio
                right_w = total_w * (1.0 - self.ratio)
                new_stem = panel_io.allocate_new_panel_stem(work_dir, page.id)
                new_entry = page.panels.add()
                from .panel_op import _copy_panel_entry

                _copy_panel_entry(src, new_entry)
                new_entry.panel_stem = new_stem
                new_entry.id = new_stem.split("_", 1)[1]
                new_entry.title = f"{src.title} 右"
                new_entry.rect_x_mm = src.rect_x_mm + left_w + gap_h
                new_entry.rect_y_mm = src.rect_y_mm
                new_entry.rect_width_mm = right_w
                new_entry.rect_height_mm = src.rect_height_mm
                new_entry.z_order = max((p.z_order for p in page.panels), default=0) + 1
                # src を左側に縮小
                src.rect_width_mm = left_w

            panel_io.save_panel_meta(work_dir, page.id, src)
            panel_io.save_panel_meta(work_dir, page.id, new_entry)
            page_io.save_page_json(work_dir, page)
            page.panel_count = len(page.panels)
            page_io.save_pages_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_cut failed")
            self.report({"ERROR"}, f"コマ分割失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, "コマを分割しました")
        return {"FINISHED"}


class BNAME_OT_panel_to_polygon(Operator):
    """矩形コマを多角形化 (4 頂点を vertices にセット)."""

    bl_idname = "bname.panel_to_polygon"
    bl_label = "多角形化"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None or not (0 <= page.active_panel_index < len(page.panels)):
            return False
        return page.panels[page.active_panel_index].shape_type == "rect"

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        entry = page.panels[page.active_panel_index]
        entry.vertices.clear()
        corners = [
            (entry.rect_x_mm, entry.rect_y_mm),
            (entry.rect_x_mm + entry.rect_width_mm, entry.rect_y_mm),
            (entry.rect_x_mm + entry.rect_width_mm, entry.rect_y_mm + entry.rect_height_mm),
            (entry.rect_x_mm, entry.rect_y_mm + entry.rect_height_mm),
        ]
        for x, y in corners:
            v = entry.vertices.add()
            v.x_mm = x
            v.y_mm = y
        entry.shape_type = "polygon"
        self.report({"INFO"}, "多角形化しました")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_panel_cut,
    BNAME_OT_panel_to_polygon,
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
