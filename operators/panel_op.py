"""コマ追加・削除・複製・移動・Z順序変更 Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..io import page_io, panel_io, schema
from ..utils import layer_stack as layer_stack_utils
from ..utils import log, page_grid, paths
from .panel_knife_cut_op import _panel_polygon, _polygon_area, _set_panel_polygon, _split_convex_polygon_by_line
from . import panel_modal_state

_logger = log.get_logger(__name__)


# ---------- 共通ヘルパ ----------


def _require_active_page(op: Operator, context):
    work = get_work(context)
    page = get_active_page(context)
    if work is None or not work.loaded or page is None:
        op.report({"ERROR"}, "作品 / ページが選択されていません")
        return None, None
    return work, page


def _save_page_and_pages(work, page, work_dir: Path) -> None:
    page_io.save_page_json(work_dir, page)
    page.panel_count = len(page.panels)
    page_io.save_pages_json(work_dir, work)


def _sync_layer_stack_after_panel_change(context) -> None:
    layer_stack_utils.sync_layer_stack_after_data_change(
        context,
        align_panel_order=True,
    )


def _selected_edge_panel_target(context):
    wm = context.window_manager
    if getattr(wm, "bname_edge_select_kind", "none") == "none":
        return None
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    page_index = int(getattr(wm, "bname_edge_select_page", -1))
    panel_index = int(getattr(wm, "bname_edge_select_panel", -1))
    if not (0 <= page_index < len(work.pages)):
        return None
    page = work.pages[page_index]
    if not (0 <= panel_index < len(page.panels)):
        return None
    return work, page_index, page, panel_index, page.panels[panel_index]


def _require_target_panel(op: Operator, context):
    selected = _selected_edge_panel_target(context)
    if selected is not None:
        work, page_index, page, panel_index, panel = selected
        work.active_page_index = page_index
        page.active_panel_index = panel_index
        return work, page_index, page, panel_index, panel, True

    work = get_work(context)
    page = get_active_page(context)
    if work is None or not work.loaded or page is None:
        op.report({"ERROR"}, "作品 / コマが選択されていません")
        return None
    panel_index = int(page.active_panel_index)
    if not (0 <= panel_index < len(page.panels)):
        op.report({"ERROR"}, "コマが選択されていません")
        return None
    page_index = int(work.active_page_index)
    return work, page_index, page, panel_index, page.panels[panel_index], False


def _set_edge_selection(context, *, kind: str, page_index: int, panel_index: int) -> None:
    wm = context.window_manager
    wm.bname_edge_select_kind = kind
    wm.bname_edge_select_page = int(page_index)
    wm.bname_edge_select_panel = int(panel_index)
    wm.bname_edge_select_edge = -1
    wm.bname_edge_select_vertex = -1


def _panel_bounds_mm(poly: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if len(poly) < 3:
        return None
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _split_polygon_grid(
    poly: list[tuple[float, float]],
    rows: int,
    cols: int,
    gap_v: float,
    gap_h: float,
) -> tuple[list[list[list[tuple[float, float]]]], float, float] | None:
    bounds = _panel_bounds_mm(poly)
    if bounds is None:
        return None
    min_x, min_y, max_x, max_y = bounds
    total_gap_w = gap_h * (cols - 1)
    total_gap_h = gap_v * (rows - 1)
    width = max_x - min_x
    height = max_y - min_y
    cell_w = (width - total_gap_w) / cols
    cell_h = (height - total_gap_h) / rows
    if cell_w <= 0.0 or cell_h <= 0.0:
        return None

    columns: list[list[tuple[float, float]]] = []
    remaining = list(poly)
    for c in range(cols - 1):
        x_cut = min_x + (c + 1) * cell_w + (c + 0.5) * gap_h
        split = _split_convex_polygon_by_line(
            remaining,
            (x_cut, min_y - 1.0),
            (x_cut, max_y + 1.0),
            gap_mm=gap_h,
        )
        if split is None:
            return None
        right_poly, left_poly = split
        if _polygon_area(left_poly) < 0.01 or _polygon_area(right_poly) < 0.01:
            return None
        columns.append(left_poly)
        remaining = right_poly
    columns.append(remaining)

    grid: list[list[list[tuple[float, float]]]] = []
    for col_poly in columns:
        bottoms: list[list[tuple[float, float]]] = []
        remaining_col = col_poly
        for r in range(rows - 1):
            y_cut = min_y + (r + 1) * cell_h + (r + 0.5) * gap_v
            split = _split_convex_polygon_by_line(
                remaining_col,
                (min_x - 1.0, y_cut),
                (max_x + 1.0, y_cut),
                gap_mm=gap_v,
            )
            if split is None:
                return None
            bottom_poly, top_poly = split
            if _polygon_area(bottom_poly) < 0.01 or _polygon_area(top_poly) < 0.01:
                return None
            bottoms.append(bottom_poly)
            remaining_col = top_poly
        grid.append([remaining_col] + list(reversed(bottoms)))
    return grid, cell_w, cell_h


# ---------- コマ生成ヘルパ (page_op などから再利用) ----------


def create_rect_panel(
    work,
    page,
    work_dir: Path,
    x_mm: float,
    y_mm: float,
    width_mm: float,
    height_mm: float,
    title: str | None = None,
):
    """指定矩形の新規コマを page.panels に追加し、panel.json と page.json を保存.

    pages.json の保存は呼出側の責務。新規 entry を返す。
    """
    stem = panel_io.allocate_new_panel_stem(work_dir, page.id)
    entry = page.panels.add()
    entry.panel_stem = stem
    entry.id = stem.split("_", 1)[1]
    entry.title = title if title is not None else stem
    entry.shape_type = "rect"
    entry.rect_x_mm = x_mm
    entry.rect_y_mm = y_mm
    entry.rect_width_mm = width_mm
    entry.rect_height_mm = height_mm
    # 追加直後の entry を除いて max を取る (entry 自身は初期値 0)
    max_z = max(
        (pe.z_order for pe in page.panels if pe is not entry),
        default=-1,
    )
    entry.z_order = max_z + 1
    page.active_panel_index = len(page.panels) - 1
    panel_io.save_panel_meta(work_dir, page.id, entry)
    page_io.save_page_json(work_dir, page)
    page.panel_count = len(page.panels)
    return entry


def create_basic_frame_panel(work, page, work_dir: Path):
    """基本枠 (用紙の inner_frame) サイズの矩形コマを 1 個生成して返す."""
    p = work.paper
    x_mm = (p.canvas_width_mm - p.inner_frame_width_mm) / 2.0 + p.inner_frame_offset_x_mm
    y_mm = (p.canvas_height_mm - p.inner_frame_height_mm) / 2.0 + p.inner_frame_offset_y_mm
    return create_rect_panel(
        work,
        page,
        work_dir,
        x_mm,
        y_mm,
        p.inner_frame_width_mm,
        p.inner_frame_height_mm,
        title="基本枠",
    )


# ---------- コマ追加 ----------


class BNAME_OT_panel_add(Operator):
    """現在のページに矩形コマを追加."""

    bl_idname = "bname.panel_add"
    bl_label = "コマを追加"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return get_active_page(context) is not None

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        try:
            # デフォルトは中央に 60×40mm の矩形
            p = work.paper
            x_mm = (p.canvas_width_mm - 60.0) / 2.0
            y_mm = (p.canvas_height_mm - 40.0) / 2.0
            entry = create_rect_panel(work, page, work_dir, x_mm, y_mm, 60.0, 40.0)
            stem = entry.panel_stem
            page_io.save_pages_json(work_dir, work)
            if hasattr(context.scene, "bname_active_layer_kind"):
                context.scene.bname_active_layer_kind = "panel"
            _sync_layer_stack_after_panel_change(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_add failed")
            self.report({"ERROR"}, f"コマ追加失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ追加: {stem}")
        return {"FINISHED"}


class BNAME_OT_panel_remove(Operator):
    """選択中のコマを削除."""

    bl_idname = "bname.panel_remove"
    bl_label = "コマを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return page is not None and 0 <= page.active_panel_index < len(page.panels)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_panel_index
        if not (0 <= idx < len(page.panels)):
            return {"CANCELLED"}
        entry = page.panels[idx]
        stem = entry.panel_stem
        work_dir = Path(work.work_dir)
        try:
            panel_io.remove_panel_files(work_dir, page.id, stem)
            layer_stack_utils.delete_gp_layers_for_parent_keys(
                context, {layer_stack_utils.gp_parent_key_for_panel(page, entry)}
            )
            page.panels.remove(idx)
            if len(page.panels) == 0:
                page.active_panel_index = -1
            elif idx >= len(page.panels):
                page.active_panel_index = len(page.panels) - 1
            _save_page_and_pages(work, page, work_dir)
            _sync_layer_stack_after_panel_change(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_remove failed")
            self.report({"ERROR"}, f"コマ削除失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ削除: {stem}")
        return {"FINISHED"}


class BNAME_OT_panel_duplicate(Operator):
    """選択中のコマを同ページ内で複製."""

    bl_idname = "bname.panel_duplicate"
    bl_label = "コマを複製"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return page is not None and 0 <= page.active_panel_index < len(page.panels)

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_panel_index
        src = page.panels[idx]
        work_dir = Path(work.work_dir)
        try:
            new_stem = panel_io.allocate_new_panel_stem(work_dir, page.id)
            panel_io.copy_panel_files(work_dir, page.id, page.id, src.panel_stem, new_stem)
            # entry を複製
            new_entry = page.panels.add()
            _copy_panel_entry(src, new_entry)
            new_entry.panel_stem = new_stem
            new_entry.id = new_stem.split("_", 1)[1]
            new_entry.title = f"{src.title} (複製)"
            new_entry.rect_x_mm = src.rect_x_mm + 5.0
            new_entry.rect_y_mm = src.rect_y_mm - 5.0
            new_entry.z_order = max((p.z_order for p in page.panels), default=0) + 1
            # 直後に配置
            new_index = len(page.panels) - 1
            if new_index != idx + 1:
                page.panels.move(new_index, idx + 1)
            page.active_panel_index = idx + 1
            if hasattr(context.scene, "bname_active_layer_kind"):
                context.scene.bname_active_layer_kind = "panel"
            panel_io.save_panel_meta(work_dir, page.id, new_entry)
            _save_page_and_pages(work, page, work_dir)
            _sync_layer_stack_after_panel_change(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_duplicate failed")
            self.report({"ERROR"}, f"コマ複製失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ複製: {src.panel_stem} → {new_stem}")
        return {"FINISHED"}


def _copy_panel_entry(src, dst) -> None:
    """PanelEntry の内容を複製.

    PointerProperty (border / white_margin) と CollectionProperty
    (vertices / layer_refs) を含めてすべてコピーするため、schema 経由で
    dict を往復させる。dst.id / dst.panel_stem は呼出側で上書きされる想定。
    """
    data = schema.panel_entry_to_dict(src)
    schema.panel_entry_from_dict(dst, data)


# ---------- 他ページへの移動 ----------


def _other_page_enum_items(_self, context):
    work = get_work(context)
    active_idx = work.active_page_index if work else -1
    cache: list[tuple[str, str, str]] = []
    if work is None:
        return [("", "(ページなし)", "")]
    for i, p in enumerate(work.pages):
        if i == active_idx:
            continue
        cache.append((p.id, p.title or p.id, ""))
    if not cache:
        cache.append(("", "(他のページなし)", ""))
    _OTHER_PAGE_CACHE[:] = cache
    return _OTHER_PAGE_CACHE


_OTHER_PAGE_CACHE: list[tuple[str, str, str]] = []


class BNAME_OT_panel_move_to_page(Operator):
    """選択中のコマを別のページへ移動."""

    bl_idname = "bname.panel_move_to_page"
    bl_label = "コマを他ページへ移動"
    bl_options = {"REGISTER", "UNDO"}

    target_page_id: EnumProperty(  # type: ignore[valid-type]
        name="移動先ページ",
        items=_other_page_enum_items,
    )

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        page = get_active_page(context)
        return (
            w is not None
            and w.loaded
            and page is not None
            and 0 <= page.active_panel_index < len(page.panels)
            and len(w.pages) >= 2
        )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        if not self.target_page_id:
            self.report({"ERROR"}, "移動先ページを選択してください")
            return {"CANCELLED"}
        # 移動先ページ
        target_page = None
        for p in work.pages:
            if p.id == self.target_page_id:
                target_page = p
                break
        if target_page is None:
            self.report({"ERROR"}, f"移動先ページが見つかりません: {self.target_page_id}")
            return {"CANCELLED"}
        idx = page.active_panel_index
        src_entry = page.panels[idx]
        old_parent_key = layer_stack_utils.gp_parent_key_for_panel(page, src_entry)
        work_dir = Path(work.work_dir)
        try:
            # 移動先で衝突しないファイル名を採番
            dst_stem = panel_io.allocate_new_panel_stem(work_dir, target_page.id)
            panel_io.move_panel_files(
                work_dir, page.id, target_page.id, src_entry.panel_stem, dst_stem
            )
            # 移動先 collection に追加
            new_entry = target_page.panels.add()
            _copy_panel_entry(src_entry, new_entry)
            new_entry.panel_stem = dst_stem
            new_entry.id = dst_stem.split("_", 1)[1]
            new_entry.title = src_entry.title
            new_parent_key = layer_stack_utils.gp_parent_key_for_panel(target_page, new_entry)
            source_page_index = next((i for i, p in enumerate(work.pages) if p.id == page.id), -1)
            target_page_index = next((i for i, p in enumerate(work.pages) if p.id == target_page.id), -1)
            if source_page_index >= 0 and target_page_index >= 0:
                src_ox, src_oy = page_grid.page_total_offset_mm(work, context.scene, source_page_index)
                dst_ox, dst_oy = page_grid.page_total_offset_mm(work, context.scene, target_page_index)
                layer_stack_utils.translate_gp_layers_for_parent_keys(
                    context, {old_parent_key}, dst_ox - src_ox, dst_oy - src_oy
                )
            layer_stack_utils.reparent_gp_layers(context, old_parent_key, new_parent_key)
            # 元の collection から削除
            page.panels.remove(idx)
            if len(page.panels) == 0:
                page.active_panel_index = -1
            elif idx >= len(page.panels):
                page.active_panel_index = len(page.panels) - 1
            page_io.save_page_json(work_dir, page)
            page_io.save_page_json(work_dir, target_page)
            page.panel_count = len(page.panels)
            target_page.panel_count = len(target_page.panels)
            page_io.save_pages_json(work_dir, work)
            _sync_layer_stack_after_panel_change(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_move_to_page failed")
            self.report({"ERROR"}, f"コマ移動失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ移動: → {target_page.id}/{dst_stem}")
        return {"FINISHED"}


# ---------- Z順序 ----------


class BNAME_OT_panel_z_order(Operator):
    """選択中のコマの Z 順序を変更."""

    bl_idname = "bname.panel_z_order"
    bl_label = "Z順序変更"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(  # type: ignore[valid-type]
        name="方向",
        items=(
            ("FRONT", "最前面", ""),
            ("BACK", "最背面", ""),
            ("FORWARD", "前面へ", ""),
            ("BACKWARD", "背面へ", ""),
        ),
    )

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return page is not None and 0 <= page.active_panel_index < len(page.panels)

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_panel_index
        current = page.panels[idx].z_order
        all_orders = [p.z_order for p in page.panels]
        mn, mx = (min(all_orders), max(all_orders)) if all_orders else (0, 0)
        if self.direction == "FRONT":
            page.panels[idx].z_order = mx + 1
        elif self.direction == "BACK":
            page.panels[idx].z_order = mn - 1
        elif self.direction == "FORWARD":
            page.panels[idx].z_order = current + 1
        elif self.direction == "BACKWARD":
            page.panels[idx].z_order = current - 1
        work_dir = Path(work.work_dir)
        try:
            panel_io.save_panel_meta(work_dir, page.id, page.panels[idx])
            page_io.save_page_json(work_dir, page)
            _sync_layer_stack_after_panel_change(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_z_order failed")
            self.report({"ERROR"}, f"Z順序変更失敗: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


# ---------- 分割テンプレート ----------


class BNAME_OT_panel_split_template(Operator):
    """選択中コマを縦横に均等分割して置き換える."""

    bl_idname = "bname.panel_split_template"
    bl_label = "分割テンプレートで一括生成"
    bl_options = {"REGISTER", "UNDO"}

    rows: IntProperty(name="行数", default=3, min=1, soft_max=10)  # type: ignore[valid-type]
    cols: IntProperty(name="列数", default=2, min=1, soft_max=10)  # type: ignore[valid-type]
    clear_existing: BoolProperty(  # type: ignore[valid-type]
        name="元コマを削除",
        description="ON で選択中コマを削除してから分割結果に置き換える",
        default=True,
    )
    target_page_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    target_panel_index: IntProperty(default=-1, options={"HIDDEN"})  # type: ignore[valid-type]
    target_from_edge_selection: BoolProperty(default=False, options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return False
        if _selected_edge_panel_target(context) is not None:
            return True
        page = get_active_page(context)
        return page is not None and 0 <= page.active_panel_index < len(page.panels)

    def invoke(self, context, event):
        target = _require_target_panel(self, context)
        if target is None:
            return {"CANCELLED"}
        work, page_index, page, panel_index, panel, from_edge = target
        if panel.shape_type not in {"rect", "polygon"}:
            self.report({"WARNING"}, "矩形または多角形コマを選択してください")
            return {"CANCELLED"}
        self.target_page_id = page.id
        self.target_panel_index = panel_index
        self.target_from_edge_selection = from_edge
        work.active_page_index = page_index
        page.active_panel_index = panel_index
        panel_modal_state.finish_all(context)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        page = None
        page_index = -1
        for i, page_entry in enumerate(work.pages):
            if page_entry.id == self.target_page_id:
                page = page_entry
                page_index = i
                break
        if page is None:
            target = _require_target_panel(self, context)
            if target is None:
                return {"CANCELLED"}
            work, page_index, page, self.target_panel_index, _panel, self.target_from_edge_selection = target
            self.target_page_id = page.id
        if not (0 <= self.target_panel_index < len(page.panels)):
            self.report({"ERROR"}, "分割対象のコマが見つかりません")
            return {"CANCELLED"}
        src = page.panels[self.target_panel_index]
        if src.shape_type not in {"rect", "polygon"}:
            self.report({"WARNING"}, "矩形または多角形コマを選択してください")
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        panel_modal_state.finish_all(context)
        gap_v = work.panel_gap.vertical_mm
        gap_h = work.panel_gap.horizontal_mm
        rows, cols = self.rows, self.cols
        src_title = src.title
        src_z = int(src.z_order)
        src_template = schema.panel_entry_to_dict(src)
        insert_z_base = src_z if self.clear_existing else (
            max((p.z_order for p in page.panels), default=-1) + 1
        )
        src_poly = _panel_polygon(src)
        if src.shape_type == "rect":
            total_gap_w = gap_h * (cols - 1)
            total_gap_h = gap_v * (rows - 1)
            cell_w = (float(src.rect_width_mm) - total_gap_w) / cols
            cell_h = (float(src.rect_height_mm) - total_gap_h) / rows
            if cell_w <= 0.0 or cell_h <= 0.0:
                self.report({"ERROR"}, "コマが小さすぎるため、この分割数では作成できません")
                return {"CANCELLED"}
            inner_x = float(src.rect_x_mm)
            inner_y = float(src.rect_y_mm)
            split_grid = None
        else:
            split_result = _split_polygon_grid(src_poly, rows, cols, gap_v, gap_h)
            if split_result is None:
                self.report({"ERROR"}, "この多角形コマは均等分割できません")
                return {"CANCELLED"}
            split_grid, cell_w, cell_h = split_result

        try:
            if self.clear_existing:
                panel_io.remove_panel_files(work_dir, page.id, src.panel_stem)
                layer_stack_utils.delete_gp_layers_for_parent_keys(
                    context, {layer_stack_utils.gp_parent_key_for_panel(page, src)}
                )
                page.panels.remove(self.target_panel_index)
                if len(page.panels) == 0:
                    page.active_panel_index = -1
                elif self.target_panel_index >= len(page.panels):
                    page.active_panel_index = len(page.panels) - 1

            first_new_index = len(page.panels)
            # 行は上から下へ (漫画は右→左の読み順だが、ここでは配列順のみ)
            for r in range(rows):
                for c in range(cols):
                    stem = panel_io.allocate_new_panel_stem(work_dir, page.id)
                    entry = page.panels.add()
                    schema.panel_entry_from_dict(entry, src_template)
                    entry.panel_stem = stem
                    entry.id = stem.split("_", 1)[1]
                    entry.title = f"{src_title} {r + 1}-{c + 1}"
                    if split_grid is None:
                        entry.shape_type = "rect"
                        entry.vertices.clear()
                        entry.rect_x_mm = inner_x + c * (cell_w + gap_h)
                        entry.rect_y_mm = (
                            inner_y + (rows - 1 - r) * (cell_h + gap_v)
                        )
                        entry.rect_width_mm = cell_w
                        entry.rect_height_mm = cell_h
                    else:
                        _set_panel_polygon(entry, split_grid[c][r])
                        entry.edge_styles.clear()
                    entry.z_order = insert_z_base + r * cols + c
                    panel_io.save_panel_meta(work_dir, page.id, entry)
            page.active_panel_index = first_new_index if len(page.panels) > first_new_index else -1
            work.active_page_index = page_index
            _save_page_and_pages(work, page, work_dir)
            _sync_layer_stack_after_panel_change(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_split_template failed")
            self.report({"ERROR"}, f"分割失敗: {exc}")
            return {"CANCELLED"}
        if self.target_from_edge_selection and page.active_panel_index >= 0:
            _set_edge_selection(
                context,
                kind="border",
                page_index=page_index,
                panel_index=page.active_panel_index,
            )
        self.report({"INFO"}, f"分割: {rows}×{cols} = {rows * cols} コマ")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_panel_add,
    BNAME_OT_panel_remove,
    BNAME_OT_panel_duplicate,
    BNAME_OT_panel_move_to_page,
    BNAME_OT_panel_z_order,
    BNAME_OT_panel_split_template,
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
