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
from bpy_extras.view3d_utils import region_2d_to_location_3d
from gpu_extras.batch import batch_for_shader

from ..core.work import get_work
from ..io import page_io, panel_io
from . import panel_modal_state
from ..utils import geom, layer_stack as layer_stack_utils, log, page_grid

_logger = log.get_logger(__name__)


COLOR_CUT_LINE = (1.0, 0.1, 0.1, 0.95)
NAV_GIZMO_HITBOX_WIDTH_PX = 112.0
NAV_GIZMO_HITBOX_HEIGHT_PX = 232.0
NAV_GIZMO_HITBOX_MARGIN_PX = 8.0


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


def _panel_polygon(panel) -> list[tuple[float, float]]:
    """panel エントリから多角形頂点リストを返す (mm、CCW)."""
    if panel.shape_type == "rect":
        x, y = panel.rect_x_mm, panel.rect_y_mm
        w, h = panel.rect_width_mm, panel.rect_height_mm
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if panel.shape_type == "polygon":
        return [(v.x_mm, v.y_mm) for v in panel.vertices]
    return []


def _set_panel_polygon(panel, poly: Sequence[tuple[float, float]]) -> None:
    """panel エントリの形状を多角形 (vertices) に書き換える."""
    panel.shape_type = "polygon"
    panel.vertices.clear()
    for x, y in poly:
        v = panel.vertices.add()
        v.x_mm = float(x)
        v.y_mm = float(y)
    # rect_* は無効化 (外接矩形を入れておくと panel_to_rect で復元しやすい)
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

    panel 個別の panel_gap_*_mm (>= 0) が優先、負値なら work.panel_gap を継承。
    """
    pgv = float(getattr(panel, "panel_gap_vertical_mm", -1.0))
    pgh = float(getattr(panel, "panel_gap_horizontal_mm", -1.0))
    gap_v = pgv if pgv >= 0.0 else float(work.panel_gap.vertical_mm)
    gap_h = pgh if pgh >= 0.0 else float(work.panel_gap.horizontal_mm)

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


def _apply_cut_to_panel(
    work, page, panel_idx: int, work_dir: Path,
    A_local: tuple[float, float], B_local: tuple[float, float],
) -> bool:
    """1 つのコマだけを cut line A-B で分割.

    コマ間隔は work.panel_gap (もしくは panel 個別オーバーライド) を
    カット線の角度に応じて補間して適用する。
    戻り値: 分割が発生したか。
    """
    from .panel_op import _copy_panel_entry

    if not (0 <= panel_idx < len(page.panels)):
        return False
    panel = page.panels[panel_idx]
    poly = _panel_polygon(panel)
    if not poly:
        return False
    gap_mm = _effective_gap_mm(work, A_local, B_local, panel)
    result = _split_convex_polygon_by_line(poly, A_local, B_local, gap_mm=gap_mm)
    if result is None:
        return False
    left_poly, right_poly = result

    # 元コマを左側に書き換え
    _set_panel_polygon(panel, left_poly)
    # カットで edge_index が変わるため edge_styles 個別オーバーライドはクリア
    panel.edge_styles.clear()
    # 新規コマ (右側) を追加
    new_stem = panel_io.allocate_new_panel_stem(work_dir, page.id)
    try:
        panel_io.copy_panel_files(
            work_dir, page.id, page.id, panel.panel_stem, new_stem
        )
    except Exception:  # noqa: BLE001
        _logger.warning("knife_cut: copy_panel_files failed for %s", panel.panel_stem)
    new_entry = page.panels.add()
    _copy_panel_entry(panel, new_entry)
    new_entry.panel_stem = new_stem
    new_entry.id = new_stem.split("_", 1)[1]
    new_entry.title = f"{panel.title} (分割)"
    _set_panel_polygon(new_entry, right_poly)
    new_entry.edge_styles.clear()  # 元コマから複製された個別設定もクリア
    z_max = max((p.z_order for p in page.panels), default=0)
    new_entry.z_order = z_max + 1
    try:
        panel_io.save_panel_meta(work_dir, page.id, panel)
        panel_io.save_panel_meta(work_dir, page.id, new_entry)
    except Exception:  # noqa: BLE001
        _logger.exception("knife_cut: save_panel_meta failed")

    page.panel_count = len(page.panels)
    try:
        page_io.save_page_json(work_dir, page)
    except Exception:  # noqa: BLE001
        _logger.exception("knife_cut: save_page_json failed")
    return True


def _sync_layer_stack_after_cut(context) -> None:
    try:
        layer_stack_utils.sync_layer_stack_after_data_change(
            context,
            align_panel_order=True,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("knife_cut: layer stack sync failed")


def _find_panel_at_world(
    work, x_mm: float, y_mm: float,
) -> tuple[int, int] | None:
    """world (mm) 座標下のコマを (page_index, panel_index) で返す.

    全ページの grid offset を考慮して走査。同位置に複数コマあれば Z 順最大。
    """
    scene = bpy.context.scene
    if scene is None:
        return None
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    cw = work.paper.canvas_width_mm
    ch = work.paper.canvas_height_mm
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    for i, page in enumerate(work.pages):
        ox, oy = page_grid.page_grid_offset_mm(
            i, cols, gap, cw, ch, start_side, read_direction
        )
        add_x, add_y = page_grid.page_manual_offset_mm(page)
        ox += add_x
        oy += add_y
        local_x = x_mm - ox
        local_y = y_mm - oy
        if not (0.0 <= local_x <= cw and 0.0 <= local_y <= ch):
            continue
        # Z 順最大を優先
        sorted_panels = sorted(
            range(len(page.panels)),
            key=lambda j: -page.panels[j].z_order,
        )
        for panel_idx in sorted_panels:
            poly = _panel_polygon(page.panels[panel_idx])
            if not poly:
                continue
            if _point_in_polygon((local_x, local_y), poly):
                return (i, panel_idx)
    return None


# ---------- modal operator ----------


class BNAME_OT_panel_knife_cut(Operator):
    """枠線カットツール (CSP 互換): 任意角度の切断線で複数コマを連続分割する."""

    bl_idname = "bname.panel_knife_cut"
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
        if panel_modal_state.get_active("knife_cut") is not None:
            panel_modal_state.finish_active(
                "knife_cut", context, keep_selection=False,
            )
            return {"FINISHED"}
        panel_modal_state.finish_active("edge_move", context, keep_selection=False)
        panel_modal_state.finish_active("layer_move", context, keep_selection=False)
        panel_modal_state.finish_active("balloon_tool", context, keep_selection=True)
        panel_modal_state.finish_active("text_tool", context, keep_selection=True)
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

        # POST_PIXEL でラバーバンドを描画
        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (self,), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        self._cursor_modal_set = panel_modal_state.set_modal_cursor(context, "CROSSHAIR")
        panel_modal_state.set_active("knife_cut", self, context)
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
        region = self._region_at_mouse(ev)
        return region is not None and region.type == "WINDOW" and region == self._region

    def _is_over_navigation_gizmo(self, ev) -> bool:
        if not self._is_inside_region(ev):
            return False
        prefs_view = getattr(getattr(bpy.context, "preferences", None), "view", None)
        if prefs_view is not None and not bool(getattr(prefs_view, "show_navigate_ui", True)):
            return False
        space = getattr(self._area.spaces, "active", None)
        if space is not None:
            if not bool(getattr(space, "show_gizmo", True)):
                return False
            if not bool(getattr(space, "show_gizmo_navigate", True)):
                return False
        mx, my = self._to_window(ev)
        return (
            mx >= self._region.width - NAV_GIZMO_HITBOX_WIDTH_PX - NAV_GIZMO_HITBOX_MARGIN_PX
            and my >= self._region.height - NAV_GIZMO_HITBOX_HEIGHT_PX - NAV_GIZMO_HITBOX_MARGIN_PX
        )

    def _tag_redraw(self) -> None:
        if self._region is not None:
            self._region.tag_redraw()

    def _cleanup(self, context=None) -> None:
        if getattr(self, "_cursor_modal_set", False):
            panel_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        h = getattr(self, "_draw_handler", None)
        if h is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
            except Exception:  # noqa: BLE001
                pass
            self._draw_handler = None
        self._tag_redraw()

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        _ = keep_selection
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        self._cleanup(context)
        panel_modal_state.clear_active("knife_cut", self, context)

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            panel_modal_state.clear_active("knife_cut", self, context)
            return {"FINISHED", "PASS_THROUGH"}
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
                    bpy.ops.bname.panel_edge_move("INVOKE_DEFAULT")
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
            self.finish_from_external(context, keep_selection=False)
            return {"FINISHED"}

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
        hit = _find_panel_at_world(work, xa, ya)
        if hit is None:
            self.report({"INFO"}, "開始位置にコマがありません")
            return
        page_idx, panel_idx = hit
        page = work.pages[page_idx]

        # 対象ページの grid offset を引いてページローカル座標に
        scene = bpy.context.scene
        cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
        gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
        cw = work.paper.canvas_width_mm
        ch = work.paper.canvas_height_mm
        start_side = getattr(work.paper, "start_side", "right")
        read_direction = getattr(work.paper, "read_direction", "left")
        ox, oy = page_grid.page_grid_offset_mm(
            page_idx, cols, gap, cw, ch, start_side, read_direction
        )
        add_x, add_y = page_grid.page_manual_offset_mm(page)
        ox += add_x
        oy += add_y
        A_local = (xa - ox, ya - oy)
        B_local = (xb - ox, yb - oy)

        ok = _apply_cut_to_panel(work, page, panel_idx, work_dir, A_local, B_local)
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


# ---------- POST_PIXEL ラバーバンド ----------


def _draw_callback(op: "BNAME_OT_panel_knife_cut") -> None:
    if not op._dragging or op._p1_px is None or op._p2_px is None:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
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


_CLASSES = (BNAME_OT_panel_knife_cut,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
