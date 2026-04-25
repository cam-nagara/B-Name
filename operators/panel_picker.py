"""ワールド座標 (mm) からページ/コマを逆引きするヘルパ.

overview モード中は全ページが grid 配置で描画されているため、ユーザーが
どのページのどのコマをクリックしたかを算出する。通常モード (単ページ
表示) では active ページのみを対象とする。

計画書 3. Phase 1 / 検索ヘルパ find_panel_at_world_mm の仕様 準拠。
"""

from __future__ import annotations

import bpy

from ..utils import log

_logger = log.get_logger(__name__)


def _hit_test_panel(entry, x_mm: float, y_mm: float) -> bool:
    """``entry`` のヒット判定。polygon は外接矩形で近似.

    rect: 矩形内であれば True。
    polygon: 外接矩形 (bounding box) 内であれば True。
    その他 (curve / freeform 等) は現段階では未対応 (False)。
    """
    shape = entry.shape_type
    if shape == "rect":
        return (
            entry.rect_x_mm <= x_mm <= entry.rect_x_mm + entry.rect_width_mm
            and entry.rect_y_mm <= y_mm <= entry.rect_y_mm + entry.rect_height_mm
        )
    if shape == "polygon":
        verts = entry.vertices
        if len(verts) < 3:
            return False
        xs = [v.x_mm for v in verts]
        ys = [v.y_mm for v in verts]
        return min(xs) <= x_mm <= max(xs) and min(ys) <= y_mm <= max(ys)
    return False


def _hit_test_page(page, x_mm: float, y_mm: float) -> int | None:
    """``page`` 内で (x_mm, y_mm) にヒットするコマの index を返す.

    同座標に複数コマが重なっている場合は Z 順最大 (最前面) を返す。
    ヒットしなければ None。
    """
    best_idx: int | None = None
    best_z: int | None = None
    for j, entry in enumerate(page.panels):
        if not _hit_test_panel(entry, x_mm, y_mm):
            continue
        z = int(entry.z_order)
        if best_idx is None or z > (best_z if best_z is not None else z - 1):
            best_idx = j
            best_z = z
    return best_idx


def find_panel_at_world_mm(
    work, x_mm: float, y_mm: float
) -> tuple[int, int] | None:
    """ワールド (mm) 座標から (page_index, panel_index) を解決.

    - overview_mode が False なら active ページのみ対象 (offset=0)
    - overview_mode が True なら全ページを grid offset 付きで走査
    - 同じ位置に複数コマが重なっていても 1 ページ内の Z 順最大のみを返す
      (ページ跨ぎの Z 比較は意味がないため、最初にヒットした「ページ内
      最前面」を採用)
    """
    if work is None or len(work.pages) == 0:
        return None
    scene = bpy.context.scene
    if scene is None:
        return None

    overview = bool(getattr(scene, "bname_overview_mode", False))

    if not overview:
        idx = work.active_page_index
        if not (0 <= idx < len(work.pages)):
            return None
        page = work.pages[idx]
        hit = _hit_test_page(page, x_mm, y_mm)
        return (idx, hit) if hit is not None else None

    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    cw = work.paper.canvas_width_mm
    ch = work.paper.canvas_height_mm
    for i, page in enumerate(work.pages):
        col = i % cols
        row = i // cols
        ox = -col * (cw + gap)  # 日本漫画は右→左展開 (負の X)
        oy = -row * (ch + gap)
        local_x = x_mm - ox
        local_y = y_mm - oy
        # キャンバス矩形の外は早期スキップ (パフォーマンス最適化)
        if not (0.0 <= local_x <= cw and 0.0 <= local_y <= ch):
            continue
        hit = _hit_test_page(page, local_x, local_y)
        if hit is not None:
            return (i, hit)
    return None
