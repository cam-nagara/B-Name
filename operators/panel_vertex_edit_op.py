"""コマ枠の頂点・辺をビューポート上でドラッグ編集する modal オペレータ.

矩形/多角形コマ共通:
- **頂点ハンドル**: ドラッグで単一頂点を移動
  - rect: ドラッグで対角コーナー固定のまま rect_*_mm を更新
  - polygon: vertices[i] を更新
- **辺ハンドル (中点)**: ドラッグで辺を平行移動
  - rect: 対辺を固定、辺を垂直に平行移動
  - polygon: 両端頂点を同時に平行移動
- ESC / 右クリック: キャンセル (変更を破棄)
- Enter / 左クリック外側: 確定

スナップ対象 (X/Y 座標):
- 基本枠 (inner_frame) の 4 辺
- キャンバスの 4 辺
- 仕上がり枠の 4 辺
- 他のコマの辺/頂点
しきい値は ``SNAP_THRESHOLD_MM`` (既定 2.0mm)。
"""

from __future__ import annotations

from pathlib import Path

import bpy
import gpu
from bpy.types import Operator
from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_location_3d
from gpu_extras.batch import batch_for_shader

from ..core.work import get_active_page, get_work
from ..io import page_io, panel_io
from ..utils import geom, log

_logger = log.get_logger(__name__)


# ---- 定数 ----

HANDLE_SIZE_PX = 10.0  # 頂点ハンドルの 1 辺 (ピクセル)
EDGE_HANDLE_SIZE_PX = 8.0  # 辺ハンドルの 1 辺
HANDLE_HIT_PX = 15.0  # ヒット半径 (ピクセル)
SNAP_THRESHOLD_MM = 2.0  # スナップしきい値 (mm)

COLOR_VERTEX = (1.0, 1.0, 0.0, 1.0)  # 黄
COLOR_VERTEX_HOVER = (1.0, 0.5, 0.0, 1.0)  # オレンジ
COLOR_VERTEX_ACTIVE = (1.0, 0.0, 0.0, 1.0)  # 赤 (ドラッグ中)
COLOR_EDGE = (0.2, 0.9, 1.0, 1.0)  # シアン
COLOR_EDGE_HOVER = (0.0, 0.6, 1.0, 1.0)
COLOR_SNAP_GUIDE = (0.0, 1.0, 0.2, 0.9)  # 緑


# ---- ヘルパ ----


def _find_view3d_window(context):
    """VIEW_3D エリアの WINDOW リージョン + region_data を返す.

    N パネル内からオペレータを起動すると context.region が UI リージョン
    になるため、明示的に WINDOW リージョンを探す必要がある。
    """
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


def _panel_vertices_mm(entry) -> list[tuple[float, float]]:
    """コマ枠の頂点列を (x_mm, y_mm) の list で返す.

    rect: 左下から反時計回り [BL, BR, TR, TL]
    polygon: entry.vertices の順
    """
    if entry.shape_type == "rect":
        return [
            (entry.rect_x_mm, entry.rect_y_mm),
            (entry.rect_x_mm + entry.rect_width_mm, entry.rect_y_mm),
            (entry.rect_x_mm + entry.rect_width_mm, entry.rect_y_mm + entry.rect_height_mm),
            (entry.rect_x_mm, entry.rect_y_mm + entry.rect_height_mm),
        ]
    return [(v.x_mm, v.y_mm) for v in entry.vertices]


def _region_2d_to_mm(region, rv3d, mx: float, my: float) -> tuple[float, float] | None:
    loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
    if loc is None:
        return None
    return geom.m_to_mm(loc.x), geom.m_to_mm(loc.y)


def _mm_to_region_2d(region, rv3d, x_mm: float, y_mm: float):
    return location_3d_to_region_2d(
        region, rv3d, (geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0)
    )


def _compute_page_offset(context) -> tuple[float, float]:
    """overview モード時の active ページの grid offset (mm) を返す."""
    scene = context.scene if context else bpy.context.scene
    if scene is None or not getattr(scene, "bname_overview_mode", False):
        return (0.0, 0.0)
    work = get_work(context)
    if work is None or not work.loaded:
        return (0.0, 0.0)
    idx = work.active_page_index
    if not (0 <= idx < len(work.pages)):
        return (0.0, 0.0)
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    cw = work.paper.canvas_width_mm
    ch = work.paper.canvas_height_mm
    col = idx % cols
    row = idx // cols
    return (-col * (cw + gap), -row * (ch + gap))


def _collect_snap_lines(work, page, current_entry) -> tuple[list[float], list[float]]:
    """X/Y 座標のスナップ候補を返す (単位 mm)."""
    xs: list[float] = []
    ys: list[float] = []
    p = work.paper
    # 基本枠
    ix = (p.canvas_width_mm - p.inner_frame_width_mm) / 2.0 + p.inner_frame_offset_x_mm
    iy = (p.canvas_height_mm - p.inner_frame_height_mm) / 2.0 + p.inner_frame_offset_y_mm
    xs.extend([ix, ix + p.inner_frame_width_mm])
    ys.extend([iy, iy + p.inner_frame_height_mm])
    # キャンバス
    xs.extend([0.0, p.canvas_width_mm])
    ys.extend([0.0, p.canvas_height_mm])
    # 仕上がり枠
    fw, fh = p.finish_width_mm, p.finish_height_mm
    fx = (p.canvas_width_mm - fw) / 2.0
    fy = (p.canvas_height_mm - fh) / 2.0
    xs.extend([fx, fx + fw])
    ys.extend([fy, fy + fh])
    # 他のコマ
    for entry in page.panels:
        if entry is current_entry:
            continue
        if entry.shape_type == "rect":
            xs.extend([entry.rect_x_mm, entry.rect_x_mm + entry.rect_width_mm])
            ys.extend([entry.rect_y_mm, entry.rect_y_mm + entry.rect_height_mm])
        else:
            for v in entry.vertices:
                xs.append(v.x_mm)
                ys.append(v.y_mm)
    return xs, ys


def _snap_value(value_mm: float, candidates, threshold_mm: float):
    """``value_mm`` を候補にスナップ。結果とスナップ線座標を返す。

    スナップなしの場合 (value_mm, None) を返す。
    """
    best = None
    best_dist = threshold_mm
    for c in candidates:
        d = abs(c - value_mm)
        if d < best_dist:
            best = c
            best_dist = d
    if best is not None:
        return best, best
    return value_mm, None


# ---- オペレータ ----


class BNAME_OT_panel_edit_vertices(Operator):
    """選択中コマの頂点・辺をビューポート上でドラッグ編集."""

    bl_idname = "bname.panel_edit_vertices"
    bl_label = "コマ枠を編集 (頂点/辺ドラッグ)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        # invoke 側でマウス直下のコマへ逆引きフォーカスするため、active panel
        # が未選択でも起動できるようにする (overview のクリック起動対応)。
        work = get_work(context)
        return (
            work is not None
            and work.loaded
            and context.area is not None
            and context.area.type == "VIEW_3D"
        )

    def invoke(self, context, event):
        # マウス直下のコマへフォーカス (overview 時は全ページ逆引き)
        from . import panel_edit_op

        panel_edit_op._resolve_target_from_event(context, event)

        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            self.report({"WARNING"}, "コマを選択してください")
            return {"CANCELLED"}
        if not (0 <= page.active_panel_index < len(page.panels)):
            self.report({"WARNING"}, "コマを選択してください")
            return {"CANCELLED"}
        entry = page.panels[page.active_panel_index]

        # VIEW_3D の WINDOW リージョンを明示的に探して保持する.
        # N パネルのボタンから invoke すると context.region は UI リージョン
        # になるため rv3d が None で座標変換が全滅する。ここで確定させる。
        target = _find_view3d_window(context)
        if target is None:
            self.report({"ERROR"}, "3D ビューポートが見つかりません")
            return {"CANCELLED"}
        self._area, self._region, self._rv3d = target

        # overview モード中も active ページの grid offset を考慮して描画・
        # ヒット判定するため、overview 強制 OFF は行わない (計画書 Phase 1)。
        self._page_offset = _compute_page_offset(context)

        self._work = work
        self._page = page
        self._entry = entry
        self._original = self._snapshot(entry)
        self._hover = None  # ("vertex"|"edge", index) or None
        self._drag = None  # dict or None
        self._snap_guides: list[tuple[str, float]] = []  # [("x"|"y", value_mm)]

        # 描画ハンドラ登録 (POST_PIXEL — 2D ピクセル座標で描画)
        args = (self,)
        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, args, "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        self._tag_redraw(context)
        self.report({"INFO"}, "ドラッグで編集 | Enter で確定 | ESC でキャンセル")
        return {"RUNNING_MODAL"}

    # ---- スナップショット (キャンセル時の復元用) ----

    @staticmethod
    def _snapshot(entry) -> dict:
        return {
            "shape_type": entry.shape_type,
            "rect_x_mm": entry.rect_x_mm,
            "rect_y_mm": entry.rect_y_mm,
            "rect_width_mm": entry.rect_width_mm,
            "rect_height_mm": entry.rect_height_mm,
            "vertices": [(v.x_mm, v.y_mm) for v in entry.vertices],
        }

    @staticmethod
    def _restore(entry, snap: dict) -> None:
        entry.shape_type = snap["shape_type"]
        entry.rect_x_mm = snap["rect_x_mm"]
        entry.rect_y_mm = snap["rect_y_mm"]
        entry.rect_width_mm = snap["rect_width_mm"]
        entry.rect_height_mm = snap["rect_height_mm"]
        entry.vertices.clear()
        for x, y in snap["vertices"]:
            v = entry.vertices.add()
            v.x_mm = x
            v.y_mm = y

    # ---- modal ----

    def modal(self, context, event):
        try:
            # entry が削除された等の例外ケースで modal が暴走しないよう防御
            _ = self._entry.panel_stem  # 参照を生かす
        except Exception:  # noqa: BLE001
            self._cleanup(context)
            return {"CANCELLED"}

        # 起動リージョンが UI リージョンの場合 mouse_region_x/y は誤った座標
        # になるため、絶対座標 (event.mouse_x/y) から WINDOW リージョンの
        # 原点を引いて正規化する。
        def _to_window(ev):
            return ev.mouse_x - self._region.x, ev.mouse_y - self._region.y

        if event.type == "MOUSEMOVE":
            mx, my = _to_window(event)
            if self._drag is None:
                self._hover = self._hit_test(context, mx, my)
            else:
                self._update_drag(context, mx, my)
            self._tag_redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                mx, my = _to_window(event)
                hit = self._hit_test(context, mx, my)
                if hit is not None:
                    self._start_drag(context, mx, my, hit)
                    self._tag_redraw(context)
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE":
                if self._drag is not None:
                    self._drag = None
                    self._snap_guides = []
                    self._tag_redraw(context)
                return {"RUNNING_MODAL"}

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            self._restore(self._entry, self._original)
            self._cleanup(context)
            self._tag_redraw(context)
            self.report({"INFO"}, "キャンセル")
            return {"CANCELLED"}

        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            self._save_and_cleanup(context)
            self._tag_redraw(context)
            return {"FINISHED"}

        return {"PASS_THROUGH"}

    # ---- ヒットテスト ----

    def _hit_test(self, context, mx: float, my: float):
        region = self._region
        rv3d = self._rv3d
        if region is None or rv3d is None:
            return None
        entry = self._entry
        ox, oy = self._page_offset
        verts_mm = _panel_vertices_mm(entry)
        best = None
        best_dist2 = HANDLE_HIT_PX * HANDLE_HIT_PX

        # 頂点ハンドル (優先度高). entry はローカル座標、overview 時は offset 加算
        for i, (vx, vy) in enumerate(verts_mm):
            p = _mm_to_region_2d(region, rv3d, vx + ox, vy + oy)
            if p is None:
                continue
            d2 = (p.x - mx) ** 2 + (p.y - my) ** 2
            if d2 < best_dist2:
                best = ("vertex", i)
                best_dist2 = d2

        if best is not None:
            return best

        # 辺ハンドル (中点)
        n = len(verts_mm)
        for i in range(n):
            x1, y1 = verts_mm[i]
            x2, y2 = verts_mm[(i + 1) % n]
            p = _mm_to_region_2d(
                region, rv3d, (x1 + x2) / 2.0 + ox, (y1 + y2) / 2.0 + oy
            )
            if p is None:
                continue
            d2 = (p.x - mx) ** 2 + (p.y - my) ** 2
            if d2 < best_dist2:
                best = ("edge", i)
                best_dist2 = d2
        return best

    # ---- ドラッグ処理 ----

    def _start_drag(self, context, mx: float, my: float, hit) -> None:
        region = self._region
        rv3d = self._rv3d
        if region is None or rv3d is None:
            return
        pos = _region_2d_to_mm(region, rv3d, mx, my)
        if pos is None:
            return
        entry = self._entry
        verts_mm = _panel_vertices_mm(entry)
        kind, idx = hit
        self._drag = {
            "kind": kind,
            "index": idx,
            "start_pos_mm": pos,
            "orig_vertices": list(verts_mm),
            "orig_rect": (
                entry.rect_x_mm,
                entry.rect_y_mm,
                entry.rect_width_mm,
                entry.rect_height_mm,
            ) if entry.shape_type == "rect" else None,
        }

    def _update_drag(self, context, mx: float, my: float) -> None:
        region = self._region
        rv3d = self._rv3d
        if region is None or rv3d is None:
            return
        pos = _region_2d_to_mm(region, rv3d, mx, my)
        if pos is None:
            return
        drag = self._drag
        start_x, start_y = drag["start_pos_mm"]
        dx = pos[0] - start_x
        dy = pos[1] - start_y
        entry = self._entry
        xs_snap, ys_snap = _collect_snap_lines(self._work, self._page, entry)
        self._snap_guides = []

        if drag["kind"] == "vertex":
            self._apply_vertex_drag(entry, drag, dx, dy, xs_snap, ys_snap)
        else:
            self._apply_edge_drag(entry, drag, dx, dy, xs_snap, ys_snap)

    def _apply_vertex_drag(self, entry, drag, dx, dy, xs_snap, ys_snap) -> None:
        idx = drag["index"]
        orig = drag["orig_vertices"][idx]
        new_x = orig[0] + dx
        new_y = orig[1] + dy
        new_x, sx = _snap_value(new_x, xs_snap, SNAP_THRESHOLD_MM)
        new_y, sy = _snap_value(new_y, ys_snap, SNAP_THRESHOLD_MM)
        if sx is not None:
            self._snap_guides.append(("x", sx))
        if sy is not None:
            self._snap_guides.append(("y", sy))
        if entry.shape_type == "rect":
            self._set_rect_corner(entry, drag, idx, new_x, new_y)
        else:
            if 0 <= idx < len(entry.vertices):
                entry.vertices[idx].x_mm = new_x
                entry.vertices[idx].y_mm = new_y

    @staticmethod
    def _set_rect_corner(entry, drag, idx: int, new_x: float, new_y: float) -> None:
        """矩形コマの idx 番コーナーを (new_x, new_y) に。対角を固定。"""
        ox, oy, ow, oh = drag["orig_rect"]
        # コーナーインデックス: 0=BL, 1=BR, 2=TR, 3=TL (verts_mm と同じ順)
        # 対角を固定
        if idx == 0:  # BL — TR を固定
            fixed_x = ox + ow
            fixed_y = oy + oh
        elif idx == 1:  # BR — TL を固定
            fixed_x = ox
            fixed_y = oy + oh
        elif idx == 2:  # TR — BL を固定
            fixed_x = ox
            fixed_y = oy
        else:  # 3 == TL — BR を固定
            fixed_x = ox + ow
            fixed_y = oy
        x1 = min(new_x, fixed_x)
        x2 = max(new_x, fixed_x)
        y1 = min(new_y, fixed_y)
        y2 = max(new_y, fixed_y)
        entry.rect_x_mm = x1
        entry.rect_y_mm = y1
        entry.rect_width_mm = max(0.1, x2 - x1)
        entry.rect_height_mm = max(0.1, y2 - y1)

    def _apply_edge_drag(self, entry, drag, dx, dy, xs_snap, ys_snap) -> None:
        idx = drag["index"]
        if entry.shape_type == "rect":
            ox, oy, ow, oh = drag["orig_rect"]
            if idx == 0:  # 下辺 (BL-BR): Y のみ
                new_y = oy + dy
                new_y, s = _snap_value(new_y, ys_snap, SNAP_THRESHOLD_MM)
                if s is not None:
                    self._snap_guides.append(("y", s))
                top = oy + oh
                entry.rect_y_mm = min(new_y, top - 0.1)
                entry.rect_height_mm = top - entry.rect_y_mm
            elif idx == 2:  # 上辺 (TR-TL): Y のみ
                top = oy + oh + dy
                top, s = _snap_value(top, ys_snap, SNAP_THRESHOLD_MM)
                if s is not None:
                    self._snap_guides.append(("y", s))
                entry.rect_height_mm = max(0.1, top - oy)
                entry.rect_y_mm = oy
            elif idx == 1:  # 右辺 (BR-TR): X のみ
                right = ox + ow + dx
                right, s = _snap_value(right, xs_snap, SNAP_THRESHOLD_MM)
                if s is not None:
                    self._snap_guides.append(("x", s))
                entry.rect_width_mm = max(0.1, right - ox)
                entry.rect_x_mm = ox
            else:  # 3 == 左辺 (TL-BL): X のみ
                new_x = ox + dx
                new_x, s = _snap_value(new_x, xs_snap, SNAP_THRESHOLD_MM)
                if s is not None:
                    self._snap_guides.append(("x", s))
                right = ox + ow
                entry.rect_x_mm = min(new_x, right - 0.1)
                entry.rect_width_mm = right - entry.rect_x_mm
            return

        # polygon: 辺の両端頂点を平行移動
        orig_verts = drag["orig_vertices"]
        n = len(orig_verts)
        if n < 2:
            return
        i1 = idx
        i2 = (idx + 1) % n
        x1o, y1o = orig_verts[i1]
        x2o, y2o = orig_verts[i2]
        dx_edge = abs(x2o - x1o)
        dy_edge = abs(y2o - y1o)
        # 辺の主軸に沿ったスナップ (横辺なら Y、縦辺なら X、斜めは両方)
        adj_dx, adj_dy = dx, dy
        if dx_edge >= dy_edge:
            target = y1o + dy
            snapped, s = _snap_value(target, ys_snap, SNAP_THRESHOLD_MM)
            if s is not None:
                adj_dy = snapped - y1o
                self._snap_guides.append(("y", s))
        if dy_edge >= dx_edge:
            target = x1o + dx
            snapped, s = _snap_value(target, xs_snap, SNAP_THRESHOLD_MM)
            if s is not None:
                adj_dx = snapped - x1o
                self._snap_guides.append(("x", s))
        if 0 <= i1 < len(entry.vertices):
            entry.vertices[i1].x_mm = x1o + adj_dx
            entry.vertices[i1].y_mm = y1o + adj_dy
        if 0 <= i2 < len(entry.vertices):
            entry.vertices[i2].x_mm = x2o + adj_dx
            entry.vertices[i2].y_mm = y2o + adj_dy

    # ---- 保存 / 終了 ----

    def _save_and_cleanup(self, context) -> None:
        try:
            work = self._work
            page = self._page
            entry = self._entry
            work_dir = Path(work.work_dir) if work.work_dir else None
            if work_dir is not None:
                panel_io.save_panel_meta(work_dir, page.id, entry)
                page_io.save_page_json(work_dir, page)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_edit_vertices: save failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
        finally:
            self._cleanup(context)

    def _cleanup(self, context) -> None:
        handler = getattr(self, "_draw_handler", None)
        if handler is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
            except Exception:  # noqa: BLE001
                pass
            self._draw_handler = None

    def _tag_redraw(self, context) -> None:
        screen = getattr(context, "screen", None)
        if screen is None:
            return
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


# ---- 描画 (POST_PIXEL) ----


def _draw_callback(op: "BNAME_OT_panel_edit_vertices") -> None:
    """modal 中の頂点/辺ハンドルとスナップガイドを描画."""
    try:
        entry = op._entry
    except AttributeError:
        return
    if entry is None:
        return
    context = bpy.context
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return
    verts_mm = _panel_vertices_mm(entry)
    if not verts_mm:
        return

    # entry はローカル座標、overview 時は self._page_offset 加算で world 化
    ox, oy = getattr(op, "_page_offset", (0.0, 0.0))

    # 頂点・辺中点を 2D に変換
    verts_2d: list[tuple[float, float] | None] = []
    for x_mm, y_mm in verts_mm:
        p = _mm_to_region_2d(region, rv3d, x_mm + ox, y_mm + oy)
        verts_2d.append((p.x, p.y) if p is not None else None)

    n = len(verts_mm)
    edges_2d: list[tuple[float, float] | None] = []
    for i in range(n):
        x1, y1 = verts_mm[i]
        x2, y2 = verts_mm[(i + 1) % n]
        p = _mm_to_region_2d(
            region, rv3d, (x1 + x2) / 2.0 + ox, (y1 + y2) / 2.0 + oy
        )
        edges_2d.append((p.x, p.y) if p is not None else None)

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()

    # スナップガイド (画面全体を横切る線)
    for axis, val_mm in getattr(op, "_snap_guides", []):
        _draw_snap_line(shader, region, rv3d, axis, val_mm, context, (ox, oy))

    # 辺ハンドル
    for i, pt in enumerate(edges_2d):
        if pt is None:
            continue
        color = COLOR_EDGE_HOVER if op._hover == ("edge", i) else COLOR_EDGE
        if op._drag is not None and op._drag.get("kind") == "edge" and op._drag.get("index") == i:
            color = COLOR_VERTEX_ACTIVE
        _draw_square_px(shader, pt[0], pt[1], EDGE_HANDLE_SIZE_PX, color)

    # 頂点ハンドル (上に重ねる)
    for i, pt in enumerate(verts_2d):
        if pt is None:
            continue
        color = COLOR_VERTEX_HOVER if op._hover == ("vertex", i) else COLOR_VERTEX
        if op._drag is not None and op._drag.get("kind") == "vertex" and op._drag.get("index") == i:
            color = COLOR_VERTEX_ACTIVE
        _draw_square_px(shader, pt[0], pt[1], HANDLE_SIZE_PX, color)


def _draw_square_px(shader, cx: float, cy: float, size_px: float, color) -> None:
    half = size_px / 2.0
    verts = [
        (cx - half, cy - half),
        (cx + half, cy - half),
        (cx + half, cy + half),
        (cx - half, cy + half),
    ]
    indices = [(0, 1, 2), (0, 2, 3)]
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_snap_line(
    shader, region, rv3d, axis: str, val_mm: float, context, offset=(0.0, 0.0)
) -> None:
    """スナップ線をリージョン全体にわたって描画 (破線ではなく実線).

    ``val_mm`` は entry ローカル座標。``offset`` は overview モード時の
    ページ grid offset (mm)。val_mm + offset で world 座標として描画する。
    """
    ox, oy = offset
    # リージョンの 4 隅を mm に変換し、min/max を取って線を引く範囲を決める
    corners_px = [
        (0, 0),
        (region.width, 0),
        (region.width, region.height),
        (0, region.height),
    ]
    mms_x: list[float] = []
    mms_y: list[float] = []
    for px, py in corners_px:
        m = _region_2d_to_mm(region, rv3d, px, py)
        if m is None:
            return
        mms_x.append(m[0])
        mms_y.append(m[1])

    if axis == "x":
        y_min = min(mms_y)
        y_max = max(mms_y)
        p1 = _mm_to_region_2d(region, rv3d, val_mm + ox, y_min)
        p2 = _mm_to_region_2d(region, rv3d, val_mm + ox, y_max)
    else:
        x_min = min(mms_x)
        x_max = max(mms_x)
        p1 = _mm_to_region_2d(region, rv3d, x_min, val_mm + oy)
        p2 = _mm_to_region_2d(region, rv3d, x_max, val_mm + oy)
    if p1 is None or p2 is None:
        return
    verts = [(p1.x, p1.y), (p2.x, p2.y)]
    batch = batch_for_shader(shader, "LINES", {"pos": verts})
    shader.uniform_float("color", COLOR_SNAP_GUIDE)
    batch.draw(shader)


# ---- 登録 ----

_CLASSES = (BNAME_OT_panel_edit_vertices,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
