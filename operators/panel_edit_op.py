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
from . import panel_picker

_logger = log.get_logger(__name__)


def _resolve_target_from_event(context, event) -> None:
    """``event.mouse_x/y`` の直下にあるコマへ active_page/panel をフォーカス.

    overview モード中でもカーソル位置のコマに対して操作が効くようにする
    (計画書 3. Phase 1)。VIEW_3D 領域外のクリックなら何もしない (現在の
    active を維持)。
    """
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    from ..utils import geom

    work = get_work(context)
    if work is None or not work.loaded:
        return
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if not (
                region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height
            ):
                continue
            rv3d = getattr(area.spaces.active, "region_3d", None)
            if rv3d is None:
                continue
            mx = event.mouse_x - region.x
            my = event.mouse_y - region.y
            loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
            if loc is None:
                continue
            hit = panel_picker.find_panel_at_world_mm(
                work, geom.m_to_mm(loc.x), geom.m_to_mm(loc.y)
            )
            if hit is None:
                return
            page_idx, panel_idx = hit
            if 0 <= page_idx < len(work.pages):
                work.active_page_index = page_idx
                page = work.pages[page_idx]
                if 0 <= panel_idx < len(page.panels):
                    page.active_panel_index = panel_idx
            return


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
        work = get_work(context)
        return work is not None and work.loaded

    def invoke(self, context, event):
        # マウス直下のコマへフォーカス (overview 時は全ページ逆引き)
        _resolve_target_from_event(context, event)
        page = get_active_page(context)
        if (
            page is None
            or not (0 <= page.active_panel_index < len(page.panels))
            or page.panels[page.active_panel_index].shape_type != "rect"
        ):
            self.report({"WARNING"}, "矩形コマを選択してください")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        idx = page.active_panel_index
        if not (0 <= idx < len(page.panels)):
            return {"CANCELLED"}
        src = page.panels[idx]
        if src.shape_type != "rect":
            self.report({"WARNING"}, "矩形コマにのみ適用可能です")
            return {"CANCELLED"}
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
        work = get_work(context)
        return work is not None and work.loaded

    def invoke(self, context, event):
        _resolve_target_from_event(context, event)
        page = get_active_page(context)
        if (
            page is None
            or not (0 <= page.active_panel_index < len(page.panels))
            or page.panels[page.active_panel_index].shape_type != "rect"
        ):
            self.report({"WARNING"}, "矩形コマを選択してください")
            return {"CANCELLED"}
        return self.execute(context)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        if not (0 <= page.active_panel_index < len(page.panels)):
            return {"CANCELLED"}
        entry = page.panels[page.active_panel_index]
        if entry.shape_type != "rect":
            self.report({"WARNING"}, "矩形コマにのみ適用可能です")
            return {"CANCELLED"}
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
        work_dir = Path(work.work_dir) if work.work_dir else None
        if work_dir is not None:
            try:
                panel_io.save_panel_meta(work_dir, page.id, entry)
                page_io.save_page_json(work_dir, page)
            except Exception as exc:  # noqa: BLE001
                _logger.exception("panel_to_polygon: save failed")
                self.report({"ERROR"}, f"保存失敗: {exc}")
                return {"CANCELLED"}
        self.report({"INFO"}, "多角形化しました")
        return {"FINISHED"}


class BNAME_OT_panel_to_rect(Operator):
    """多角形/曲線/フリーフォームのコマを矩形化 (外接矩形で近似)."""

    bl_idname = "bname.panel_to_rect"
    bl_label = "矩形化"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded

    def invoke(self, context, event):
        _resolve_target_from_event(context, event)
        page = get_active_page(context)
        if (
            page is None
            or not (0 <= page.active_panel_index < len(page.panels))
            or page.panels[page.active_panel_index].shape_type == "rect"
        ):
            self.report({"WARNING"}, "多角形コマを選択してください")
            return {"CANCELLED"}
        return self.execute(context)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        if not (0 <= page.active_panel_index < len(page.panels)):
            return {"CANCELLED"}
        entry = page.panels[page.active_panel_index]
        if entry.shape_type == "rect":
            self.report({"WARNING"}, "既に矩形コマです")
            return {"CANCELLED"}
        if len(entry.vertices) > 0:
            xs = [v.x_mm for v in entry.vertices]
            ys = [v.y_mm for v in entry.vertices]
            entry.rect_x_mm = min(xs)
            entry.rect_y_mm = min(ys)
            entry.rect_width_mm = max(xs) - min(xs)
            entry.rect_height_mm = max(ys) - min(ys)
        entry.vertices.clear()
        entry.shape_type = "rect"
        work_dir = Path(work.work_dir) if work.work_dir else None
        if work_dir is not None:
            try:
                panel_io.save_panel_meta(work_dir, page.id, entry)
                page_io.save_page_json(work_dir, page)
            except Exception as exc:  # noqa: BLE001
                _logger.exception("panel_to_rect: save failed")
                self.report({"ERROR"}, f"保存失敗: {exc}")
                return {"CANCELLED"}
        self.report({"INFO"}, "矩形化しました")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_panel_cut,
    BNAME_OT_panel_to_polygon,
    BNAME_OT_panel_to_rect,
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
