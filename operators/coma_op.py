"""コマ追加・削除・複製・移動・Z順序変更 Operator."""

from __future__ import annotations

from pathlib import Path
import math

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..io import page_io, coma_io, schema
from ..utils import edge_selection, object_selection
from ..utils import layer_stack as layer_stack_utils
from ..utils import log, page_grid, paths
from .coma_knife_cut_op import _coma_polygon, _polygon_area, _set_coma_polygon, _split_convex_polygon_by_line
from . import coma_modal_state

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
    page.coma_count = len(page.comas)
    page_io.save_pages_json(work_dir, work)


def _sync_layer_stack_after_coma_change(context) -> None:
    layer_stack_utils.sync_layer_stack_after_data_change(
        context,
        align_coma_order=True,
    )


def _selected_edge_coma_target(context):
    wm = context.window_manager
    if getattr(wm, "bname_edge_select_kind", "none") == "none":
        return None
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    page_index = int(getattr(wm, "bname_edge_select_page", -1))
    coma_index = int(getattr(wm, "bname_edge_select_coma", -1))
    if not (0 <= page_index < len(work.pages)):
        return None
    page = work.pages[page_index]
    if not (0 <= coma_index < len(page.comas)):
        return None
    return work, page_index, page, coma_index, page.comas[coma_index]


def _require_target_coma(op: Operator, context):
    selected = _selected_edge_coma_target(context)
    if selected is not None:
        work, page_index, page, coma_index, panel = selected
        work.active_page_index = page_index
        page.active_coma_index = coma_index
        return work, page_index, page, coma_index, panel, True

    work = get_work(context)
    page = get_active_page(context)
    if work is None or not work.loaded or page is None:
        op.report({"ERROR"}, "作品 / コマが選択されていません")
        return None
    coma_index = int(page.active_coma_index)
    if not (0 <= coma_index < len(page.comas)):
        op.report({"ERROR"}, "コマが選択されていません")
        return None
    page_index = int(work.active_page_index)
    return work, page_index, page, coma_index, page.comas[coma_index], False


def _set_edge_selection(context, *, kind: str, page_index: int, coma_index: int) -> None:
    edge_selection.set_selection(
        context,
        kind,
        page_index=page_index,
        coma_index=coma_index,
    )


def _coma_bounds_mm(poly: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if len(poly) < 3:
        return None
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


_MERGE_TOL_MM = 1.0e-4


def _merge_point_key(point: tuple[float, float]) -> tuple[int, int]:
    return (
        int(round(float(point[0]) / _MERGE_TOL_MM)),
        int(round(float(point[1]) / _MERGE_TOL_MM)),
    )


def _merge_lerp(a: tuple[float, float], b: tuple[float, float], t: float) -> tuple[float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _merge_edge_t(
    a: tuple[float, float],
    b: tuple[float, float],
    point: tuple[float, float],
) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    denom = dx * dx + dy * dy
    if denom <= 1.0e-12:
        return 0.0
    return ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denom


def _merge_edges_collinear(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= _MERGE_TOL_MM:
        return False
    cross_c = abs(dx * (c[1] - a[1]) - dy * (c[0] - a[0])) / length
    cross_d = abs(dx * (d[1] - a[1]) - dy * (d[0] - a[0])) / length
    if cross_c > _MERGE_TOL_MM * 5.0 or cross_d > _MERGE_TOL_MM * 5.0:
        return False
    t_c = _merge_edge_t(a, b, c)
    t_d = _merge_edge_t(a, b, d)
    return max(0.0, min(t_c, t_d)) < min(1.0, max(t_c, t_d)) - 1.0e-8


def _merge_remove_collinear(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    changed = True
    out = list(points)
    while changed and len(out) >= 3:
        changed = False
        kept = []
        n = len(out)
        for i, cur in enumerate(out):
            prev = out[(i - 1) % n]
            nxt = out[(i + 1) % n]
            ux = cur[0] - prev[0]
            uy = cur[1] - prev[1]
            vx = nxt[0] - cur[0]
            vy = nxt[1] - cur[1]
            if math.hypot(ux, uy) <= _MERGE_TOL_MM or math.hypot(vx, vy) <= _MERGE_TOL_MM:
                changed = True
                continue
            cross = abs(ux * vy - uy * vx)
            if cross <= _MERGE_TOL_MM * max(1.0, math.hypot(ux, uy), math.hypot(vx, vy)):
                dot = ux * vx + uy * vy
                if dot >= 0.0:
                    changed = True
                    continue
            kept.append(cur)
        out = kept
    return out


def _merge_signed_area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for i, point in enumerate(points):
        nxt = points[(i + 1) % len(points)]
        total += point[0] * nxt[1] - nxt[0] * point[1]
    return total * 0.5


def _merge_boundary_polygon(polys: list[list[tuple[float, float]]]) -> list[tuple[float, float]] | None:
    raw_edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for poly in polys:
        if len(poly) < 3:
            continue
        pts = list(poly)
        if _merge_signed_area(pts) < 0.0:
            pts.reverse()
        for i, a in enumerate(pts):
            b = pts[(i + 1) % len(pts)]
            if math.hypot(b[0] - a[0], b[1] - a[1]) > _MERGE_TOL_MM:
                raw_edges.append((a, b))
    if not raw_edges:
        return None

    point_by_key: dict[tuple[int, int], tuple[float, float]] = {}
    signed_segments: dict[tuple[tuple[int, int], tuple[int, int]], int] = {}
    for i, (a, b) in enumerate(raw_edges):
        t_values = [0.0, 1.0]
        for j, (c, d) in enumerate(raw_edges):
            if i == j or not _merge_edges_collinear(a, b, c, d):
                continue
            t_c = _merge_edge_t(a, b, c)
            t_d = _merge_edge_t(a, b, d)
            lo = max(0.0, min(t_c, t_d))
            hi = min(1.0, max(t_c, t_d))
            if hi - lo > 1.0e-8:
                t_values.extend((lo, hi))
        t_values = sorted({round(t, 10) for t in t_values if -1.0e-8 <= t <= 1.0 + 1.0e-8})
        for t0, t1 in zip(t_values, t_values[1:], strict=False):
            if t1 - t0 <= 1.0e-8:
                continue
            p0 = _merge_lerp(a, b, max(0.0, min(1.0, t0)))
            p1 = _merge_lerp(a, b, max(0.0, min(1.0, t1)))
            k0 = _merge_point_key(p0)
            k1 = _merge_point_key(p1)
            if k0 == k1:
                continue
            point_by_key.setdefault(k0, p0)
            point_by_key.setdefault(k1, p1)
            c0, c1 = (k0, k1) if k0 <= k1 else (k1, k0)
            sign = 1 if (k0, k1) == (c0, c1) else -1
            signed_segments[(c0, c1)] = signed_segments.get((c0, c1), 0) + sign

    directed: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for (a_key, b_key), signed in signed_segments.items():
        if signed > 0:
            directed.append((a_key, b_key))
        elif signed < 0:
            directed.append((b_key, a_key))
    if not directed:
        return None

    loops: list[list[tuple[float, float]]] = []
    unused = set(range(len(directed)))
    while unused:
        first = next(iter(unused))
        unused.remove(first)
        start, end = directed[first]
        keys = [start, end]
        while end != start:
            next_index = next((idx for idx in list(unused) if directed[idx][0] == end), -1)
            if next_index < 0:
                return None
            unused.remove(next_index)
            _s, end = directed[next_index]
            keys.append(end)
            if len(keys) > len(directed) + 2:
                return None
        loop = [point_by_key[key] for key in keys[:-1]]
        loop = _merge_remove_collinear(loop)
        if len(loop) >= 3 and _polygon_area(loop) > 0.01:
            loops.append(loop)
    if not loops:
        return None
    loops.sort(key=_polygon_area, reverse=True)
    if len(loops) > 1 and _polygon_area(loops[1]) > 0.01:
        return None
    return loops[0]


def _split_polygon_grid(
    poly: list[tuple[float, float]],
    rows: int,
    cols: int,
    gap_v: float,
    gap_h: float,
) -> tuple[list[list[list[tuple[float, float]]]], float, float] | None:
    bounds = _coma_bounds_mm(poly)
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


def create_rect_coma(
    work,
    page,
    work_dir: Path,
    x_mm: float,
    y_mm: float,
    width_mm: float,
    height_mm: float,
    title: str | None = None,
):
    """指定矩形の新規コマを page.comas に追加し、cNN.json と page.json を保存.

    pages.json の保存は呼出側の責務。新規 entry を返す。
    """
    stem = coma_io.allocate_new_coma_id(work_dir, page.id)
    entry = page.comas.add()
    entry.coma_id = stem
    entry.id = stem
    entry.title = title if title is not None else stem
    entry.shape_type = "rect"
    entry.rect_x_mm = x_mm
    entry.rect_y_mm = y_mm
    entry.rect_width_mm = width_mm
    entry.rect_height_mm = height_mm
    # 追加直後の entry を除いて max を取る (entry 自身は初期値 0)
    max_z = max(
        (pe.z_order for pe in page.comas if pe is not entry),
        default=-1,
    )
    entry.z_order = max_z + 1
    page.active_coma_index = len(page.comas) - 1
    coma_io.save_coma_meta(work_dir, page.id, entry)
    page_io.save_page_json(work_dir, page)
    page.coma_count = len(page.comas)
    return entry


def create_basic_frame_coma(work, page, work_dir: Path):
    """基本枠 (用紙の inner_frame) サイズの矩形コマを 1 個生成して返す."""
    p = work.paper
    x_mm = (p.canvas_width_mm - p.inner_frame_width_mm) / 2.0 + p.inner_frame_offset_x_mm
    y_mm = (p.canvas_height_mm - p.inner_frame_height_mm) / 2.0 + p.inner_frame_offset_y_mm
    return create_rect_coma(
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


class BNAME_OT_coma_add(Operator):
    """現在のページに矩形コマを追加."""

    bl_idname = "bname.coma_add"
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
            entry = create_rect_coma(work, page, work_dir, x_mm, y_mm, 60.0, 40.0)
            stem = entry.coma_id
            page_io.save_pages_json(work_dir, work)
            if hasattr(context.scene, "bname_active_layer_kind"):
                context.scene.bname_active_layer_kind = "coma"
            _sync_layer_stack_after_coma_change(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_add failed")
            self.report({"ERROR"}, f"コマ追加失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ追加: {stem}")
        return {"FINISHED"}


class BNAME_OT_coma_remove(Operator):
    """選択中のコマを削除."""

    bl_idname = "bname.coma_remove"
    bl_label = "コマを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return page is not None and 0 <= page.active_coma_index < len(page.comas)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_coma_index
        if not (0 <= idx < len(page.comas)):
            return {"CANCELLED"}
        entry = page.comas[idx]
        stem = entry.coma_id
        work_dir = Path(work.work_dir)
        try:
            coma_io.remove_coma_files(work_dir, page.id, stem)
            layer_stack_utils.delete_gp_layers_for_parent_keys(
                context, {layer_stack_utils.gp_parent_key_for_coma(page, entry)}
            )
            layer_stack_utils.delete_effect_layers_for_parent_keys(
                context, {layer_stack_utils.gp_parent_key_for_coma(page, entry)}
            )
            page.comas.remove(idx)
            if len(page.comas) == 0:
                page.active_coma_index = -1
            elif idx >= len(page.comas):
                page.active_coma_index = len(page.comas) - 1
            _save_page_and_pages(work, page, work_dir)
            _sync_layer_stack_after_coma_change(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_remove failed")
            self.report({"ERROR"}, f"コマ削除失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ削除: {stem}")
        return {"FINISHED"}


class BNAME_OT_coma_duplicate(Operator):
    """選択中のコマを同ページ内で複製."""

    bl_idname = "bname.coma_duplicate"
    bl_label = "コマを複製"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return page is not None and 0 <= page.active_coma_index < len(page.comas)

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_coma_index
        src = page.comas[idx]
        work_dir = Path(work.work_dir)
        try:
            new_stem = coma_io.allocate_new_coma_id(work_dir, page.id)
            coma_io.copy_coma_files(work_dir, page.id, page.id, src.coma_id, new_stem)
            # entry を複製
            new_entry = page.comas.add()
            _copy_coma_entry(src, new_entry)
            new_entry.coma_id = new_stem
            new_entry.id = new_stem
            new_entry.title = f"{src.title} (複製)"
            new_entry.rect_x_mm = src.rect_x_mm + 5.0
            new_entry.rect_y_mm = src.rect_y_mm - 5.0
            new_entry.z_order = max((p.z_order for p in page.comas), default=0) + 1
            # 直後に配置
            new_index = len(page.comas) - 1
            if new_index != idx + 1:
                page.comas.move(new_index, idx + 1)
            page.active_coma_index = idx + 1
            if hasattr(context.scene, "bname_active_layer_kind"):
                context.scene.bname_active_layer_kind = "coma"
            coma_io.save_coma_meta(work_dir, page.id, new_entry)
            _save_page_and_pages(work, page, work_dir)
            _sync_layer_stack_after_coma_change(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_duplicate failed")
            self.report({"ERROR"}, f"コマ複製失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ複製: {src.coma_id} → {new_stem}")
        return {"FINISHED"}


def _copy_coma_entry(src, dst) -> None:
    """ComaEntry の内容を複製.

    PointerProperty (border / white_margin) と CollectionProperty
    (vertices / layer_refs) を含めてすべてコピーするため、schema 経由で
    dict を往復させる。dst.id / dst.coma_id は呼出側で上書きされる想定。
    """
    data = schema.coma_entry_to_dict(src)
    schema.coma_entry_from_dict(dst, data)


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


class BNAME_OT_coma_move_to_page(Operator):
    """選択中のコマを別のページへ移動."""

    bl_idname = "bname.coma_move_to_page"
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
            and 0 <= page.active_coma_index < len(page.comas)
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
        idx = page.active_coma_index
        src_entry = page.comas[idx]
        old_parent_key = layer_stack_utils.gp_parent_key_for_coma(page, src_entry)
        work_dir = Path(work.work_dir)
        try:
            # 移動先で衝突しないファイル名を採番
            dst_stem = coma_io.allocate_new_coma_id(work_dir, target_page.id)
            coma_io.move_coma_files(
                work_dir, page.id, target_page.id, src_entry.coma_id, dst_stem
            )
            # 移動先 collection に追加
            new_entry = target_page.comas.add()
            _copy_coma_entry(src_entry, new_entry)
            new_entry.coma_id = dst_stem
            new_entry.id = dst_stem
            new_entry.title = src_entry.title
            new_parent_key = layer_stack_utils.gp_parent_key_for_coma(target_page, new_entry)
            source_page_index = next((i for i, p in enumerate(work.pages) if p.id == page.id), -1)
            target_page_index = next((i for i, p in enumerate(work.pages) if p.id == target_page.id), -1)
            if source_page_index >= 0 and target_page_index >= 0:
                src_ox, src_oy = page_grid.page_total_offset_mm(work, context.scene, source_page_index)
                dst_ox, dst_oy = page_grid.page_total_offset_mm(work, context.scene, target_page_index)
                layer_stack_utils.translate_gp_layers_for_parent_keys(
                    context, {old_parent_key}, dst_ox - src_ox, dst_oy - src_oy
                )
                layer_stack_utils.translate_effect_layers_for_parent_keys(
                    context, {old_parent_key}, dst_ox - src_ox, dst_oy - src_oy
                )
            layer_stack_utils.reparent_gp_layers(context, old_parent_key, new_parent_key)
            layer_stack_utils.reparent_effect_layers(context, old_parent_key, new_parent_key)
            coma_io.save_coma_meta(work_dir, target_page.id, new_entry)
            # 元の collection から削除
            page.comas.remove(idx)
            if len(page.comas) == 0:
                page.active_coma_index = -1
            elif idx >= len(page.comas):
                page.active_coma_index = len(page.comas) - 1
            page_io.save_page_json(work_dir, page)
            page_io.save_page_json(work_dir, target_page)
            page.coma_count = len(page.comas)
            target_page.coma_count = len(target_page.comas)
            page_io.save_pages_json(work_dir, work)
            _sync_layer_stack_after_coma_change(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_move_to_page failed")
            self.report({"ERROR"}, f"コマ移動失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ移動: → {target_page.id}/{dst_stem}")
        return {"FINISHED"}


# ---------- Z順序 ----------


class BNAME_OT_coma_z_order(Operator):
    """選択中のコマの Z 順序を変更."""

    bl_idname = "bname.coma_z_order"
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
        return page is not None and 0 <= page.active_coma_index < len(page.comas)

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_coma_index
        current = page.comas[idx].z_order
        all_orders = [p.z_order for p in page.comas]
        mn, mx = (min(all_orders), max(all_orders)) if all_orders else (0, 0)
        if self.direction == "FRONT":
            page.comas[idx].z_order = mx + 1
        elif self.direction == "BACK":
            page.comas[idx].z_order = mn - 1
        elif self.direction == "FORWARD":
            page.comas[idx].z_order = current + 1
        elif self.direction == "BACKWARD":
            page.comas[idx].z_order = current - 1
        work_dir = Path(work.work_dir)
        try:
            coma_io.save_coma_meta(work_dir, page.id, page.comas[idx])
            page_io.save_page_json(work_dir, page)
            _sync_layer_stack_after_coma_change(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_z_order failed")
            self.report({"ERROR"}, f"Z順序変更失敗: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_coma_merge_selected(Operator):
    """複数選択中のコマ枠を 1 つの多角形コマへ結合."""

    bl_idname = "bname.coma_merge_selected"
    bl_label = "コマ結合"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        refs = object_selection.selected_coma_refs(context)
        if len(refs) < 2:
            return False
        page_ids = {str(getattr(page, "id", "") or "") for _pi, page, _idx, _panel in refs}
        return len(page_ids) == 1

    def execute(self, context):
        work = get_work(context)
        if work is None or not getattr(work, "loaded", False):
            return {"CANCELLED"}
        refs = object_selection.selected_coma_refs(context)
        if len(refs) < 2:
            self.report({"ERROR"}, "結合するコマを2つ以上選択してください")
            return {"CANCELLED"}
        page_ids = {str(getattr(page, "id", "") or "") for _pi, page, _idx, _panel in refs}
        if len(page_ids) != 1:
            self.report({"ERROR"}, "コマ結合は同じページ内のコマだけが対象です")
            return {"CANCELLED"}
        polys = [_coma_polygon(panel) for _pi, _page, _idx, panel in refs]
        merged = _merge_boundary_polygon(polys)
        if merged is None or len(merged) < 3:
            self.report({"ERROR"}, "選択コマの外周を作れません。隣接しているコマを選択してください")
            return {"CANCELLED"}
        page_index, page, survivor_index, survivor = refs[0]
        active_index = int(getattr(page, "active_coma_index", -1))
        for ref_page_index, ref_page, ref_index, ref_coma in refs:
            if ref_page == page and ref_index == active_index:
                page_index, page, survivor_index, survivor = ref_page_index, ref_page, ref_index, ref_coma
                break
        survivor_key = layer_stack_utils.gp_parent_key_for_coma(page, survivor)
        work_dir = Path(work.work_dir)
        remove_indices = sorted(
            (idx for _pi, _page, idx, _panel in refs if idx != survivor_index),
            reverse=True,
        )
        try:
            _set_coma_polygon(survivor, merged)
            survivor.edge_styles.clear()
            survivor.title = getattr(survivor, "title", "") or "結合コマ"
            survivor.z_order = max((int(getattr(panel, "z_order", 0)) for _pi, _page, _idx, panel in refs), default=survivor.z_order)
            for idx in remove_indices:
                if not (0 <= idx < len(page.comas)):
                    continue
                removed = page.comas[idx]
                old_key = layer_stack_utils.gp_parent_key_for_coma(page, removed)
                layer_stack_utils.reparent_gp_layers(context, old_key, survivor_key)
                layer_stack_utils.reparent_effect_layers(context, old_key, survivor_key)
                try:
                    coma_io.remove_coma_files(work_dir, page.id, removed.coma_id)
                except Exception:  # noqa: BLE001
                    _logger.exception("panel_merge_selected: remove panel files failed")
                page.comas.remove(idx)
                if idx < survivor_index:
                    survivor_index -= 1
            page.active_coma_index = max(0, min(survivor_index, len(page.comas) - 1))
            work.active_page_index = page_index
            page.coma_count = len(page.comas)
            for panel in page.comas:
                coma_io.save_coma_meta(work_dir, page.id, panel)
            _save_page_and_pages(work, page, work_dir)
            _sync_layer_stack_after_coma_change(context)
            edge_selection.set_selection(
                context,
                "border",
                page_index=page_index,
                coma_index=page.active_coma_index,
            )
            object_selection.set_keys(context, [object_selection.coma_key(page, page.comas[page.active_coma_index])])
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_merge_selected failed")
            self.report({"ERROR"}, f"コマ結合失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ結合: {len(refs)} 個 → 1 個")
        return {"FINISHED"}


# ---------- 分割テンプレート ----------


class BNAME_OT_coma_split_template(Operator):
    """選択中コマを縦横に均等分割して置き換える."""

    bl_idname = "bname.coma_split_template"
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
    target_coma_index: IntProperty(default=-1, options={"HIDDEN"})  # type: ignore[valid-type]
    target_from_edge_selection: BoolProperty(default=False, options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return False
        if _selected_edge_coma_target(context) is not None:
            return True
        page = get_active_page(context)
        return page is not None and 0 <= page.active_coma_index < len(page.comas)

    def invoke(self, context, event):
        target = _require_target_coma(self, context)
        if target is None:
            return {"CANCELLED"}
        work, page_index, page, coma_index, panel, from_edge = target
        if panel.shape_type not in {"rect", "polygon"}:
            self.report({"WARNING"}, "矩形または多角形コマを選択してください")
            return {"CANCELLED"}
        self.target_page_id = page.id
        self.target_coma_index = coma_index
        self.target_from_edge_selection = from_edge
        work.active_page_index = page_index
        page.active_coma_index = coma_index
        coma_modal_state.finish_all(context)
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
            target = _require_target_coma(self, context)
            if target is None:
                return {"CANCELLED"}
            work, page_index, page, self.target_coma_index, _panel, self.target_from_edge_selection = target
            self.target_page_id = page.id
        if not (0 <= self.target_coma_index < len(page.comas)):
            self.report({"ERROR"}, "分割対象のコマが見つかりません")
            return {"CANCELLED"}
        src = page.comas[self.target_coma_index]
        if src.shape_type not in {"rect", "polygon"}:
            self.report({"WARNING"}, "矩形または多角形コマを選択してください")
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        coma_modal_state.finish_all(context)
        gap_v = work.coma_gap.vertical_mm
        gap_h = work.coma_gap.horizontal_mm
        rows, cols = self.rows, self.cols
        src_title = src.title
        src_z = int(src.z_order)
        src_template = schema.coma_entry_to_dict(src)
        insert_z_base = src_z if self.clear_existing else (
            max((p.z_order for p in page.comas), default=-1) + 1
        )
        src_poly = _coma_polygon(src)
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
                coma_io.remove_coma_files(work_dir, page.id, src.coma_id)
                layer_stack_utils.delete_gp_layers_for_parent_keys(
                    context, {layer_stack_utils.gp_parent_key_for_coma(page, src)}
                )
                layer_stack_utils.delete_effect_layers_for_parent_keys(
                    context, {layer_stack_utils.gp_parent_key_for_coma(page, src)}
                )
                page.comas.remove(self.target_coma_index)
                if len(page.comas) == 0:
                    page.active_coma_index = -1
                elif self.target_coma_index >= len(page.comas):
                    page.active_coma_index = len(page.comas) - 1

            first_new_index = len(page.comas)
            # 行は上から下へ (漫画は右→左の読み順だが、ここでは配列順のみ)
            for r in range(rows):
                for c in range(cols):
                    stem = coma_io.allocate_new_coma_id(work_dir, page.id)
                    entry = page.comas.add()
                    schema.coma_entry_from_dict(entry, src_template)
                    entry.coma_id = stem
                    entry.id = stem
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
                        _set_coma_polygon(entry, split_grid[c][r])
                        entry.edge_styles.clear()
                    entry.z_order = insert_z_base + r * cols + c
                    coma_io.save_coma_meta(work_dir, page.id, entry)
            page.active_coma_index = first_new_index if len(page.comas) > first_new_index else -1
            work.active_page_index = page_index
            _save_page_and_pages(work, page, work_dir)
            _sync_layer_stack_after_coma_change(context)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_split_template failed")
            self.report({"ERROR"}, f"分割失敗: {exc}")
            return {"CANCELLED"}
        if self.target_from_edge_selection and page.active_coma_index >= 0:
            _set_edge_selection(
                context,
                kind="border",
                page_index=page_index,
                coma_index=page.active_coma_index,
            )
        self.report({"INFO"}, f"分割: {rows}×{cols} = {rows * cols} コマ")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_coma_add,
    BNAME_OT_coma_remove,
    BNAME_OT_coma_duplicate,
    BNAME_OT_coma_move_to_page,
    BNAME_OT_coma_z_order,
    BNAME_OT_coma_merge_selected,
    BNAME_OT_coma_split_template,
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
