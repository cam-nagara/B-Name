"""ナイフツール: ビューポート上で切断線をドラッグしてコマを分割する modal オペレータ.

クリスタ相当の操作感:
- LMB ドラッグで切断線 (ラバーバンド) をプレビュー
- リリース時、線の向きで軸 (水平/垂直) を自動判定
  - |Δx| ≥ |Δy| → 水平に近い線 → **水平カット (上下分割)** (線が通過した Y 位置で分割)
  - |Δx| <  |Δy| → 垂直に近い線 → **垂直カット (左右分割)** (線が通過した X 位置で分割)
- 切断線は矩形コマの内側を通過している必要あり (通らない方向のカットはキャンセル)
- ESC / RMB: キャンセル

現状は矩形コマ限定 (polygon コマは panel_vertex_edit_op を使う)。
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
from ..utils import geom, log, paths

_logger = log.get_logger(__name__)


# ---- 定数 ----

COLOR_CUT_LINE = (1.0, 0.1, 0.1, 0.95)  # 赤
COLOR_CUT_AXIS_GUIDE = (1.0, 0.8, 0.0, 0.7)  # 黄 (軸ロック表示)


def _find_view3d_window(context):
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


def _region_to_mm(region, rv3d, mx, my) -> tuple[float, float] | None:
    loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
    if loc is None:
        return None
    return geom.m_to_mm(loc.x), geom.m_to_mm(loc.y)


def _hit_rect(x_mm: float, y_mm: float, rx, ry, rw, rh) -> bool:
    return rx <= x_mm <= rx + rw and ry <= y_mm <= ry + rh


class BNAME_OT_panel_knife_cut(Operator):
    """矩形コマをビューポート上のドラッグでカット (クリスタのナイフ相当)."""

    bl_idname = "bname.panel_knife_cut"
    bl_label = "ナイフツールでカット"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return (
            work is not None
            and work.loaded
            and context.area is not None
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
        if entry.shape_type != "rect":
            self.report({"WARNING"}, "矩形コマのみ対応です")
            return {"CANCELLED"}

        target = _find_view3d_window(context)
        if target is None:
            self.report({"ERROR"}, "3D ビューポートが見つかりません")
            return {"CANCELLED"}
        self._area, self._region, self._rv3d = target

        # overview_mode が ON でも grid offset を panel_picker が考慮するため
        # 自動 OFF は行わない (計画書 3. Phase 1: overview 全ページ対応)。

        self._work = work
        self._page = page
        self._entry = entry

        # 切断線の始点/終点 (画面 px 座標)
        self._p1_px: tuple[float, float] | None = None
        self._p2_px: tuple[float, float] | None = None
        self._dragging = False

        args = (self,)
        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, args, "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        self._tag_redraw()
        self.report(
            {"INFO"},
            "LMB でドラッグしてコマを横切る切断線を描いてください | ESC でキャンセル",
        )
        return {"RUNNING_MODAL"}

    def _to_window(self, ev):
        return ev.mouse_x - self._region.x, ev.mouse_y - self._region.y

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
        try:
            _ = self._entry.panel_stem  # 失効チェック
        except Exception:  # noqa: BLE001
            self._cleanup()
            return {"CANCELLED"}

        if event.type == "MOUSEMOVE":
            if self._dragging:
                self._p2_px = self._to_window(event)
                self._tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                self._p1_px = self._to_window(event)
                self._p2_px = self._p1_px
                self._dragging = True
                self._tag_redraw()
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE":
                if self._dragging and self._p1_px is not None and self._p2_px is not None:
                    ok = self._apply_cut()
                    self._cleanup()
                    return {"FINISHED"} if ok else {"CANCELLED"}
                return {"RUNNING_MODAL"}

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            self._cleanup()
            self.report({"INFO"}, "キャンセル")
            return {"CANCELLED"}

        return {"PASS_THROUGH"}

    def _apply_cut(self) -> bool:
        entry = self._entry
        work = self._work
        page = self._page
        region = self._region
        rv3d = self._rv3d
        p1 = _region_to_mm(region, rv3d, *self._p1_px)
        p2 = _region_to_mm(region, rv3d, *self._p2_px)
        if p1 is None or p2 is None:
            self.report({"WARNING"}, "座標変換失敗")
            return False
        (x1, y1), (x2, y2) = p1, p2

        # overview モード中は対象ページの grid offset を差し引いて
        # entry のローカル rect 座標と同じ系に揃える
        scene = bpy.context.scene
        if getattr(scene, "bname_overview_mode", False):
            page_idx = work.active_page_index
            cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
            gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
            cw = work.paper.canvas_width_mm
            ch = work.paper.canvas_height_mm
            col = page_idx % cols
            row = page_idx // cols
            ox = -col * (cw + gap)
            oy = -row * (ch + gap)
            x1 -= ox
            x2 -= ox
            y1 -= oy
            y2 -= oy

        dx, dy = x2 - x1, y2 - y1
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            self.report({"WARNING"}, "切断線が短すぎます")
            return False

        rx, ry, rw, rh = (
            entry.rect_x_mm, entry.rect_y_mm,
            entry.rect_width_mm, entry.rect_height_mm,
        )
        # 線の主軸で分割方向を判定
        # 線が横方向に伸びる (|dx| >= |dy|) → 水平カット (上下分割). 線が通過する Y で分割
        # 線が縦方向に伸びる → 垂直カット (左右分割). 線が通過する X で分割
        horizontal_cut = abs(dx) >= abs(dy)

        if horizontal_cut:
            # Y 位置: 始点と終点の中点の Y を採用 (矩形の上下端に clamp)
            cut_y = (y1 + y2) * 0.5
            if not (ry + 0.5 < cut_y < ry + rh - 0.5):
                self.report(
                    {"WARNING"},
                    "切断線がコマの内側を通っていません (水平カット)",
                )
                return False
            ratio = (cut_y - ry) / rh  # 下辺からの比率 (0.0 底辺 〜 1.0 上辺)
            axis = 0  # 水平カット
        else:
            cut_x = (x1 + x2) * 0.5
            if not (rx + 0.5 < cut_x < rx + rw - 0.5):
                self.report(
                    {"WARNING"},
                    "切断線がコマの内側を通っていません (垂直カット)",
                )
                return False
            ratio = (cut_x - rx) / rw  # 左辺からの比率 (0.0 左端 〜 1.0 右端)
            axis = 1  # 垂直カット

        # 既存の BNAME_OT_panel_cut ロジックを使うため、そのオペレータを直接呼ぶ
        # (execute のみ呼出。invoke ダイアログは出さない)
        ratio = max(0.05, min(0.95, ratio))
        try:
            result = bpy.ops.bname.panel_cut(
                "EXEC_DEFAULT", axis=axis, ratio=ratio,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_knife_cut: panel_cut ops failed")
            self.report({"ERROR"}, f"切断失敗: {exc}")
            return False
        if "FINISHED" not in result:
            self.report({"WARNING"}, f"切断 op が完了せず: {result}")
            return False
        kind = "水平" if horizontal_cut else "垂直"
        self.report({"INFO"}, f"{kind}カット (比率 {ratio:.2f})")
        return True


def _draw_callback(op: "BNAME_OT_panel_knife_cut") -> None:
    try:
        entry = op._entry
    except AttributeError:
        return
    if entry is None or not op._dragging:
        return
    if op._p1_px is None or op._p2_px is None:
        return

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()

    # 切断線本体 (赤)
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

    # 実際に適用される軸ガイド (黄の破線的実線をコマ矩形に重ねる)
    region = op._region
    rv3d = op._rv3d
    if region is None or rv3d is None:
        return
    x1, y1 = op._p1_px
    x2, y2 = op._p2_px
    horizontal_cut = abs(x2 - x1) >= abs(y2 - y1)

    def mm2px(x_mm, y_mm):
        p = location_3d_to_region_2d(
            region, rv3d, (geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0)
        )
        return (p.x, p.y) if p is not None else None

    rx, ry, rw, rh = (
        entry.rect_x_mm, entry.rect_y_mm,
        entry.rect_width_mm, entry.rect_height_mm,
    )

    # overview モード中はページ grid offset を加算して world 座標に戻す
    scene = bpy.context.scene
    ox_page = oy_page = 0.0
    if getattr(scene, "bname_overview_mode", False):
        work = op._work
        page_idx = work.active_page_index
        cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
        gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
        cw = work.paper.canvas_width_mm
        ch = work.paper.canvas_height_mm
        col = page_idx % cols
        row = page_idx // cols
        ox_page = -col * (cw + gap)
        oy_page = -row * (ch + gap)

    # 始点と終点の mm 中点を求めて、それを通る水平/垂直線を矩形全幅で描画
    mid_mm = _region_to_mm(region, rv3d, (x1 + x2) * 0.5, (y1 + y2) * 0.5)
    if mid_mm is None:
        return

    if horizontal_cut:
        cut_y = mid_mm[1]  # world mm
        p_left = mm2px(rx + ox_page, cut_y)
        p_right = mm2px(rx + rw + ox_page, cut_y)
        if p_left is not None and p_right is not None:
            verts = [p_left, p_right]
            batch = batch_for_shader(shader, "LINES", {"pos": verts})
            shader.uniform_float("color", COLOR_CUT_AXIS_GUIDE)
            batch.draw(shader)
    else:
        cut_x = mid_mm[0]  # world mm
        p_bot = mm2px(cut_x, ry + oy_page)
        p_top = mm2px(cut_x, ry + rh + oy_page)
        if p_bot is not None and p_top is not None:
            verts = [p_bot, p_top]
            batch = batch_for_shader(shader, "LINES", {"pos": verts})
            shader.uniform_float("color", COLOR_CUT_AXIS_GUIDE)
            batch.draw(shader)


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
