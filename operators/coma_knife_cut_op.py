"""枠線カットツール: 任意角度の切断線でコマを分割する modal オペレータ.

CLIP STUDIO PAINT の「枠線分割ツール」相当の操作感:
- LMB ドラッグで切断線をプレビュー (赤いラバーバンド)
- リリース時、線が **横切ったすべてのコマ** を **線の角度そのまま** で分割
  (水平/垂直に丸めない、斜めもサポート)
- ドラッグ範囲が複数ページにまたがる場合、すべての該当コマを対象にする
  (アクティブページに限定しない)
- 一度切ってもツールはそのまま継続。次のドラッグで連続して切れる
- ESC / RMB / Enter: ツール終了

矩形コマは斜めカットされると多角形 (shape_type="polygon") に変換される。
多角形コマも分割可能。曲線/フリーフォームコマはスキップ。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import bpy
import gpu
from bpy.types import Operator
from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_location_3d
from gpu_extras.batch import batch_for_shader

from ..core.work import get_work
from ..io import page_io, coma_io
from . import coma_modal_state, view_event_region
from ..utils import (
    edge_selection,
    geom,
    layer_stack as layer_stack_utils,
    log,
    object_selection,
    page_grid,
    page_range,
)

_logger = log.get_logger(__name__)


COLOR_CUT_LINE = (1.0, 0.1, 0.1, 0.95)
COLOR_CUT_PREVIEW_FILL_A = (0.1, 0.65, 1.0, 0.18)
COLOR_CUT_PREVIEW_FILL_B = (1.0, 0.75, 0.1, 0.18)
COLOR_CUT_PREVIEW_OUTLINE = (0.05, 0.9, 0.95, 0.95)


def _find_view3d(context):
    area = context.area if context.area and context.area.type == "VIEW_3D" else None
    if area is None:
        screen = context.screen
        if screen is None:
            return None
        for a in screen.areas:
            if a.type == "VIEW_3D":
                area = a
                break
        else:
            return None
    region = None
    for r in area.regions:
        if r.type == "WINDOW":
            region = r
            break
    if region is None:
        return None
    space = area.spaces.active
    rv3d = getattr(space, "region_3d", None)
    if rv3d is None:
        return None
    return area, region, rv3d


def _region_to_world_mm(region, rv3d, mx, my) -> tuple[float, float] | None:
    loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
    if loc is None:
        return None
    return geom.m_to_mm(loc.x), geom.m_to_mm(loc.y)


def _world_to_region_px(region, rv3d, x_mm: float, y_mm: float) -> tuple[float, float] | None:
    pos = location_3d_to_region_2d(
        region,
        rv3d,
        (geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0),
    )
    if pos is None:
        return None
    return float(pos.x), float(pos.y)


# ---------- 凸多角形を直線で分割 ----------

def _split_no_gap(
    poly: Sequence[tuple[float, float]],
    A: tuple[float, float],
    B: tuple[float, float],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]] | None:
    """凸多角形を直線 A-B でちょうど分割 (gap なし).

    返値: (positive_side, negative_side) — それぞれ side > 0 / side < 0 の
    sub-polygon。線が多角形を切っていない or 縮退なら None。
    """
    if len(poly) < 3:
        return None
    dx = B[0] - A[0]
    dy = B[1] - A[1]
    L_sq = dx * dx + dy * dy
    if L_sq < 1e-12:
        return None

    def side(p: tuple[float, float]) -> float:
        return (p[0] - A[0]) * dy - (p[1] - A[1]) * dx

    eps = 1e-6
    sides = [side(p) for p in poly]
    n = len(poly)
    pos: list[tuple[float, float]] = []
    neg: list[tuple[float, float]] = []
    intersections = 0

    for i in range(n):
        cur = poly[i]
        nxt = poly[(i + 1) % n]
        s_cur = sides[i]
        s_nxt = sides[(i + 1) % n]
        if s_cur >= -eps:
            pos.append(cur)
        if s_cur <= eps:
            neg.append(cur)
        if (s_cur > eps and s_nxt < -eps) or (s_cur < -eps and s_nxt > eps):
            t = s_cur / (s_cur - s_nxt)
            ix = cur[0] + t * (nxt[0] - cur[0])
            iy = cur[1] + t * (nxt[1] - cur[1])
            ipt = (ix, iy)
            pos.append(ipt)
            neg.append(ipt)
            intersections += 1

    if intersections != 2:
        return None
    if len(pos) < 3 or len(neg) < 3:
        return None
    if _polygon_area(pos) < 0.01 or _polygon_area(neg) < 0.01:
        return None
    return pos, neg


def _split_convex_polygon_by_line(
    poly: Sequence[tuple[float, float]],
    A: tuple[float, float],
    B: tuple[float, float],
    gap_mm: float = 0.0,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]] | None:
    """凸多角形 ``poly`` を直線 A-B で分割 (コマ間隔 ``gap_mm`` 適用).

    ``gap_mm > 0`` のとき、cut 線を法線方向に ±gap/2 だけ平行移動した
    2 本の線で別々に poly を分割する:
      - line_pos = A-B を法線 (+nx, +ny) 方向に half_gap 平行移動
      - line_neg = A-B を法線 (-nx, -ny) 方向に half_gap 平行移動
    返値の positive sub は ``poly`` のうち line_pos より法線正側の部分。
    返値の negative sub は ``poly`` のうち line_neg より法線負側の部分。
    両者の間には gap_mm の隙間が空き、かつ各 sub-polygon の頂点はすべて
    元の poly 境界の **内側** に収まる (= 元の panel 辺の角度を変えない)。

    交点不足 / 縮退 / 一方が消失する場合は None。
    """
    if len(poly) < 3:
        return None
    half_gap = max(0.0, float(gap_mm)) * 0.5
    if half_gap <= 0.0:
        return _split_no_gap(poly, A, B)

    dx = B[0] - A[0]
    dy = B[1] - A[1]
    L_sq = dx * dx + dy * dy
    if L_sq < 1e-12:
        return None
    L = L_sq ** 0.5
    nx = dy / L  # 右手側法線 (side > 0 と同じ向き)
    ny = -dx / L

    A_pos = (A[0] + nx * half_gap, A[1] + ny * half_gap)
    B_pos = (B[0] + nx * half_gap, B[1] + ny * half_gap)
    A_neg = (A[0] - nx * half_gap, A[1] - ny * half_gap)
    B_neg = (B[0] - nx * half_gap, B[1] - ny * half_gap)

    pos_split = _split_no_gap(poly, A_pos, B_pos)
    neg_split = _split_no_gap(poly, A_neg, B_neg)
    if pos_split is None or neg_split is None:
        return None
    # pos_split[0] は line_pos より法線正側 (= 元 cut 線より +half_gap 法線正側)
    # neg_split[1] は line_neg より法線負側 (= 元 cut 線より +half_gap 法線負側)
    return pos_split[0], neg_split[1]


def _polygon_area(poly: Sequence[tuple[float, float]]) -> float:
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


# ---------- ページ単位の panel cut ----------


def _coma_polygon(panel) -> list[tuple[float, float]]:
    """panel エントリから多角形頂点リストを返す (mm、CCW)."""
    if panel.shape_type == "rect":
        x, y = panel.rect_x_mm, panel.rect_y_mm
        w, h = panel.rect_width_mm, panel.rect_height_mm
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if panel.shape_type == "polygon":
        return [(v.x_mm, v.y_mm) for v in panel.vertices]
    return []


def _set_coma_polygon(panel, poly: Sequence[tuple[float, float]]) -> None:
    """panel エントリの形状を多角形 (vertices) に書き換える."""
    panel.shape_type = "polygon"
    panel.vertices.clear()
    for x, y in poly:
        v = panel.vertices.add()
        v.x_mm = float(x)
        v.y_mm = float(y)
    # rect_* は無効化 (外接矩形を入れておくと coma_to_rect で復元しやすい)
    if poly:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        panel.rect_x_mm = min(xs)
        panel.rect_y_mm = min(ys)
        panel.rect_width_mm = max(xs) - min(xs)
        panel.rect_height_mm = max(ys) - min(ys)


def _point_in_polygon(p: tuple[float, float], poly: Sequence[tuple[float, float]]) -> bool:
    """ray casting で点 p が多角形 poly の内側にあるかを判定."""
    x, y = p
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)):
            x_cross = (xj - xi) * (y - yi) / (yj - yi + 1e-30) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _effective_gap_mm(
    work, A: tuple[float, float], B: tuple[float, float], panel
) -> float:
    """カット線が触れるコマ辺の組み合わせに応じた gap (mm) を返す.

    panel の外接矩形に対し、カット線 (無限延長) がどの辺と交わるかで判定:
      - 上辺/下辺 (水平辺) のみと交わる → 縦カット → 左右スキマ (gap_h)
      - 左辺/右辺 (垂直辺) のみと交わる → 横カット → 上下スキマ (gap_v)
      - 混在 (上+右、下+左 など) → 左右スキマ (gap_h)

    panel 個別の coma_gap_*_mm (>= 0) が優先、負値なら work.coma_gap を継承。
    """
    pgv = float(getattr(panel, "coma_gap_vertical_mm", -1.0))
    pgh = float(getattr(panel, "coma_gap_horizontal_mm", -1.0))
    gap_v = pgv if pgv >= 0.0 else float(work.coma_gap.vertical_mm)
    gap_h = pgh if pgh >= 0.0 else float(work.coma_gap.horizontal_mm)

    # panel の外接矩形を取得
    if panel.shape_type == "rect":
        x0 = panel.rect_x_mm
        y0 = panel.rect_y_mm
        x1 = x0 + panel.rect_width_mm
        y1 = y0 + panel.rect_height_mm
    elif panel.shape_type == "polygon" and len(panel.vertices) >= 3:
        xs = [v.x_mm for v in panel.vertices]
        ys = [v.y_mm for v in panel.vertices]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
    else:
        return gap_h

    eps = 0.1  # mm
    dx = B[0] - A[0]
    dy = B[1] - A[1]

    def _h_intersect(y_const: float) -> bool:
        """A-B 直線と水平線 y=y_const の交点が [x0, x1] 範囲内にあるか."""
        if abs(dy) < 1e-9:
            return False
        t = (y_const - A[1]) / dy
        x = A[0] + t * dx
        return (x0 - eps) <= x <= (x1 + eps)

    def _v_intersect(x_const: float) -> bool:
        """A-B 直線と垂直線 x=x_const の交点が [y0, y1] 範囲内にあるか."""
        if abs(dx) < 1e-9:
            return False
        t = (x_const - A[0]) / dx
        y = A[1] + t * dy
        return (y0 - eps) <= y <= (y1 + eps)

    touches_horiz = _h_intersect(y0) or _h_intersect(y1)  # 上辺 or 下辺
    touches_vert = _v_intersect(x0) or _v_intersect(x1)   # 左辺 or 右辺

    if touches_horiz and not touches_vert:
        # 上下辺のみ → 縦カット → 左右スキマ
        return gap_h
    if touches_vert and not touches_horiz:
        # 左右辺のみ → 横カット → 上下スキマ
        return gap_v
    # 混在 (上+右、下+左 など) → 左右スキマ (ユーザー指定)
    return gap_h


def _apply_cut_to_coma(
    work, page, coma_idx: int, work_dir: Path,
    A_local: tuple[float, float], B_local: tuple[float, float],
) -> bool:
    """1 つのコマだけを cut line A-B で分割.

    コマ間隔は work.coma_gap (もしくは panel 個別オーバーライド) を
    カット線の角度に応じて補間して適用する。
    戻り値: 分割が発生したか。
    """
    from .coma_op import _copy_coma_entry

    if not (0 <= coma_idx < len(page.comas)):
        return False
    panel = page.comas[coma_idx]
    poly = _coma_polygon(panel)
    if not poly:
        return False
    gap_mm = _effective_gap_mm(work, A_local, B_local, panel)
    result = _split_convex_polygon_by_line(poly, A_local, B_local, gap_mm=gap_mm)
    if result is None:
        return False
    left_poly, right_poly = result

    # 元コマを左側に書き換え
    _set_coma_polygon(panel, left_poly)
    # カットで edge_index が変わるため edge_styles 個別オーバーライドはクリア
    panel.edge_styles.clear()
    # 新規コマ (右側) を追加
    new_stem = coma_io.allocate_new_coma_id(work_dir, page.id)
    try:
        coma_io.copy_coma_files(
            work_dir, page.id, page.id, panel.coma_id, new_stem
        )
    except Exception:  # noqa: BLE001
        _logger.warning("knife_cut: copy_coma_files failed for %s", panel.coma_id)
    new_entry = page.comas.add()
    _copy_coma_entry(panel, new_entry)
    new_entry.coma_id = new_stem
    new_entry.id = new_stem
    new_entry.title = f"{panel.title} (分割)"
    _set_coma_polygon(new_entry, right_poly)
    new_entry.edge_styles.clear()  # 元コマから複製された個別設定もクリア
    z_max = max((p.z_order for p in page.comas), default=0)
    new_entry.z_order = z_max + 1
    try:
        coma_io.save_coma_meta(work_dir, page.id, panel)
        coma_io.save_coma_meta(work_dir, page.id, new_entry)
    except Exception:  # noqa: BLE001
        _logger.exception("knife_cut: save_coma_meta failed")

    page.coma_count = len(page.comas)
    try:
        page_io.save_page_json(work_dir, page)
    except Exception:  # noqa: BLE001
        _logger.exception("knife_cut: save_page_json failed")
    return True


def _sync_layer_stack_after_cut(context) -> None:
    try:
        layer_stack_utils.sync_layer_stack_after_data_change(
            context,
            align_coma_order=True,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("knife_cut: layer stack sync failed")


def _page_world_offset_mm(work, page_index: int, scene=None) -> tuple[float, float]:
    scene = scene or bpy.context.scene
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    cw = work.paper.canvas_width_mm
    ch = work.paper.canvas_height_mm
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    ox, oy = page_grid.page_grid_offset_mm(
        page_index, cols, gap, cw, ch, start_side, read_direction
    )
    page = work.pages[page_index]
    add_x, add_y = page_grid.page_manual_offset_mm(page)
    return ox + add_x, oy + add_y


def _find_coma_at_world(
    work, x_mm: float, y_mm: float,
) -> tuple[int, int] | None:
    """world (mm) 座標下のコマを (page_index, coma_index) で返す.

    全ページの grid offset を考慮して走査。同位置に複数コマあれば Z 順最大。
    """
    scene = bpy.context.scene
    if scene is None:
        return None
    cw = work.paper.canvas_width_mm
    ch = work.paper.canvas_height_mm
    for i, page in enumerate(work.pages):
        if not page_range.page_in_range(page):
            continue
        ox, oy = _page_world_offset_mm(work, i, scene)
        local_x = x_mm - ox
        local_y = y_mm - oy
        if not (0.0 <= local_x <= cw and 0.0 <= local_y <= ch):
            continue
        # Z 順最大を優先
        sorted_comas = sorted(
            range(len(page.comas)),
            key=lambda j: -page.comas[j].z_order,
        )
        for coma_idx in sorted_comas:
            poly = _coma_polygon(page.comas[coma_idx])
            if not poly:
                continue
            if _point_in_polygon((local_x, local_y), poly):
                return (i, coma_idx)
    return None


# ---------- modal operator ----------


class BNAME_OT_coma_knife_cut(Operator):
    """枠線カットツール (CSP 互換): 任意角度の切断線で複数コマを連続分割する."""

    bl_idname = "bname.coma_knife_cut"
    bl_label = "枠線カットツール"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded

    def invoke(self, context, event):
        target = _find_view3d(context)
        if target is None:
            return {"PASS_THROUGH"}
        if coma_modal_state.get_active("knife_cut") is not None:
            return {"FINISHED"}
        coma_modal_state.finish_active("coma_vertex_edit", context, keep_selection=True)
        coma_modal_state.finish_active("edge_move", context, keep_selection=False)
        coma_modal_state.finish_active("layer_move", context, keep_selection=False)
        coma_modal_state.finish_active("balloon_tool", context, keep_selection=True)
        coma_modal_state.finish_active("text_tool", context, keep_selection=True)
        coma_modal_state.finish_active("effect_line_tool", context, keep_selection=True)
        self._area, self._region, self._rv3d = target
        self._work = get_work(context)
        if self._work is None or not self._work.loaded:
            self.report({"WARNING"}, "作品を開いてください")
            return {"CANCELLED"}

        # ドラッグ状態
        self._p1_px: tuple[float, float] | None = None
        self._p2_px: tuple[float, float] | None = None
        self._dragging = False
        self._cut_count_total = 0
        self._externally_finished = False
        self._navigation_drag_passthrough = False
        self._cursor_modal_set = False
        self._edge_drag = None
        self._edge_drag_moved = False

        # POST_PIXEL でラバーバンドを描画
        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (self,), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "CROSSHAIR")
        coma_modal_state.set_active("knife_cut", self, context)
        self._tag_redraw()
        self.report(
            {"INFO"},
            "LMB ドラッグで枠線をカット | ESC / RMB / Enter で終了",
        )
        return {"RUNNING_MODAL"}

    def _to_window(self, ev):
        return ev.mouse_x - self._region.x, ev.mouse_y - self._region.y

    def _region_at_mouse(self, ev):
        for region in self._area.regions:
            if (
                region.x <= ev.mouse_x < region.x + region.width
                and region.y <= ev.mouse_y < region.y + region.height
            ):
                return region
        return None

    def _snap_p2(
        self, p2: tuple[float, float], shift: bool,
    ) -> tuple[float, float]:
        """Shift 押下時、p1→p2 を画面上の水平/垂直に拘束する.

        |Δx| >= |Δy| なら水平 (Y を p1.y に固定)、それ以外は垂直 (X を p1.x に固定)。
        Shift が離されている場合はそのまま返す。
        """
        if not shift or self._p1_px is None:
            return p2
        x1, y1 = self._p1_px
        x2, y2 = p2
        dx = x2 - x1
        dy = y2 - y1
        if abs(dx) >= abs(dy):
            return (x2, y1)  # 水平にスナップ
        return (x1, y2)  # 垂直にスナップ

    def _is_inside_region(self, ev) -> bool:
        mouse_x = int(getattr(ev, "mouse_x", -10_000_000))
        mouse_y = int(getattr(ev, "mouse_y", -10_000_000))
        for region in self._area.regions:
            if region.type == "WINDOW":
                continue
            if (
                region.x <= mouse_x < region.x + region.width
                and region.y <= mouse_y < region.y + region.height
            ):
                return False
        region = self._region
        return (
            region.x <= mouse_x < region.x + region.width
            and region.y <= mouse_y < region.y + region.height
        )

    def _is_over_navigation_gizmo(self, ev) -> bool:
        return view_event_region.is_view3d_navigation_ui_event(bpy.context, ev)

    def _tag_redraw(self) -> None:
        if self._region is not None:
            self._region.tag_redraw()

    def _cleanup(self, context=None) -> None:
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        h = getattr(self, "_draw_handler", None)
        if h is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
            except Exception:  # noqa: BLE001
                pass
            self._draw_handler = None
        edge_drag = getattr(self, "_edge_drag", None)
        if edge_drag is not None:
            edge_drag.cancel()
        self._edge_drag = None
        self._edge_drag_moved = False
        self._tag_redraw()

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        _ = keep_selection
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        self._cleanup(context)
        coma_modal_state.clear_active("knife_cut", self, context)

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            coma_modal_state.clear_active("knife_cut", self, context)
            return {"FINISHED", "PASS_THROUGH"}
        if getattr(self, "_edge_drag", None) is not None:
            return self._modal_edge_drag(context, event)
        if getattr(self, "_navigation_drag_passthrough", False):
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                self._navigation_drag_passthrough = False
            return {"PASS_THROUGH"}
        # Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y は modal 保持中の PropertyGroup 参照を
        # stale 化させて C レベル crash を起こすため、検知したら即終了して譲る。
        if event.value == "PRESS" and event.type in {"Z", "Y"} and event.ctrl:
            self.finish_from_external(context, keep_selection=False)
            return {"FINISHED", "PASS_THROUGH"}

        if (
            event.value == "PRESS"
            and event.type == "G"
            and not event.ctrl
            and not event.alt
            and not event.shift
        ):
            if not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            self.finish_from_external(context, keep_selection=False)
            try:
                with context.temp_override(area=self._area, region=self._region):
                    bpy.ops.bname.coma_edge_move("INVOKE_DEFAULT")
            except Exception:  # noqa: BLE001
                _logger.exception("knife_cut: failed to switch to edge_move")
            return {"FINISHED"}

        if (
            event.value == "PRESS"
            and event.type == "F"
            and not event.ctrl
            and not event.alt
            and not event.shift
        ):
            if not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            return {"RUNNING_MODAL"}

        if (
            event.value == "PRESS"
            and event.type == "K"
            and not event.ctrl
            and not event.alt
            and not event.shift
        ):
            if not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            self.finish_from_external(context, keep_selection=False)
            try:
                with context.temp_override(area=self._area, region=self._region):
                    bpy.ops.bname.layer_move_tool("INVOKE_DEFAULT")
            except Exception:  # noqa: BLE001
                _logger.exception("knife_cut: failed to switch to layer_move")
            return {"FINISHED"}

        # B-Name のモード切替ショートカットが押されたら modal を終了して譲る。
        if (
            event.value == "PRESS"
            and event.type in {"O", "P", "COMMA", "PERIOD", "Z", "X"}
            and not event.ctrl
            and not event.alt
        ):
            if not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            self.finish_from_external(context, keep_selection=False)
            return {"FINISHED", "PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            if not self._dragging and self._is_over_navigation_gizmo(event):
                return {"PASS_THROUGH"}
            if not self._dragging and not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            if self._dragging:
                self._p2_px = self._snap_p2(self._to_window(event), event.shift)
                self._tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                if self._is_over_navigation_gizmo(event):
                    self._navigation_drag_passthrough = True
                    return {"PASS_THROUGH"}
                if not self._is_inside_region(event):
                    return {"PASS_THROUGH"}
                if self._try_start_edge_drag(context, event):
                    self._tag_redraw()
                    return {"RUNNING_MODAL"}
                self._p1_px = self._to_window(event)
                self._p2_px = self._p1_px
                self._dragging = True
                self._tag_redraw()
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE":
                if not self._dragging and not self._is_inside_region(event):
                    return {"PASS_THROUGH"}
                if self._dragging and self._p1_px is not None and self._p2_px is not None:
                    # リリース直前の Shift 状態でも軸ロックを反映
                    self._p2_px = self._snap_p2(self._p2_px, event.shift)
                    self._apply_cut_world()
                    # ツールは継続 (FINISHED ではなく RUNNING_MODAL を返す)
                    self._p1_px = None
                    self._p2_px = None
                    self._dragging = False
                    self._tag_redraw()
                return {"RUNNING_MODAL"}

        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            if not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            self.finish_from_external(context, keep_selection=False)
            if self._cut_count_total > 0:
                self.report(
                    {"INFO"},
                    f"枠線カットツール終了 (合計 {self._cut_count_total} コマ分割)",
                )
            else:
                self.report({"INFO"}, "枠線カットツール終了")
            return {"FINISHED"}

        if event.type in {"ESC", "RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            if not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            self.finish_from_external(context, keep_selection=False)
            if self._cut_count_total > 0:
                self.report(
                    {"INFO"},
                    f"枠線カットツール終了 (合計 {self._cut_count_total} コマ分割)",
                )
            else:
                self.report({"INFO"}, "枠線カットツール終了")
            return {"FINISHED"}

        # 中ボタン (パン) などはビューポート操作にパススルー
        return {"PASS_THROUGH"}

    def _modal_edge_drag(self, context, event):
        if event.type == "MOUSEMOVE":
            if self._edge_drag.apply(event):
                self._edge_drag_moved = True
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            changed = self._edge_drag.finish("B-Name: 枠線移動")
            moved = bool(getattr(self, "_edge_drag_moved", False))
            self._edge_drag = None
            self._edge_drag_moved = False
            if not changed and not moved:
                try:
                    from ..utils import detail_popup

                    detail_popup.open_active_detail_deferred(context)
                except Exception:  # noqa: BLE001
                    pass
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._edge_drag.cancel()
            self._edge_drag = None
            self._edge_drag_moved = False
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def _try_start_edge_drag(self, context, event) -> bool:
        from . import coma_edge_drag_session, coma_edge_move_op

        mx, my = self._to_window(event)
        if coma_edge_move_op.extend_selected_handle_at_event(context, event):
            return True
        hit = coma_edge_move_op._pick_edge_or_vertex(
            self._work,
            self._region,
            self._rv3d,
            int(mx),
            int(my),
        )
        if hit is None:
            return False
        page_index = int(hit["page"])
        coma_index = int(hit["coma"])
        if not (0 <= page_index < len(self._work.pages)):
            return False
        page = self._work.pages[page_index]
        if not (0 <= coma_index < len(page.comas)):
            return False
        self._work.active_page_index = page_index
        page.active_coma_index = coma_index
        panel = page.comas[coma_index]
        mode = "toggle" if event.ctrl else "add" if event.shift else "single"
        object_selection.select_key(context, object_selection.coma_key(page, panel), mode=mode)
        if hit.get("type") == "vertex":
            edge_selection.set_selection(
                context,
                "vertex",
                page_index=page_index,
                coma_index=coma_index,
                vertex_index=int(hit.get("vertex", -1)),
            )
        else:
            edge_selection.set_selection(
                context,
                "edge",
                page_index=page_index,
                coma_index=coma_index,
                edge_index=int(hit.get("edge", -1)),
            )
        if event.ctrl or event.shift:
            return True
        start_world = _region_to_world_mm(self._region, self._rv3d, mx, my)
        selection = {
            "type": "vertex" if hit.get("type") == "vertex" else "edge",
            "page": page_index,
            "coma": coma_index,
        }
        if selection["type"] == "vertex":
            selection["vertex"] = int(hit.get("vertex", -1))
        else:
            selection["edge"] = int(hit.get("edge", -1))
        self._edge_drag = coma_edge_drag_session.ComaEdgeDragSession(
            context,
            self._work,
            self._area,
            self._region,
            self._rv3d,
            selection,
            start_world,
        )
        self._edge_drag_moved = False
        return True

    def _apply_cut_world(self) -> None:
        """world mm 座標の切断線を、ドラッグ開始位置のコマ 1 つだけに適用."""
        region = self._region
        rv3d = self._rv3d
        p1 = _region_to_world_mm(region, rv3d, *self._p1_px)
        p2 = _region_to_world_mm(region, rv3d, *self._p2_px)
        if p1 is None or p2 is None:
            return
        (xa, ya), (xb, yb) = p1, p2
        if (xa - xb) ** 2 + (ya - yb) ** 2 < 0.25:  # 0.5mm 未満は無視
            return

        work = self._work
        if work is None or work.work_dir == "":
            return
        work_dir = Path(work.work_dir)

        # ドラッグ開始位置 (P1) のコマを 1 つだけ対象にする
        hit = _find_coma_at_world(work, xa, ya)
        if hit is None:
            self.report({"INFO"}, "開始位置にコマがありません")
            return
        page_idx, coma_idx = hit
        page = work.pages[page_idx]

        # 対象ページの grid offset を引いてページローカル座標に
        ox, oy = _page_world_offset_mm(work, page_idx)
        A_local = (xa - ox, ya - oy)
        B_local = (xb - ox, yb - oy)

        ok = _apply_cut_to_coma(work, page, coma_idx, work_dir, A_local, B_local)
        if ok:
            try:
                page_io.save_pages_json(work_dir, work)
            except Exception:  # noqa: BLE001
                _logger.exception("knife_cut: save_pages_json failed")
            _sync_layer_stack_after_cut(bpy.context)
            self._cut_count_total += 1
            # 1 回のカットを独立した undo step として記録
            # (modal 中のすべてのカットを 1 ステップにまとめず個別に undo/redo 可能に)
            try:
                bpy.ops.ed.undo_push(message="B-Name: 枠線カット")
            except Exception:  # noqa: BLE001
                _logger.exception("knife_cut: undo_push failed")
            self.report({"INFO"}, "コマを分割 (続けてカットできます)")
        else:
            self.report({"INFO"}, "切断線がコマを横切っていません")


# ---------- POST_PIXEL プレビュー ----------


def _preview_cut_polygons(
    op: "BNAME_OT_coma_knife_cut",
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]] | None:
    work = getattr(op, "_work", None)
    if work is None or op._p1_px is None or op._p2_px is None:
        return None
    p1 = _region_to_world_mm(op._region, op._rv3d, *op._p1_px)
    p2 = _region_to_world_mm(op._region, op._rv3d, *op._p2_px)
    if p1 is None or p2 is None:
        return None
    (xa, ya), (xb, yb) = p1, p2
    if (xa - xb) ** 2 + (ya - yb) ** 2 < 0.25:
        return None
    hit = _find_coma_at_world(work, xa, ya)
    if hit is None:
        return None
    page_idx, coma_idx = hit
    if not (0 <= page_idx < len(work.pages)):
        return None
    page = work.pages[page_idx]
    if not (0 <= coma_idx < len(page.comas)):
        return None
    ox, oy = _page_world_offset_mm(work, page_idx)
    a_local = (xa - ox, ya - oy)
    b_local = (xb - ox, yb - oy)
    panel = page.comas[coma_idx]
    poly = _coma_polygon(panel)
    if not poly:
        return None
    result = _split_convex_polygon_by_line(
        poly,
        a_local,
        b_local,
        gap_mm=_effective_gap_mm(work, a_local, b_local, panel),
    )
    if result is None:
        return None
    left_poly, right_poly = result
    left_world = [(x + ox, y + oy) for x, y in left_poly]
    right_world = [(x + ox, y + oy) for x, y in right_poly]
    return left_world, right_world


def _poly_region_points(region, rv3d, poly: Sequence[tuple[float, float]]) -> list[tuple[float, float]] | None:
    points: list[tuple[float, float]] = []
    for x_mm, y_mm in poly:
        point = _world_to_region_px(region, rv3d, x_mm, y_mm)
        if point is None:
            return None
        points.append(point)
    return points


def _draw_preview_fill(shader, points: list[tuple[float, float]], color) -> None:
    if len(points) < 3:
        return
    verts: list[tuple[float, float]] = []
    root = points[0]
    for index in range(1, len(points) - 1):
        verts.extend((root, points[index], points[index + 1]))
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": verts}).draw(shader)


def _draw_preview_outline(shader, points: list[tuple[float, float]]) -> None:
    if len(points) < 2:
        return
    verts: list[tuple[float, float]] = []
    for index, point in enumerate(points):
        verts.append(point)
        verts.append(points[(index + 1) % len(points)])
    shader.uniform_float("color", COLOR_CUT_PREVIEW_OUTLINE)
    batch_for_shader(shader, "LINES", {"pos": verts}).draw(shader)


def _draw_callback(op: "BNAME_OT_coma_knife_cut") -> None:
    if not op._dragging or op._p1_px is None or op._p2_px is None:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
    preview = _preview_cut_polygons(op)
    if preview is not None:
        left_world, right_world = preview
        left_points = _poly_region_points(op._region, op._rv3d, left_world)
        right_points = _poly_region_points(op._region, op._rv3d, right_world)
        if left_points is not None and right_points is not None:
            try:
                gpu.state.blend_set("ALPHA")
            except Exception:  # noqa: BLE001
                pass
            try:
                _draw_preview_fill(shader, left_points, COLOR_CUT_PREVIEW_FILL_A)
                _draw_preview_fill(shader, right_points, COLOR_CUT_PREVIEW_FILL_B)
                try:
                    gpu.state.line_width_set(2.0)
                except Exception:  # noqa: BLE001
                    pass
                _draw_preview_outline(shader, left_points)
                _draw_preview_outline(shader, right_points)
            finally:
                try:
                    gpu.state.line_width_set(1.0)
                    gpu.state.blend_set("NONE")
                except Exception:  # noqa: BLE001
                    pass
            return
    try:
        gpu.state.line_width_set(3.0)
    except Exception:  # noqa: BLE001
        pass
    verts = [op._p1_px, op._p2_px]
    batch = batch_for_shader(shader, "LINES", {"pos": verts})
    shader.uniform_float("color", COLOR_CUT_LINE)
    batch.draw(shader)
    try:
        gpu.state.line_width_set(1.0)
    except Exception:  # noqa: BLE001
        pass


_CLASSES = (BNAME_OT_coma_knife_cut,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
