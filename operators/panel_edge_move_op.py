"""枠線選択ツール: 枠線の辺/頂点を選択 → ドラッグ移動 + 個別スタイル編集.

CLIP STUDIO PAINT の「枠線分割ツール (移動モード)」相当の操作感:
- LMB シングルクリック: クリック地点の最寄りの **辺** を選択 (ページに依存しない)
- LMB ダブルクリック: その辺を含む **枠線全体 (panel)** を選択
- ドラッグ: 選択した辺/頂点を移動 (隣接 panel と連動して gap を維持)
- 辺の中点に **三角ハンドル 2 つ** を表示 → クリックで隣接枠線/基本枠/裁ち落とし枠まで拡張
- 選択中の辺/枠線の **色・線幅** を N パネルから編集可能
  (辺選択 → 個別 edge_style 上書き、枠線選択 → panel.border 全体)
- ESC / RMB / Enter: ツール終了
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Optional

import bpy
import gpu
from bpy.types import Operator
from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_location_3d
from gpu_extras.batch import batch_for_shader

from ..core.work import get_work
from ..io import page_io, panel_io
from ..utils import geom, log, page_grid

_logger = log.get_logger(__name__)


# ---- 定数 ----
EDGE_PICK_TOLERANCE_PX = 12.0  # 辺をクリックしたとみなす距離 (px)
VERTEX_PICK_TOLERANCE_PX = 14.0  # 頂点をクリックしたとみなす距離 (px)
HANDLE_SIZE_PX = 14.0  # 三角ハンドルの一辺 (px)
HANDLE_OFFSET_PX = 22.0  # 辺中点からハンドル中心までの距離 (px)
ADJACENCY_GAP_TOLERANCE_MM = 0.2  # 隣接判定: 対応辺との垂直距離が gap ± この値以内
ADJACENCY_OVERLAP_RATIO = 0.2  # 隣接判定: 重なり比率がこの値以上で連動
DOUBLE_CLICK_INTERVAL = 0.4  # シングル/ダブル判定の閾値 (秒)

COLOR_SELECTED_EDGE = (1.0, 0.5, 0.0, 1.0)  # 橙
COLOR_SELECTED_BORDER = (1.0, 0.3, 0.0, 1.0)  # 濃橙 (枠線全体選択)
COLOR_SELECTED_VERTEX = (1.0, 0.5, 0.0, 1.0)
COLOR_HANDLE = (1.0, 0.85, 0.0, 1.0)  # 黄


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


def _world_mm_to_region(region, rv3d, x_mm, y_mm) -> tuple[float, float] | None:
    p = location_3d_to_region_2d(
        region, rv3d, (geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0)
    )
    if p is None:
        return None
    return float(p.x), float(p.y)


def _panel_polygon(panel) -> list[tuple[float, float]]:
    if panel.shape_type == "rect":
        x, y = panel.rect_x_mm, panel.rect_y_mm
        w, h = panel.rect_width_mm, panel.rect_height_mm
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if panel.shape_type == "polygon":
        return [(v.x_mm, v.y_mm) for v in panel.vertices]
    return []


def _set_panel_polygon(panel, poly: list[tuple[float, float]]) -> None:
    panel.shape_type = "polygon"
    panel.vertices.clear()
    for x, y in poly:
        v = panel.vertices.add()
        v.x_mm = float(x)
        v.y_mm = float(y)
    if poly:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        panel.rect_x_mm = min(xs)
        panel.rect_y_mm = min(ys)
        panel.rect_width_mm = max(xs) - min(xs)
        panel.rect_height_mm = max(ys) - min(ys)


def _distance_point_to_segment(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> tuple[float, float]:
    """点 p から線分 a-b への距離と、線分上の最近点パラメータ t を返す."""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    L_sq = dx * dx + dy * dy
    if L_sq < 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1]), 0.0
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L_sq))
    cx = a[0] + t * dx
    cy = a[1] + t * dy
    return math.hypot(p[0] - cx, p[1] - cy), t


def _line_intersect(
    p1: tuple[float, float], p2: tuple[float, float],
    p3: tuple[float, float], p4: tuple[float, float],
    fallback: tuple[float, float],
) -> tuple[float, float]:
    """直線 p1-p2 と直線 p3-p4 の交点を返す (ほぼ平行なら fallback)."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return fallback
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


# ---------- 隣接 panel との連動 ----------


def _all_panel_edges_world(work) -> list[tuple[int, int, int, tuple[float, float], tuple[float, float]]]:
    """全ページの全 panel の全 edge を world (mm) 座標で返す.

    返値: [(page_idx, panel_idx, edge_idx, (x1,y1), (x2,y2)), ...]
    """
    scene = bpy.context.scene
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    cw = work.paper.canvas_width_mm
    ch = work.paper.canvas_height_mm
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")

    out: list = []
    for pi, page in enumerate(work.pages):
        ox, oy = page_grid.page_grid_offset_mm(
            pi, cols, gap, cw, ch, start_side, read_direction
        )
        for panel_i, panel in enumerate(page.panels):
            poly = _panel_polygon(panel)
            if len(poly) < 2:
                continue
            for ei in range(len(poly)):
                a = (poly[ei][0] + ox, poly[ei][1] + oy)
                b = (poly[(ei + 1) % len(poly)][0] + ox, poly[(ei + 1) % len(poly)][1] + oy)
                out.append((pi, panel_i, ei, a, b))
    return out


def _page_offset(work, page_idx: int) -> tuple[float, float]:
    scene = bpy.context.scene
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    cw = work.paper.canvas_width_mm
    ch = work.paper.canvas_height_mm
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    return page_grid.page_grid_offset_mm(
        page_idx, cols, gap, cw, ch, start_side, read_direction
    )


def _gap_for_edge(work, panel, edge: tuple[tuple[float, float], tuple[float, float]]) -> float:
    """edge の方向に応じた gap (mm) を返す (knife_cut の _effective_gap_mm と同じ規則)."""
    a, b = edge
    pgv = float(getattr(panel, "panel_gap_vertical_mm", -1.0))
    pgh = float(getattr(panel, "panel_gap_horizontal_mm", -1.0))
    gap_v = pgv if pgv >= 0.0 else float(work.panel_gap.vertical_mm)
    gap_h = pgh if pgh >= 0.0 else float(work.panel_gap.horizontal_mm)
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    # 辺が水平に近ければ → 上下スキマ (gap_v)、垂直に近ければ → 左右スキマ (gap_h)
    if abs(dx) >= abs(dy):
        return gap_v
    return gap_h


def _find_adjacent_edges(
    work, page_idx: int, panel_idx: int, edge_idx: int,
) -> list[tuple[int, int, int]]:
    """対象 edge と隣接 (= ほぼ平行で gap 距離内、重なる) する全 edge を返す.

    返値: [(page_idx, panel_idx, edge_idx), ...] (自分自身は含まない)
    """
    page = work.pages[page_idx]
    panel = page.panels[panel_idx]
    poly = _panel_polygon(panel)
    if len(poly) < 2:
        return []
    pox, poy = _page_offset(work, page_idx)
    a = (poly[edge_idx][0] + pox, poly[edge_idx][1] + poy)
    b = (poly[(edge_idx + 1) % len(poly)][0] + pox, poly[(edge_idx + 1) % len(poly)][1] + poy)
    edge_len = math.hypot(b[0] - a[0], b[1] - a[1])
    if edge_len < 1e-6:
        return []
    ux = (b[0] - a[0]) / edge_len
    uy = (b[1] - a[1]) / edge_len
    nx = -uy
    ny = ux
    target_gap = _gap_for_edge(work, panel, (a, b))

    adj: list[tuple[int, int, int]] = []
    for entry in _all_panel_edges_world(work):
        pi2, panel_i2, ei2, a2, b2 = entry
        # 同じ panel 内の他 edge は除外 (細い panel の対辺が偶然 gap 距離だと
        # 連動して panel が反転するバグを防ぐ)
        if (pi2, panel_i2) == (page_idx, panel_idx):
            continue
        # 平行性: 単位ベクトルの内積が ±1 に近い
        l2 = math.hypot(b2[0] - a2[0], b2[1] - a2[1])
        if l2 < 1e-6:
            continue
        ux2 = (b2[0] - a2[0]) / l2
        uy2 = (b2[1] - a2[1]) / l2
        dot = ux * ux2 + uy * uy2
        if abs(abs(dot) - 1.0) > 0.05:  # 約 ±18° 以上の角度差は不適
            continue
        # 法線距離: a2 から自分の line への符号付き距離
        d = (a2[0] - a[0]) * nx + (a2[1] - a[1]) * ny
        if abs(abs(d) - target_gap) > ADJACENCY_GAP_TOLERANCE_MM:
            continue
        # 重なり: a2, b2 を自分の line 接線上に投影し、[0, edge_len] と交わる比率
        t1 = ((a2[0] - a[0]) * ux + (a2[1] - a[1]) * uy)
        t2 = ((b2[0] - a[0]) * ux + (b2[1] - a[1]) * uy)
        lo = max(0.0, min(t1, t2))
        hi = min(edge_len, max(t1, t2))
        overlap = max(0.0, hi - lo)
        if overlap < ADJACENCY_OVERLAP_RATIO * edge_len:
            continue
        adj.append((pi2, panel_i2, ei2))
    return adj


# ---------- ピック ----------


def _pick_edge_or_vertex(
    work, region, rv3d, mx: int, my: int,
) -> Optional[dict]:
    """画面 (mx, my) 直下の最寄り辺 or 頂点を返す.

    返値: {"type": "edge" or "vertex",
           "page": pi, "panel": panel_i,
           "edge": ei (edge type only),
           "vertex": vi (vertex type only)}
    """
    best: Optional[dict] = None
    best_dist = float("inf")

    # 頂点を優先 (辺より priority 高く判定)
    for entry in _all_panel_edges_world(work):
        pi, panel_i, ei, a, b = entry
        # 各 edge の始点を vertex として
        ap = _world_mm_to_region(region, rv3d, a[0], a[1])
        if ap is None:
            continue
        d = math.hypot(ap[0] - mx, ap[1] - my)
        if d < VERTEX_PICK_TOLERANCE_PX and d < best_dist:
            best = {
                "type": "vertex",
                "page": pi, "panel": panel_i, "vertex": ei,
            }
            best_dist = d

    if best is not None:
        return best

    # 辺
    for entry in _all_panel_edges_world(work):
        pi, panel_i, ei, a, b = entry
        ap = _world_mm_to_region(region, rv3d, a[0], a[1])
        bp = _world_mm_to_region(region, rv3d, b[0], b[1])
        if ap is None or bp is None:
            continue
        d, t = _distance_point_to_segment((mx, my), ap, bp)
        if d < EDGE_PICK_TOLERANCE_PX and d < best_dist:
            best = {
                "type": "edge",
                "page": pi, "panel": panel_i, "edge": ei,
            }
            best_dist = d
    return best


# ---------- ハンドル ----------


def _compute_handle_centers_px(
    region, rv3d, edge_a_mm: tuple[float, float], edge_b_mm: tuple[float, float],
) -> tuple[Optional[tuple[float, float]], Optional[tuple[float, float]]] | None:
    """辺中点から法線方向 ±HANDLE_OFFSET_PX に三角ハンドル 2 つの中心を返す."""
    ap = _world_mm_to_region(region, rv3d, *edge_a_mm)
    bp = _world_mm_to_region(region, rv3d, *edge_b_mm)
    if ap is None or bp is None:
        return None, None
    mx = (ap[0] + bp[0]) * 0.5
    my = (ap[1] + bp[1]) * 0.5
    dx = bp[0] - ap[0]
    dy = bp[1] - ap[1]
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return None, None
    nx = -dy / L  # 画面上の法線
    ny = dx / L
    h1 = (mx + nx * HANDLE_OFFSET_PX, my + ny * HANDLE_OFFSET_PX)
    h2 = (mx - nx * HANDLE_OFFSET_PX, my - ny * HANDLE_OFFSET_PX)
    return h1, h2


def _hit_handle(
    region, rv3d, edge_a_mm, edge_b_mm, mx: int, my: int,
) -> int:
    """ハンドルクリック判定: 0=どちらも外、1=正側、2=負側."""
    h1, h2 = _compute_handle_centers_px(region, rv3d, edge_a_mm, edge_b_mm) or (None, None)
    if h1 is not None and math.hypot(h1[0] - mx, h1[1] - my) < HANDLE_SIZE_PX:
        return 1
    if h2 is not None and math.hypot(h2[0] - mx, h2[1] - my) < HANDLE_SIZE_PX:
        return 2
    return 0


# ---------- Modal Operator ----------


class BNAME_OT_panel_edge_move(Operator):
    """枠線選択ツール: 辺/頂点を選択 → ドラッグ移動 + 色/太さ編集.

    シングルクリックで辺、ダブルクリックで枠線全体を選択する。
    """

    bl_idname = "bname.panel_edge_move"
    bl_label = "枠線選択ツール"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded and context.area is not None

    def invoke(self, context, event):
        if context.area is None or context.area.type != "VIEW_3D":
            return {"PASS_THROUGH"}
        target = _find_view3d(context)
        if target is None:
            return {"PASS_THROUGH"}
        self._area, self._region, self._rv3d = target
        self._work = get_work(context)
        if self._work is None or not self._work.loaded:
            return {"CANCELLED"}

        # 状態
        self._selection: Optional[dict] = None  # {"type":..., "page":..., ...}
        self._dragging = False
        self._drag_start_world: Optional[tuple[float, float]] = None
        self._original_geometry: Optional[dict] = None
        # シングル/ダブルクリック判定用
        self._last_press_time = 0.0
        self._last_press_edge: Optional[tuple[int, int, int]] = None

        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (self,), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        self._update_wm_selection(context)
        self._tag_redraw()
        self.report(
            {"INFO"},
            "枠線選択: 辺=シングル / 枠線全体=ダブル | ドラッグで移動 | ESC 終了",
        )
        return {"RUNNING_MODAL"}

    def _update_wm_selection(self, context) -> None:
        """WindowManager のグローバル選択状態を更新 (N パネル UI が読む)."""
        wm = context.window_manager
        sel = self._selection
        if sel is None:
            wm.bname_edge_select_kind = "none"
            wm.bname_edge_select_page = -1
            wm.bname_edge_select_panel = -1
            wm.bname_edge_select_edge = -1
            return
        t = sel.get("type")
        wm.bname_edge_select_page = int(sel.get("page", -1))
        wm.bname_edge_select_panel = int(sel.get("panel", -1))
        if t == "edge":
            wm.bname_edge_select_kind = "edge"
            wm.bname_edge_select_edge = int(sel.get("edge", -1))
        elif t == "border":
            wm.bname_edge_select_kind = "border"
            wm.bname_edge_select_edge = -1
        else:  # vertex 等
            wm.bname_edge_select_kind = "none"
            wm.bname_edge_select_edge = -1

    def _to_window(self, ev):
        return ev.mouse_x - self._region.x, ev.mouse_y - self._region.y

    def _is_inside_region(self, ev) -> bool:
        return (
            self._region.x <= ev.mouse_x < self._region.x + self._region.width
            and self._region.y <= ev.mouse_y < self._region.y + self._region.height
        )

    def _tag_redraw(self) -> None:
        if self._region is not None:
            self._region.tag_redraw()

    def _cleanup(self) -> None:
        h = getattr(self, "_draw_handler", None)
        if h is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
            except Exception:  # noqa: BLE001
                pass
            self._draw_handler = None
        self._tag_redraw()

    def modal(self, context, event):
        # Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y (undo / redo) はモーダルが保持する
        # PropertyGroup 参照を stale 化させて C レベル crash を起こすため、
        # 検知したら即座に modal を終了して event を本来の undo に譲る。
        if event.value == "PRESS" and event.type in {"Z", "Y"} and event.ctrl:
            self._cleanup()
            try:
                self._update_wm_selection(context)
            except Exception:  # noqa: BLE001
                pass
            return {"FINISHED", "PASS_THROUGH"}

        # B-Name の他ツール/モード切替ショートカットで modal を終了して譲る
        if (
            event.value == "PRESS"
            and event.type in {"O", "P", "F", "COMMA", "PERIOD"}
            and not event.ctrl
            and not event.alt
        ):
            self._cleanup()
            try:
                self._update_wm_selection(context)
            except Exception:  # noqa: BLE001
                pass
            return {"FINISHED", "PASS_THROUGH"}

        # G (自分自身) を modal 中に押されたら consume (= 二重起動を防ぐ)
        if (
            event.value == "PRESS"
            and event.type == "G"
            and not event.ctrl
            and not event.alt
            and not event.shift
        ):
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE":
            if self._dragging and self._selection is not None:
                self._apply_drag(event)
                self._tag_redraw()
            else:
                self._tag_redraw()  # ハンドル hover 表示更新は省略 (簡易)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                if not self._is_inside_region(event):
                    return {"PASS_THROUGH"}
                mx, my = self._to_window(event)
                # 既に辺選択中ならハンドルヒットを優先
                if self._selection is not None and self._selection.get("type") == "edge":
                    edge_world = self._get_selected_edge_world()
                    if edge_world is not None:
                        h = _hit_handle(
                            self._region, self._rv3d, edge_world[0], edge_world[1], mx, my,
                        )
                        if h != 0:
                            self._do_extend(h)
                            self._tag_redraw()
                            return {"RUNNING_MODAL"}
                # 新規ピック
                hit = _pick_edge_or_vertex(self._work, self._region, self._rv3d, mx, my)
                now = time.time()
                if hit is None:
                    self._selection = None
                    self._dragging = False
                    self._last_press_time = 0.0
                    self._last_press_edge = None
                elif hit.get("type") == "edge":
                    edge_key = (hit["page"], hit["panel"], hit["edge"])
                    is_double = (
                        self._last_press_edge == edge_key
                        and (now - self._last_press_time) < DOUBLE_CLICK_INTERVAL
                    )
                    if is_double:
                        # ダブルクリック → 枠線全体 (panel 単位) を選択
                        self._selection = {
                            "type": "border",
                            "page": hit["page"],
                            "panel": hit["panel"],
                        }
                        self._dragging = False
                        self._last_press_time = 0.0
                        self._last_press_edge = None
                    else:
                        # シングルクリック → 単一辺選択 + ドラッグ開始
                        self._selection = hit
                        self._dragging = True
                        self._drag_start_world = _region_to_world_mm(
                            self._region, self._rv3d, mx, my,
                        )
                        self._capture_original_geometry()
                        self._last_press_time = now
                        self._last_press_edge = edge_key
                else:
                    # vertex
                    self._selection = hit
                    self._dragging = True
                    self._drag_start_world = _region_to_world_mm(
                        self._region, self._rv3d, mx, my,
                    )
                    self._capture_original_geometry()
                    self._last_press_time = 0.0
                    self._last_press_edge = None
                self._update_wm_selection(context)
                self._tag_redraw()
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE":
                if self._dragging:
                    self._dragging = False
                    # 形状が実際に変わった (= ドラッグした) 場合のみ保存
                    # 単純クリック (PRESS-RELEASE) では save を走らせない
                    if self._geometry_changed():
                        self._save_changes()
                    self._tag_redraw()
                return {"RUNNING_MODAL"}

        if event.type in {"RIGHTMOUSE", "ESC", "RET", "NUMPAD_ENTER"} \
                and event.value == "PRESS":
            self._cleanup()
            try:
                self._update_wm_selection(context)
            except Exception:  # noqa: BLE001
                pass
            self.report({"INFO"}, "枠線選択ツール終了")
            return {"FINISHED"}
        return {"PASS_THROUGH"}

    # ---- 選択中の辺の world 座標 ----
    def _get_selected_edge_world(self) -> Optional[tuple[tuple[float, float], tuple[float, float]]]:
        sel = self._selection
        if sel is None or sel.get("type") != "edge":
            return None
        page = self._work.pages[sel["page"]]
        panel = page.panels[sel["panel"]]
        poly = _panel_polygon(panel)
        if len(poly) < 2:
            return None
        ei = sel["edge"]
        ox, oy = _page_offset(self._work, sel["page"])
        a = (poly[ei][0] + ox, poly[ei][1] + oy)
        b = (poly[(ei + 1) % len(poly)][0] + ox, poly[(ei + 1) % len(poly)][1] + oy)
        return a, b

    # ---- ドラッグ前の形状をスナップショット ----
    def _capture_original_geometry(self) -> None:
        sel = self._selection
        if sel is None:
            return
        page = self._work.pages[sel["page"]]
        panel = page.panels[sel["panel"]]
        # 自分の polygon
        snapshot = {"poly": _panel_polygon(panel)}
        # 隣接 (edge 選択時のみ): 対応 edge の vertex index を計算しておく
        if sel["type"] == "edge":
            adj = _find_adjacent_edges(
                self._work, sel["page"], sel["panel"], sel["edge"]
            )
            adj_states = []
            for pi, panel_i, ei in adj:
                p = self._work.pages[pi].panels[panel_i]
                adj_states.append(
                    {"page": pi, "panel": panel_i, "edge": ei, "poly": _panel_polygon(p)}
                )
            snapshot["adjacent_edges"] = adj_states
        elif sel["type"] == "vertex":
            # 頂点を共有する隣接 panel (位置一致 ± tolerance) を集める
            ox, oy = _page_offset(self._work, sel["page"])
            vi = sel["vertex"]
            poly = _panel_polygon(panel)
            v_world = (poly[vi][0] + ox, poly[vi][1] + oy)
            snapshot["v_world"] = v_world
            shared = []
            for pi, page2 in enumerate(self._work.pages):
                ox2, oy2 = _page_offset(self._work, pi)
                for panel_i, p in enumerate(page2.panels):
                    poly2 = _panel_polygon(p)
                    for vi2 in range(len(poly2)):
                        wp = (poly2[vi2][0] + ox2, poly2[vi2][1] + oy2)
                        if (pi, panel_i, vi2) == (sel["page"], sel["panel"], vi):
                            continue
                        if math.hypot(wp[0] - v_world[0], wp[1] - v_world[1]) < ADJACENCY_GAP_TOLERANCE_MM * 5:
                            shared.append({
                                "page": pi, "panel": panel_i, "vertex": vi2,
                                "poly": poly2,
                            })
            snapshot["shared_vertices"] = shared
        self._original_geometry = snapshot

    # ---- ドラッグ適用 ----
    def _apply_drag(self, event) -> None:
        sel = self._selection
        if sel is None or self._original_geometry is None or self._drag_start_world is None:
            return
        mx, my = self._to_window(event)
        cur_world = _region_to_world_mm(self._region, self._rv3d, mx, my)
        if cur_world is None:
            return
        dx = cur_world[0] - self._drag_start_world[0]
        dy = cur_world[1] - self._drag_start_world[1]

        if sel["type"] == "edge":
            # 辺を法線方向にシフト + 共有頂点を「隣接辺の line と新 line の交点」に
            # 補正することで、隣接辺の **角度を維持** したまま selected edge を動かす
            orig_poly = self._original_geometry["poly"]
            ei = sel["edge"]
            n = len(orig_poly)
            a = orig_poly[ei]
            b = orig_poly[(ei + 1) % n]
            ex = b[0] - a[0]
            ey = b[1] - a[1]
            L = math.hypot(ex, ey)
            if L < 1e-6:
                return
            nx = -ey / L
            ny = ex / L
            shift = dx * nx + dy * ny
            sx = nx * shift
            sy = ny * shift

            # 新 selected edge の line 上の 2 点
            a_new_line = (a[0] + sx, a[1] + sy)
            b_new_line = (b[0] + sx, b[1] + sy)

            # 共有頂点 a を、前の辺 (poly[ei-1] → a) の line と新 line の交点へ
            # → 前の辺の角度を維持したまま頂点が新 line 上にスライド
            prev_idx = (ei - 1 + n) % n
            a_prev = orig_poly[prev_idx]
            new_a = _line_intersect(a_prev, a, a_new_line, b_new_line, fallback=a_new_line)
            # 共有頂点 b を、次の辺 (b → poly[ei+2]) の line と新 line の交点へ
            next_idx = (ei + 2) % n
            b_next = orig_poly[next_idx]
            new_b = _line_intersect(b, b_next, a_new_line, b_new_line, fallback=b_new_line)

            new_poly = list(orig_poly)
            new_poly[ei] = new_a
            new_poly[(ei + 1) % n] = new_b
            page = self._work.pages[sel["page"]]
            panel = page.panels[sel["panel"]]
            _set_panel_polygon(panel, new_poly)

            # 隣接 edge も同じ shift で動かす + 共有頂点を交点補正 (gap 維持)
            for adj_st in self._original_geometry.get("adjacent_edges", []):
                p2 = self._work.pages[adj_st["page"]].panels[adj_st["panel"]]
                op2 = adj_st["poly"]
                ei2 = adj_st["edge"]
                n2 = len(op2)
                a2 = op2[ei2]
                b2 = op2[(ei2 + 1) % n2]
                a2_line = (a2[0] + sx, a2[1] + sy)
                b2_line = (b2[0] + sx, b2[1] + sy)
                prev_idx2 = (ei2 - 1 + n2) % n2
                a2_prev = op2[prev_idx2]
                new_a2 = _line_intersect(a2_prev, a2, a2_line, b2_line, fallback=a2_line)
                next_idx2 = (ei2 + 2) % n2
                b2_next = op2[next_idx2]
                new_b2 = _line_intersect(b2, b2_next, a2_line, b2_line, fallback=b2_line)
                np2 = list(op2)
                np2[ei2] = new_a2
                np2[(ei2 + 1) % n2] = new_b2
                _set_panel_polygon(p2, np2)

        elif sel["type"] == "vertex":
            orig_poly = self._original_geometry["poly"]
            vi = sel["vertex"]
            new_poly = list(orig_poly)
            new_poly[vi] = (orig_poly[vi][0] + dx, orig_poly[vi][1] + dy)
            page = self._work.pages[sel["page"]]
            panel = page.panels[sel["panel"]]
            _set_panel_polygon(panel, new_poly)
            # 共有頂点を同量シフト
            for sh in self._original_geometry.get("shared_vertices", []):
                p2 = self._work.pages[sh["page"]].panels[sh["panel"]]
                op2 = sh["poly"]
                vi2 = sh["vertex"]
                np2 = list(op2)
                np2[vi2] = (op2[vi2][0] + dx, op2[vi2][1] + dy)
                _set_panel_polygon(p2, np2)

    # ---- ハンドルアクション (拡張) ----
    def _do_extend(self, direction: int) -> None:
        """選択辺を direction (1=正側、2=負側) 方向に拡張.

        スナップ仕様:
        - 拡張先候補は同ページの他コマ辺 / 基本枠 / 裁ち落とし枠
        - **辺の角度はスナップ先 line の角度に合わせる** (両端を新 line に射影)
        - 共有頂点は prev/next 辺の line と新 line の交点で補正 (= 隣接辺の角度維持)
        - スナップ位置のオフセット:
          - bleed: 1mm 外側
          - 他コマ辺: ピッタリ重ねる (gap=0)
          - 基本枠: ピッタリ
        - **特殊ケース** (ピッタリ重なり状態 → 離れる方向▲): 法線方向と無関係に
          隣接コマ辺と元 selected edge の距離がほぼ 0 のとき、▲を sign 方向に
          押すと辺を sign 方向に gap_v / gap_h 分平行移動して隙間を空ける。
        """
        sel = self._selection
        if sel is None or sel.get("type") != "edge":
            return
        page = self._work.pages[sel["page"]]
        panel = page.panels[sel["panel"]]
        poly = _panel_polygon(panel)
        if len(poly) < 2:
            return
        ei = sel["edge"]
        n = len(poly)
        a = poly[ei]
        b = poly[(ei + 1) % n]
        ex = b[0] - a[0]
        ey = b[1] - a[1]
        L = math.hypot(ex, ey)
        if L < 1e-6:
            return
        nx = -ey / L
        ny = ex / L
        # direction 1: 法線正方向に拡張、direction 2: 法線負方向に拡張
        sign = 1.0 if direction == 1 else -1.0

        # 拡張先候補: 裁ち落とし枠 (bleed_rect) / 基本枠 / 他 panel の辺
        from ..utils.geom import bleed_rect, inner_frame_rect
        paper = self._work.paper
        br = bleed_rect(paper)
        ifr = inner_frame_rect(paper)

        # 候補となる「線」のリスト (page-local 座標) と種別を保持。
        # 種別: "bleed" (裁ち落とし枠) / "inner" (基本枠) / "panel" (他コマ辺)
        candidate_lines: list[
            tuple[tuple[float, float], tuple[float, float], str]
        ] = []
        # 裁ち落とし枠の 4 辺
        candidate_lines.extend([
            ((br.x, br.y), (br.x2, br.y), "bleed"),
            ((br.x2, br.y), (br.x2, br.y2), "bleed"),
            ((br.x2, br.y2), (br.x, br.y2), "bleed"),
            ((br.x, br.y2), (br.x, br.y), "bleed"),
        ])
        # 基本枠の 4 辺
        candidate_lines.extend([
            ((ifr.x, ifr.y), (ifr.x2, ifr.y), "inner"),
            ((ifr.x2, ifr.y), (ifr.x2, ifr.y2), "inner"),
            ((ifr.x2, ifr.y2), (ifr.x, ifr.y2), "inner"),
            ((ifr.x, ifr.y2), (ifr.x, ifr.y), "inner"),
        ])
        # 他 panel の edge (同 panel の対辺は拡張先として不適切なので除外)
        for panel_i2, p2 in enumerate(page.panels):
            if panel_i2 == sel["panel"]:
                continue
            poly2 = _panel_polygon(p2)
            for ei2 in range(len(poly2)):
                candidate_lines.append(
                    (poly2[ei2], poly2[(ei2 + 1) % len(poly2)], "panel")
                )

        # 重なり判定ヘルパ (端点を tangent 軸に投影し edge と被るか)
        tx = ex / L
        ty = ey / L

        def _has_tangent_overlap(ca_, cb_) -> bool:
            t_a = (ca_[0] - a[0]) * tx + (ca_[1] - a[1]) * ty
            t_b = (cb_[0] - a[0]) * tx + (cb_[1] - a[1]) * ty
            lo = min(t_a, t_b)
            hi = max(t_a, t_b)
            return hi >= -L * 0.1 and lo <= L * 1.1

        # コマ間隔 (現拡張軸に応じた値)
        gap_v = float(self._work.panel_gap.vertical_mm)
        gap_h = float(self._work.panel_gap.horizontal_mm)
        target_gap_axis = gap_v if abs(ny) >= abs(nx) else gap_h

        OVERLAP_TOL_MM = 0.5  # 元辺との距離が ≤ これで「ピッタリ重なっている」と判定

        # ===== 特殊ケース: 元 selected edge と「ほぼ距離 0」で重なっている隣接コマがあり、
        # かつ ▲sign 方向が「その panel から離れる方向」の場合 → gap を空ける =====
        # 「離れる方向」判定は panel 中心の符号で行う (panel の中心が -sign 側にあれば
        # ▲sign は panel から遠ざかる方向)
        has_panel_overlap_opposite = False
        for panel_i2, p2 in enumerate(page.panels):
            if panel_i2 == sel["panel"]:
                continue
            poly2 = _panel_polygon(p2)
            if len(poly2) < 3:
                continue
            cx_avg = sum(v[0] for v in poly2) / len(poly2)
            cy_avg = sum(v[1] for v in poly2) / len(poly2)
            d_center = (cx_avg - a[0]) * nx + (cy_avg - a[1]) * ny
            # panel 中心が -sign 側にあるとき only (= ▲sign が「離れる方向」)
            if -sign * d_center <= 0:
                continue
            for ei2 in range(len(poly2)):
                ca = poly2[ei2]
                cb = poly2[(ei2 + 1) % len(poly2)]
                # 平行性: 辺方向が selected edge と平行な辺だけ対象
                ux2 = cb[0] - ca[0]
                uy2 = cb[1] - ca[1]
                l2 = math.hypot(ux2, uy2)
                if l2 < 1e-6:
                    continue
                dot = (ux2 / l2) * tx + (uy2 / l2) * ty
                if abs(abs(dot) - 1.0) > 0.05:
                    continue
                if not _has_tangent_overlap(ca, cb):
                    continue
                mid = ((ca[0] + cb[0]) * 0.5, (ca[1] + cb[1]) * 0.5)
                d = (mid[0] - a[0]) * nx + (mid[1] - a[1]) * ny
                if abs(d) < OVERLAP_TOL_MM:
                    has_panel_overlap_opposite = True
                    break
            if has_panel_overlap_opposite:
                break

        has_panel_overlap = has_panel_overlap_opposite

        if has_panel_overlap:
            # ピッタリ重なっている隣接コマ辺がある → ▲sign 方向に gap 分平行移動
            # (角度は元のまま維持、スナップ先 line に合わせる必要なし)
            total_shift = target_gap_axis
            if total_shift < 0.05:
                self.report({"INFO"}, "コマ間隔が 0 のため移動できません")
                return
            sx_ext = sign * total_shift * nx
            sy_ext = sign * total_shift * ny
            a_new_line = (a[0] + sx_ext, a[1] + sy_ext)
            b_new_line = (b[0] + sx_ext, b[1] + sy_ext)
            kind_label = "隣接コマからスキマを空けました"
        else:
            # ===== 通常: sign 方向の最寄り候補を探索 =====
            best_dist = float("inf")
            best_line: Optional[tuple[tuple[float, float], tuple[float, float]]] = None
            best_kind: str = ""
            for ca, cb, kind in candidate_lines:
                mid = ((ca[0] + cb[0]) * 0.5, (ca[1] + cb[1]) * 0.5)
                d = (mid[0] - a[0]) * nx + (mid[1] - a[1]) * ny
                d_signed = sign * d
                if d_signed > OVERLAP_TOL_MM and d_signed < best_dist:
                    if _has_tangent_overlap(ca, cb):
                        best_dist = d_signed
                        best_line = (ca, cb)
                        best_kind = kind
            if best_line is None:
                self.report({"INFO"}, "拡張先が見つかりません")
                return

            # 種別ごとのスナップ位置オフセット
            # - bleed: 裁ち落とし枠の **1mm 外側**
            # - panel: 隣接コマ辺に **ピッタリ重ねる** (gap=0)
            #   → ピッタリ重なった後にユーザーが反対方向の▲を押せば
            #     上の "has_panel_overlap" ケースで gap が空く動作
            # - inner: 基本枠線にピッタリ
            if best_kind == "bleed":
                offset_along_norm = 1.0
            elif best_kind == "panel":
                offset_along_norm = 0.0
            else:
                offset_along_norm = 0.0

            ca, cb = best_line
            shift_vec_x = sign * offset_along_norm * nx
            shift_vec_y = sign * offset_along_norm * ny
            # 新 selected line = スナップ先 line (オフセット適用済) → **角度はスナップ先と同じ**
            a_new_line = (ca[0] + shift_vec_x, ca[1] + shift_vec_y)
            b_new_line = (cb[0] + shift_vec_x, cb[1] + shift_vec_y)
            if math.hypot(b_new_line[0] - a_new_line[0],
                          b_new_line[1] - a_new_line[1]) < 1e-6:
                self.report({"WARNING"}, "拡張先 line が縮退しています")
                return
            kind_label = {
                "bleed": "裁ち落とし枠の 1mm 外側",
                "inner": "基本枠",
                "panel": "隣接コマ辺にピッタリ",
            }.get(best_kind, "拡張先")

        # 共有頂点を prev/next 辺の line と新 selected line の交点に補正することで、
        # 隣接辺の角度を維持する
        prev_idx = (ei - 1 + n) % n
        a_prev = poly[prev_idx]
        new_a = _line_intersect(
            a_prev, a, a_new_line, b_new_line, fallback=a_new_line
        )
        next_idx = (ei + 2) % n
        b_next = poly[next_idx]
        new_b = _line_intersect(
            b, b_next, a_new_line, b_new_line, fallback=b_new_line
        )

        new_poly = list(poly)
        new_poly[ei] = new_a
        new_poly[(ei + 1) % n] = new_b
        _set_panel_polygon(panel, new_poly)
        try:
            self._save_changes()
        except Exception:  # noqa: BLE001
            _logger.exception("edge_move: extend save failed")
        self.report({"INFO"}, f"枠線を拡張: {kind_label}")

    # ---- 形状変化検出 ----
    def _geometry_changed(self) -> bool:
        """ドラッグ前のスナップショットと現在の形状を比較.

        浮動小数点誤差を考慮し、いずれかの頂点が 0.001mm 以上動いていれば True。
        """
        if self._original_geometry is None or self._selection is None:
            return False
        sel = self._selection
        try:
            page = self._work.pages[sel["page"]]
            panel = page.panels[sel["panel"]]
        except (IndexError, KeyError):
            return False
        current = _panel_polygon(panel)
        original = self._original_geometry.get("poly", [])
        if len(current) != len(original):
            return True
        for (cx, cy), (ox, oy) in zip(current, original):
            if abs(cx - ox) > 1e-3 or abs(cy - oy) > 1e-3:
                return True
        return False

    # ---- 保存 ----
    def _save_changes(self) -> None:
        work = self._work
        if work is None or work.work_dir == "":
            return
        work_dir = Path(work.work_dir)
        # 影響を受けたページの panel を保存
        sel = self._selection
        affected_pages: set[int] = set()
        if sel is not None:
            affected_pages.add(sel["page"])
            for st in self._original_geometry.get("adjacent_edges", []) if self._original_geometry else []:
                affected_pages.add(st["page"])
            for st in self._original_geometry.get("shared_vertices", []) if self._original_geometry else []:
                affected_pages.add(st["page"])
        for pi in affected_pages:
            page = work.pages[pi]
            try:
                for panel in page.panels:
                    panel_io.save_panel_meta(work_dir, page.id, panel)
                page_io.save_page_json(work_dir, page)
            except Exception:  # noqa: BLE001
                _logger.exception("edge_move: save page %s failed", page.id)
        try:
            page_io.save_pages_json(work_dir, work)
        except Exception:  # noqa: BLE001
            _logger.exception("edge_move: save pages.json failed")


# ---------- POST_PIXEL 描画 ----------


def _draw_callback(op: "BNAME_OT_panel_edge_move") -> None:
    sel = op._selection
    if sel is None:
        return
    region = op._region
    rv3d = op._rv3d
    work = op._work
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")

    if sel["type"] == "border":
        # 枠線全体ハイライト (panel の全 edge を強調表示)
        page = work.pages[sel["page"]]
        panel = page.panels[sel["panel"]]
        poly = _panel_polygon(panel)
        if len(poly) < 2:
            return
        ox, oy = _page_offset(work, sel["page"])
        try:
            gpu.state.line_width_set(4.0)
        except Exception:  # noqa: BLE001
            pass
        verts: list[tuple[float, float]] = []
        n = len(poly)
        for i in range(n):
            a = poly[i]
            b = poly[(i + 1) % n]
            ap = _world_mm_to_region(region, rv3d, a[0] + ox, a[1] + oy)
            bp = _world_mm_to_region(region, rv3d, b[0] + ox, b[1] + oy)
            if ap is None or bp is None:
                continue
            verts.append(ap)
            verts.append(bp)
        if verts:
            batch = batch_for_shader(shader, "LINES", {"pos": verts})
            shader.bind()
            shader.uniform_float("color", COLOR_SELECTED_BORDER)
            batch.draw(shader)
        try:
            gpu.state.line_width_set(1.0)
        except Exception:  # noqa: BLE001
            pass
        return

    if sel["type"] == "edge":
        edge_world = op._get_selected_edge_world()
        if edge_world is None:
            return
        a, b = edge_world
        ap = _world_mm_to_region(region, rv3d, a[0], a[1])
        bp = _world_mm_to_region(region, rv3d, b[0], b[1])
        if ap is None or bp is None:
            return
        # 選択辺ハイライト
        try:
            gpu.state.line_width_set(4.0)
        except Exception:  # noqa: BLE001
            pass
        batch = batch_for_shader(shader, "LINES", {"pos": [ap, bp]})
        shader.bind()
        shader.uniform_float("color", COLOR_SELECTED_EDGE)
        batch.draw(shader)
        try:
            gpu.state.line_width_set(1.0)
        except Exception:  # noqa: BLE001
            pass

        # 三角ハンドル (法線 ±)
        h1, h2 = _compute_handle_centers_px(region, rv3d, a, b) or (None, None)
        for handle, dir_idx in ((h1, 1), (h2, 2)):
            if handle is None:
                continue
            _draw_triangle_handle(shader, handle, ap, bp, dir_idx)

    elif sel["type"] == "vertex":
        page = work.pages[sel["page"]]
        panel = page.panels[sel["panel"]]
        poly = _panel_polygon(panel)
        if len(poly) <= sel["vertex"]:
            return
        ox, oy = _page_offset(work, sel["page"])
        v = poly[sel["vertex"]]
        vp = _world_mm_to_region(region, rv3d, v[0] + ox, v[1] + oy)
        if vp is None:
            return
        # 頂点ハイライト (小さい円 = 矩形で代用)
        s = 6.0
        verts = [
            (vp[0] - s, vp[1] - s), (vp[0] + s, vp[1] - s),
            (vp[0] + s, vp[1] + s), (vp[0] - s, vp[1] + s),
        ]
        batch = batch_for_shader(
            shader, "TRIS", {"pos": verts},
            indices=[(0, 1, 2), (0, 2, 3)],
        )
        shader.bind()
        shader.uniform_float("color", COLOR_SELECTED_VERTEX)
        batch.draw(shader)


def _draw_triangle_handle(
    shader, center: tuple[float, float],
    edge_a_px: tuple[float, float], edge_b_px: tuple[float, float],
    direction_idx: int,
) -> None:
    """edge の法線方向 (direction_idx=1 or 2) を向く三角形を描画."""
    cx, cy = center
    ex = edge_b_px[0] - edge_a_px[0]
    ey = edge_b_px[1] - edge_a_px[1]
    L = math.hypot(ex, ey)
    if L < 1e-6:
        return
    # 法線
    nx = -ey / L
    ny = ex / L
    if direction_idx == 2:
        nx, ny = -nx, -ny
    # tangent
    tx = ex / L
    ty = ey / L
    s = HANDLE_SIZE_PX
    # 三角形: 頂点 = 中心 + 法線方向 s, 左右 base = 中心 ± tangent * s/2 - 法線 * s/2
    apex = (cx + nx * s, cy + ny * s)
    base_l = (cx - tx * s * 0.5 - nx * s * 0.3, cy - ty * s * 0.5 - ny * s * 0.3)
    base_r = (cx + tx * s * 0.5 - nx * s * 0.3, cy + ty * s * 0.5 - ny * s * 0.3)
    verts = [apex, base_l, base_r]
    batch = batch_for_shader(
        shader, "TRIS", {"pos": verts}, indices=[(0, 1, 2)],
    )
    shader.bind()
    shader.uniform_float("color", COLOR_HANDLE)
    batch.draw(shader)


# ---------- edge_style 操作 (UI から呼ぶ) ----------


def _find_or_add_edge_style(panel, edge_index: int):
    """edge_index に対応する edge_style を返す (なければ追加)."""
    for s in panel.edge_styles:
        if int(s.edge_index) == int(edge_index):
            return s
    s = panel.edge_styles.add()
    s.edge_index = int(edge_index)
    # 既定値: panel.border から継承
    s.width_mm = float(panel.border.width_mm)
    s.color = panel.border.color
    return s


def _remove_edge_style(panel, edge_index: int) -> bool:
    for i, s in enumerate(panel.edge_styles):
        if int(s.edge_index) == int(edge_index):
            panel.edge_styles.remove(i)
            return True
    return False


def _save_panel_change(work, page) -> None:
    if work is None or work.work_dir == "":
        return
    work_dir = Path(work.work_dir)
    try:
        for p in page.panels:
            panel_io.save_panel_meta(work_dir, page.id, p)
        page_io.save_page_json(work_dir, page)
    except Exception:  # noqa: BLE001
        _logger.exception("edge_style save failed")


class BNAME_OT_edge_style_create(Operator):
    """選択中の辺に edge_style override を新規作成 (現在の border 色/太さで初期化)."""

    bl_idname = "bname.edge_style_create"
    bl_label = "この辺に個別設定を追加"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        wm = context.window_manager
        if wm.bname_edge_select_kind != "edge":
            return {"CANCELLED"}
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        pi = wm.bname_edge_select_page
        pn = wm.bname_edge_select_panel
        ei = wm.bname_edge_select_edge
        if not (0 <= pi < len(work.pages)):
            return {"CANCELLED"}
        page = work.pages[pi]
        if not (0 <= pn < len(page.panels)):
            return {"CANCELLED"}
        panel = page.panels[pn]
        _find_or_add_edge_style(panel, ei)
        _save_panel_change(work, page)
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
        return {"FINISHED"}


class BNAME_OT_edge_style_remove(Operator):
    """選択中の辺の edge_style override を削除 (panel.border 設定に戻る)."""

    bl_idname = "bname.edge_style_remove"
    bl_label = "個別設定を削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        wm = context.window_manager
        if wm.bname_edge_select_kind != "edge":
            return {"CANCELLED"}
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        pi = wm.bname_edge_select_page
        pn = wm.bname_edge_select_panel
        ei = wm.bname_edge_select_edge
        if not (0 <= pi < len(work.pages)):
            return {"CANCELLED"}
        page = work.pages[pi]
        if not (0 <= pn < len(page.panels)):
            return {"CANCELLED"}
        panel = page.panels[pn]
        _remove_edge_style(panel, ei)
        _save_panel_change(work, page)
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
        return {"FINISHED"}


class BNAME_OT_edge_style_clear_all(Operator):
    """選択中の panel の全 edge_style override を一括削除."""

    bl_idname = "bname.edge_style_clear_all"
    bl_label = "全ての個別設定を削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        wm = context.window_manager
        if wm.bname_edge_select_kind not in {"edge", "border"}:
            return {"CANCELLED"}
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        pi = wm.bname_edge_select_page
        pn = wm.bname_edge_select_panel
        if not (0 <= pi < len(work.pages)):
            return {"CANCELLED"}
        page = work.pages[pi]
        if not (0 <= pn < len(page.panels)):
            return {"CANCELLED"}
        panel = page.panels[pn]
        panel.edge_styles.clear()
        _save_panel_change(work, page)
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_panel_edge_move,
    BNAME_OT_edge_style_create,
    BNAME_OT_edge_style_remove,
    BNAME_OT_edge_style_clear_all,
)


def register() -> None:
    from bpy.props import EnumProperty, IntProperty
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bname_edge_select_kind = EnumProperty(
        name="選択種別",
        items=[
            ("none", "未選択", ""),
            ("edge", "辺", ""),
            ("border", "枠線全体", ""),
        ],
        default="none",
    )
    bpy.types.WindowManager.bname_edge_select_page = IntProperty(default=-1)
    bpy.types.WindowManager.bname_edge_select_panel = IntProperty(default=-1)
    bpy.types.WindowManager.bname_edge_select_edge = IntProperty(default=-1)


def unregister() -> None:
    for prop in (
        "bname_edge_select_kind",
        "bname_edge_select_page",
        "bname_edge_select_panel",
        "bname_edge_select_edge",
    ):
        try:
            delattr(bpy.types.WindowManager, prop)
        except AttributeError:
            pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
