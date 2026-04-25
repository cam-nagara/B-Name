"""overview 配置の grid transform 計算.

overlay 描画・panel_picker・ページ Collection transform がすべて同じ式で
page_index → (ox_mm, oy_mm) を導出する必要があるため、この 1 ファイルに
集約する。日本漫画は右→左読みのため、ページ 0001 が x=0 で以降は負の X
方向に展開される。

- 計算式は ``ui/overlay.py`` の overview 配置ロジックと一致させる
- 単位は mm (Blender unit 変換は 0.001 を掛ける)
"""

from __future__ import annotations

import bpy

from . import gpencil as gp_utils
from . import log
from .geom import mm_to_m

_logger = log.get_logger(__name__)


def page_grid_offset_mm(
    page_index: int,
    cols: int,
    gap_mm: float,
    canvas_width_mm: float,
    canvas_height_mm: float,
) -> tuple[float, float]:
    """``page_index`` の grid offset (mm) を返す (overlay と同一式)."""
    cols = max(1, int(cols))
    col = page_index % cols
    row = page_index // cols
    ox = -col * (canvas_width_mm + gap_mm)
    oy = -row * (canvas_height_mm + gap_mm)
    return (ox, oy)


def _resolve_overview_params(scene, work) -> tuple[int, float, float, float]:
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    cw = float(work.paper.canvas_width_mm)
    ch = float(work.paper.canvas_height_mm)
    return cols, gap, cw, ch


SUBPAGE_OFFSET_X_PROP = "bname_subpage_offset_x_mm"
SUBPAGE_OFFSET_Y_PROP = "bname_subpage_offset_y_mm"


def _obj_subpage_offset_mm(obj) -> tuple[float, float]:
    """``obj`` の subpage offset (mm) を custom property から取得.

    見開きページでは、左半分と右半分の 2 GP を同じ Collection に置くために
    右 GP に (canvas_width_mm, 0) の subpage offset を乗せる。
    custom property が無ければ (0.0, 0.0)。
    """
    try:
        ox = float(obj.get(SUBPAGE_OFFSET_X_PROP, 0.0))
        oy = float(obj.get(SUBPAGE_OFFSET_Y_PROP, 0.0))
        return ox, oy
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def apply_page_collection_transforms(context, work) -> int:
    """全ページ Collection の location を grid offset で再計算.

    戻り値: 更新した Collection 数。Collection が未生成のページはスキップ。
    overview モード設定に関わらず常に grid 配置で並べる (scene 内の
    物理座標は overview モードに依存しない)。

    per-object の subpage offset (見開きの右半分用) があれば grid offset に
    加算する。これにより見開きページで 2 GP (左/右) を正しい位置に並置できる。
    """
    scene = context.scene if context else bpy.context.scene
    if scene is None or work is None:
        return 0
    cols, gap, cw, ch = _resolve_overview_params(scene, work)
    updated = 0
    for i, page_entry in enumerate(work.pages):
        coll = gp_utils.get_page_collection(page_entry.id)
        if coll is None:
            continue
        ox_mm, oy_mm = page_grid_offset_mm(i, cols, gap, cw, ch)
        for obj in coll.objects:
            sub_x, sub_y = _obj_subpage_offset_mm(obj)
            new_offset = (mm_to_m(ox_mm + sub_x), mm_to_m(oy_mm + sub_y), 0.0)
            try:
                obj.location = new_offset
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "apply_page_collection_transforms: location set failed on %s",
                    obj.name,
                )
        updated += 1
    return updated


def page_index_at_world_mm(
    work, scene, x_mm: float, y_mm: float
) -> int | None:
    """world 座標 (mm) からページ index を逆引き (キャンバス矩形内のみ).

    overview 的 grid 配置を前提に、各ページのキャンバス矩形 [ox, ox+cw] x
    [oy, oy+ch] 内に座標が入っているかを確認する。入っていない場合は None。
    境界近傍のデッドゾーン処理は呼び出し側で行う。
    """
    if work is None or scene is None:
        return None
    cols, gap, cw, ch = _resolve_overview_params(scene, work)
    for i, _ in enumerate(work.pages):
        ox_mm, oy_mm = page_grid_offset_mm(i, cols, gap, cw, ch)
        if ox_mm <= x_mm <= ox_mm + cw and oy_mm <= y_mm <= oy_mm + ch:
            return i
    return None
