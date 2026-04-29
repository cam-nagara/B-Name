"""ビューポート reparent (Alt 系) 操作中に出すドロップインジケーター GPU 描画.

`set_state` で描画する内容を更新し、`clear_state` で非表示に戻す。
draw_handler は addon register 時に常時登録され、状態が空のときは何も描画しない。

状態の種類:
- ``hover_target``: Alt+ドラッグ中のホバー先 (コマ枠 + ページ枠 を実線シアンで縁取り)
- ``confirm_target``: Alt+クリック確定の演出用 (短時間パルス点滅)
- ``error_target``: 不可な操作 (赤で点滅)
- ``preview_card``: ドラッグ中の半透明レイヤーカードプレビュー
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..utils import log
from ..utils.layer_hierarchy import coma_polygon, page_stack_key

_logger = log.get_logger(__name__)


# ---------- 状態 ----------


@dataclass
class _OverlayState:
    """描画する内容を保持するシングルトン."""

    # ホバー中のターゲット (Alt+ドラッグ中)
    hover_kind: str = ""  # "coma" | "page" | "outside" | ""
    hover_page_id: str = ""
    hover_coma_id: str = ""
    hover_page_index: int = -1

    # 確定演出 (短時間パルス)
    confirm_kind: str = ""
    confirm_page_id: str = ""
    confirm_coma_id: str = ""
    confirm_page_index: int = -1
    confirm_until: float = 0.0  # time.monotonic() + 0.3

    # エラー演出 (短時間赤点滅)
    error_kind: str = ""
    error_page_id: str = ""
    error_coma_id: str = ""
    error_page_index: int = -1
    error_until: float = 0.0

    # 半透明プレビューカード (ドラッグ中の追従)
    preview_visible: bool = False
    preview_world_xy_mm: Optional[tuple[float, float]] = None
    preview_label: str = ""
    preview_count: int = 0


_state = _OverlayState()
_handle: Optional[object] = None


# ---------- 公開 API ----------


def set_hover(kind: str, *, page_id: str = "", coma_id: str = "", page_index: int = -1) -> None:
    _state.hover_kind = str(kind or "")
    _state.hover_page_id = str(page_id or "")
    _state.hover_coma_id = str(coma_id or "")
    _state.hover_page_index = int(page_index)
    _tag_redraw_all()


def clear_hover() -> None:
    set_hover("")


def flash_confirm(kind: str, *, page_id: str = "", coma_id: str = "", page_index: int = -1, duration: float = 0.3) -> None:
    _state.confirm_kind = str(kind or "")
    _state.confirm_page_id = str(page_id or "")
    _state.confirm_coma_id = str(coma_id or "")
    _state.confirm_page_index = int(page_index)
    _state.confirm_until = time.monotonic() + max(0.0, float(duration))
    _tag_redraw_all()


def flash_error(kind: str, *, page_id: str = "", coma_id: str = "", page_index: int = -1, duration: float = 0.3) -> None:
    _state.error_kind = str(kind or "")
    _state.error_page_id = str(page_id or "")
    _state.error_coma_id = str(coma_id or "")
    _state.error_page_index = int(page_index)
    _state.error_until = time.monotonic() + max(0.0, float(duration))
    _tag_redraw_all()


def set_preview(*, world_xy_mm: Optional[tuple[float, float]], label: str = "", count: int = 0) -> None:
    _state.preview_visible = world_xy_mm is not None
    _state.preview_world_xy_mm = world_xy_mm
    _state.preview_label = str(label or "")
    _state.preview_count = int(count)
    _tag_redraw_all()


def clear_preview() -> None:
    set_preview(world_xy_mm=None)


def clear_all() -> None:
    _state.hover_kind = ""
    _state.confirm_until = 0.0
    _state.error_until = 0.0
    _state.preview_visible = False
    _state.preview_world_xy_mm = None
    _tag_redraw_all()


# ---------- 描画 ----------


def _tag_redraw_all() -> None:
    wm = bpy.context.window_manager if bpy.context else None
    if wm is None:
        return
    for window in getattr(wm, "windows", []):
        for area in getattr(window.screen, "areas", []):
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _resolve_page(work, page_id: str):
    if work is None or not page_id:
        return None, -1
    for i, page in enumerate(work.pages):
        if page_stack_key(page) == page_id:
            return page, i
    return None, -1


def _resolve_panel(page, coma_id: str):
    if page is None or not coma_id:
        return None
    for panel in getattr(page, "comas", []):
        if str(getattr(panel, "coma_id", "") or "") == coma_id:
            return panel
    return None


def _world_polygon_for_page(work, scene, page, page_index: int) -> list[tuple[float, float]]:
    from ..utils import page_grid

    if page is None or work is None:
        return []
    paper = work.paper
    cw = float(getattr(paper, "canvas_width_mm", 0.0))
    ch = float(getattr(paper, "canvas_height_mm", 0.0))
    ox, oy = page_grid.page_total_offset_mm(work, scene, page_index)
    return [
        (ox, oy),
        (ox + cw, oy),
        (ox + cw, oy + ch),
        (ox, oy + ch),
    ]


def _world_polygon_for_coma(work, scene, page, page_index: int, panel) -> list[tuple[float, float]]:
    from ..utils import page_grid

    if panel is None or page is None or work is None:
        return []
    poly = coma_polygon(panel)
    if not poly:
        return []
    ox, oy = page_grid.page_total_offset_mm(work, scene, page_index)
    return [(x + ox, y + oy) for (x, y) in poly]


def _mm_to_world_units(poly_mm: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """mm → Blender unit (m) に変換し、z=0.0 の 3D 座標に."""
    from ..utils import geom

    return [(geom.mm_to_m(x), geom.mm_to_m(y), 0.0) for (x, y) in poly_mm]


def _draw_polygon_fill(poly3d: list[tuple[float, float, float]], color: tuple[float, float, float, float]) -> None:
    if len(poly3d) < 3:
        return
    # 三角形ファン
    indices = [(0, i, i + 1) for i in range(1, len(poly3d) - 1)]
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRIS", {"pos": poly3d}, indices=indices)
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.blend_set("NONE")


def _draw_polygon_outline(poly3d: list[tuple[float, float, float]], color: tuple[float, float, float, float], width: float) -> None:
    if len(poly3d) < 2:
        return
    closed = poly3d + [poly3d[0]]
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": closed})
    region = bpy.context.region
    rw = float(getattr(region, "width", 1.0)) if region is not None else 1.0
    rh = float(getattr(region, "height", 1.0)) if region is not None else 1.0
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", color)
    shader.uniform_float("lineWidth", float(width))
    shader.uniform_float("viewportSize", (rw, rh))
    batch.draw(shader)
    gpu.state.blend_set("NONE")


def _draw_target(kind: str, page_id: str, coma_id: str, page_index: int, *, color_fill, color_line, line_width: float) -> None:
    from ..core.work import get_work

    context = bpy.context
    work = get_work(context)
    scene = context.scene
    if work is None or scene is None:
        return
    page, resolved_index = _resolve_page(work, page_id)
    if page_index < 0:
        page_index = resolved_index
    if page is None or page_index < 0:
        return
    if kind == "coma":
        panel = _resolve_panel(page, coma_id)
        if panel is None:
            return
        poly = _world_polygon_for_coma(work, scene, page, page_index, panel)
    elif kind == "page":
        poly = _world_polygon_for_page(work, scene, page, page_index)
    else:
        return
    poly3d = _mm_to_world_units(poly)
    _draw_polygon_fill(poly3d, color_fill)
    _draw_polygon_outline(poly3d, color_line, line_width)


def _pulse_alpha(until: float, base: float = 0.4) -> float:
    """until までの残り時間で alpha を計算。0.3 秒で base→0 にフェード."""
    now = time.monotonic()
    if now >= until:
        return 0.0
    remain = until - now
    # 0.3s の最初は base, 終わりは 0 で線形フェード
    return max(0.0, min(1.0, base * remain / 0.3))


def _draw_hover() -> None:
    if _state.hover_kind not in {"coma", "page"}:
        return
    color_fill = (0.2, 0.8, 1.0, 0.10)  # シアン半透明
    color_line = (0.2, 0.9, 1.0, 0.95)  # シアン実線
    _draw_target(
        _state.hover_kind,
        _state.hover_page_id,
        _state.hover_coma_id,
        _state.hover_page_index,
        color_fill=color_fill,
        color_line=color_line,
        line_width=3.0,
    )


def _draw_confirm() -> None:
    alpha = _pulse_alpha(_state.confirm_until, base=0.5)
    if alpha <= 0.0 or _state.confirm_kind not in {"coma", "page"}:
        return
    color_fill = (0.3, 1.0, 0.5, alpha * 0.4)  # ライム
    color_line = (0.3, 1.0, 0.5, alpha)
    _draw_target(
        _state.confirm_kind,
        _state.confirm_page_id,
        _state.confirm_coma_id,
        _state.confirm_page_index,
        color_fill=color_fill,
        color_line=color_line,
        line_width=4.0,
    )


def _draw_error() -> None:
    alpha = _pulse_alpha(_state.error_until, base=0.6)
    if alpha <= 0.0 or _state.error_kind not in {"coma", "page"}:
        return
    color_fill = (1.0, 0.3, 0.3, alpha * 0.3)
    color_line = (1.0, 0.3, 0.3, alpha)
    _draw_target(
        _state.error_kind,
        _state.error_page_id,
        _state.error_coma_id,
        _state.error_page_index,
        color_fill=color_fill,
        color_line=color_line,
        line_width=4.0,
    )


def _draw_preview_card() -> None:
    if not _state.preview_visible or _state.preview_world_xy_mm is None:
        return
    from ..utils import geom

    wx, wy = _state.preview_world_xy_mm
    # カーソル位置に小さい矩形プレビュー (固定 mm サイズ)
    half_w = 8.0
    half_h = 5.0
    poly_mm = [
        (wx - half_w, wy - half_h),
        (wx + half_w, wy - half_h),
        (wx + half_w, wy + half_h),
        (wx - half_w, wy + half_h),
    ]
    poly3d = _mm_to_world_units(poly_mm)
    _draw_polygon_fill(poly3d, (1.0, 1.0, 1.0, 0.3))
    _draw_polygon_outline(poly3d, (1.0, 1.0, 1.0, 0.85), 2.0)


def _draw_callback() -> None:
    try:
        _draw_hover()
        _draw_confirm()
        _draw_error()
        _draw_preview_card()
    except Exception:  # noqa: BLE001
        _logger.exception("reparent overlay draw failed")


# ---------- register / unregister ----------


def register() -> None:
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (), "WINDOW", "POST_VIEW"
        )
    _logger.debug("reparent_overlay draw_handler registered")


def unregister() -> None:
    global _handle
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        except (ValueError, RuntimeError):
            pass
        _handle = None
    clear_all()
