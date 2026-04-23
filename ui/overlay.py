"""ビューポート上の原稿オーバーレイ描画 (draw_handler_add + gpu).

計画書 3.4.3a に従い、以下を gpu + blf でオーバーレイ描画する:
- キャンバス (用紙) 枠
- 仕上がり枠 / 基本枠 / セーフライン枠
- セーフライン外側オーバーレイ (乗算)
- ノンブル / 作品情報 (blf)

書き出し結果には焼き込まれない (書き出し時は export_renderer が同じ
overlay_shared ロジックを Pillow で再実装する、Phase 6 で実装)。

座標系:
- 原稿座標は mm 基準 (キャンバス左下が原点)
- Blender ビューポートへの描画は 3D ワールド空間の XY 平面上 (z=0)
- 1 mm = 0.001 Blender unit で配置。カメラリグは Phase 2 で実装予定のため、
  現段階ではワールド XY 平面への配置のみで、カメラがこの平面を写す想定。
"""

from __future__ import annotations

from typing import Optional

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..core.work import get_work
from ..utils import log
from ..utils.geom import Rect, mm_to_m
from . import overlay_shared

_logger = log.get_logger(__name__)

# draw_handler_add の戻り値 (ハンドラ識別子)
_handle: Optional[object] = None


# ---------- 低レベル描画ヘルパ ----------


def _draw_rect_fill(rect: Rect, color: tuple[float, float, float, float]) -> None:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [
        (mm_to_m(rect.x), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y2), 0.0),
        (mm_to_m(rect.x), mm_to_m(rect.y2), 0.0),
    ]
    indices = [(0, 1, 2), (0, 2, 3)]
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_rect_outline(
    rect: Rect,
    color: tuple[float, float, float, float],
    line_width: float = 1.0,
) -> None:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [
        (mm_to_m(rect.x), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y2), 0.0),
        (mm_to_m(rect.x), mm_to_m(rect.y2), 0.0),
        (mm_to_m(rect.x), mm_to_m(rect.y), 0.0),
    ]
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    try:
        gpu.state.line_width_set(max(1.0, float(line_width)))
        batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.0)


def _draw_frame_with_hole(outer: Rect, inner: Rect, color: tuple[float, float, float, float]) -> None:
    """外側 outer を塗って内側 inner を穴抜きした「額縁」形状を描画."""
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    # 4 本の帯で外周を塗る (上下左右)
    top = Rect(outer.x, inner.y2, outer.width, outer.y2 - inner.y2)
    bottom = Rect(outer.x, outer.y, outer.width, inner.y - outer.y)
    left = Rect(outer.x, inner.y, inner.x - outer.x, inner.height)
    right = Rect(inner.x2, inner.y, outer.x2 - inner.x2, inner.height)
    verts: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []
    for r in (top, bottom, left, right):
        if r.width <= 0 or r.height <= 0:
            continue
        base = len(verts)
        verts.extend(
            [
                (mm_to_m(r.x), mm_to_m(r.y), 0.0),
                (mm_to_m(r.x2), mm_to_m(r.y), 0.0),
                (mm_to_m(r.x2), mm_to_m(r.y2), 0.0),
                (mm_to_m(r.x), mm_to_m(r.y2), 0.0),
            ]
        )
        indices.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])
    if not verts:
        return
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


# ---------- draw_handler 本体 ----------


def _apply_blend_mode(_mode: str) -> None:
    """ブレンドモード指定を GPU state に反映する (Phase 1 暫定).

    GPU state API の ``blend_set`` には MULTIPLY 相当が無く、現段階では
    すべて ALPHA ブレンドにフォールバックする。正確な乗算合成は Phase 6 の
    書き出しパイプライン (Pillow) で実装する。
    """
    gpu.state.blend_set("ALPHA")


def _draw_callback() -> None:
    context = bpy.context
    work = get_work(context)
    if work is None or not work.loaded:
        return
    paper = work.paper
    rects = overlay_shared.compute_paper_rects(paper)

    gpu.state.blend_set("ALPHA")
    try:
        # キャンバス薄塗り (視認用、ペーパーカラー)
        canvas_color = (
            float(paper.paper_color[0]),
            float(paper.paper_color[1]),
            float(paper.paper_color[2]),
            0.25,  # 背景として薄く表示
        )
        _draw_rect_fill(rects.canvas, canvas_color)

        # セーフライン外オーバーレイ (表示専用、書き出しに含まれない)
        sa = work.safe_area_overlay
        if sa.enabled:
            _apply_blend_mode(sa.blend_mode)
            color = (float(sa.color[0]), float(sa.color[1]), float(sa.color[2]), float(sa.opacity))
            _draw_frame_with_hole(rects.canvas, rects.safe, color)
            gpu.state.blend_set("ALPHA")

        # 枠線群
        _draw_rect_outline(rects.canvas, (0.4, 0.4, 0.4, 0.8), line_width=1.0)
        _draw_rect_outline(rects.finish, (0.8, 0.2, 0.2, 0.9), line_width=1.5)  # 仕上がり=赤
        _draw_rect_outline(rects.inner_frame, (0.2, 0.6, 0.9, 0.9), line_width=1.0)  # 基本枠=青
        _draw_rect_outline(rects.safe, (0.2, 0.8, 0.4, 0.6), line_width=1.0)  # セーフ=緑
    finally:
        gpu.state.blend_set("NONE")


# ---------- register / unregister ----------


def register() -> None:
    global _handle
    if _handle is not None:
        return
    _handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_callback, (), "WINDOW", "POST_VIEW"
    )
    _logger.debug("overlay draw_handler registered")


def unregister() -> None:
    global _handle
    if _handle is None:
        return
    try:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
    except (ValueError, RuntimeError):
        pass
    _handle = None
    _logger.debug("overlay draw_handler removed")
