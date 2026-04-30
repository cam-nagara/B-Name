"""overview 配置の grid transform 計算.

overlay 描画・coma_picker・ページ Collection transform がすべて同じ式で
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


def _logical_slot_index(
    page_index: int,
    start_side: str = "right",
    read_direction: str = "left",
) -> int:
    """見開きスロット index (= 「1 ページ目の逆側の空白」分の補正込み).

    1 ページ目だけは ``start_side`` と ``read_direction`` の組み合わせに応じて、
    物理的な左/右位置が期待どおりになるスロットへ置く。反対側は空白扱いにし、
    2 ページ目以降は常に ``page_index + 1`` へ進める。
    """
    if read_direction == "down":
        return max(0, int(page_index))
    if page_index <= 0:
        first_page_is_slot0 = (
            (start_side == "right" and read_direction == "left")
            or (start_side == "left" and read_direction == "right")
        )
        return 0 if first_page_is_slot0 else 1
    return page_index + 1


def is_left_half_page(page_index: int, start_side: str = "right",
                      read_direction: str = "left") -> bool:
    """そのページが見開きペアの「物理左半分」かを返す.

    ``page_grid_offset_mm`` で計算される X 軸位置に基づき、ペア (col=偶, col=奇)
    の中で物理的に X が小さい側 (= 画面左) のページに True を返す。

    ペア内の物理左右は ``read_direction`` で決まる:
      - "right" (西洋本): col 増加 = 画面右へ進む → c=0 が物理左、c=1 が物理右
      - "left"  (日本マンガ): col 増加 = 画面左へ進む → c=0 が物理右、c=1 が物理左

    例: 日本マンガ (start_side="right", read_direction="left") の場合
      - page 1 (slot 0, c=0): 物理右 = 単独右ページ
      - page 2 (slot 2, c=0): 物理右 = 次の見開きの右ページ
      - page 3 (slot 3, c=1): 物理左 = 次の見開きの左ページ
    """
    if read_direction == "down":
        return False
    slot = _logical_slot_index(page_index, start_side, read_direction)
    c_in_pair = slot % 2
    if read_direction == "right":
        return c_in_pair == 0
    return c_in_pair == 1


def page_grid_offset_mm(
    page_index: int,
    cols: int,
    gap_mm: float,
    canvas_width_mm: float,
    canvas_height_mm: float,
    start_side: str = "right",
    read_direction: str = "left",
) -> tuple[float, float]:
    """``page_index`` の grid offset (mm) を返す.

    見開きペアロジック:
      - 「論理スロット」を start_side で補正 (1 ページ目の単独ページの逆側に
        空白スロットを 1 つ置く)
      - 偶スロット (左半分) と次の奇スロット (右半分) で見開きペア
      - ペア内: 隙間 0 (密着)
      - ペア間: gap_mm 隙間あり

    read_direction:
      - "left":  X が負方向に進む (col が増えるほど左へ) — 日本マンガ既定
      - "right": X が正方向に進む — 西洋本
      - "down":  すべて X=0 で Y のみ進む (縦スクロール)。cols は無視。
    """
    slot = _logical_slot_index(page_index, start_side, read_direction)
    return slot_grid_offset_mm(
        slot,
        cols,
        gap_mm,
        canvas_width_mm,
        canvas_height_mm,
        read_direction,
    )


def slot_grid_offset_mm(
    slot: int,
    cols: int,
    gap_mm: float,
    canvas_width_mm: float,
    canvas_height_mm: float,
    read_direction: str = "left",
) -> tuple[float, float]:
    """見開き補正後の論理 slot から grid offset (mm) を返す."""
    cols = max(1, int(cols))
    if read_direction == "down":
        # 縦スクロール: 全ページが 1 列に並ぶ。見開きの概念は無視。
        return (0.0, -int(slot) * (canvas_height_mm + gap_mm))

    col = slot % cols
    row = slot // cols
    # X 方向は「列 c=0..col-1 の幅 + ペア境界 gap」を累積
    # ペア境界: c 番目スロットが偶数 (= 左半分の終わり) の次は奇数 (右半分の始まり)
    #          → c が奇数 (右半分) の次 c+1 (= 左半分の頭) で gap
    x_total = 0.0
    for c in range(col):
        x_total += canvas_width_mm
        # c 番目 → c+1 番目で gap が入るのは「c が奇数 (= 右半分) の次」
        if c % 2 == 1:
            x_total += gap_mm
    # read_direction で符号を決定
    sign = -1.0 if read_direction == "left" else 1.0
    ox = sign * x_total
    oy = -row * (canvas_height_mm + gap_mm)
    return (ox, oy)


def page_manual_offset_mm(page_entry) -> tuple[float, float]:
    """ページエントリに保存された手動移動量 (mm) を返す."""
    if page_entry is None:
        return 0.0, 0.0
    try:
        return (
            float(getattr(page_entry, "offset_x_mm", 0.0)),
            float(getattr(page_entry, "offset_y_mm", 0.0)),
        )
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def page_total_offset_mm(
    work,
    scene,
    page_index: int,
) -> tuple[float, float]:
    """grid 配置とページ手動移動量を合成した offset (mm) を返す."""
    if work is None or scene is None or not (0 <= page_index < len(work.pages)):
        return 0.0, 0.0
    cols, gap, cw, ch = _resolve_overview_params(scene, work)
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    ox_mm, oy_mm = page_grid_offset_mm(
        page_index, cols, gap, cw, ch, start_side, read_direction
    )
    add_x, add_y = page_manual_offset_mm(work.pages[page_index])
    return ox_mm + add_x, oy_mm + add_y


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


# Grease Pencil オブジェクトを用紙より手前 (z>0) に配置するためのオフセット (m).
# 紙塗りは overlay (z=0 平面) に描画されるため、GP がそれより上に乗らないと
# Solid 描画の Z 順で GP が紙の背面になり「線が消える」現象が発生する。
# 0.001 (= 1 mm) では近すぎてレイヤー間の差が判別しづらいため、
# 0.1 刻み (= 100mm) に統一。
GP_Z_LIFT_M = 0.1


def apply_page_collection_transforms(context, work) -> int:
    """全ページ Collection の location を grid offset で再計算.

    戻り値: 更新した Collection 数。Collection が未生成のページはスキップ。
    overview モード設定に関わらず常に grid 配置で並べる (scene 内の
    物理座標は overview モードに依存しない)。

    per-object の subpage offset (見開きの右半分用) があれば grid offset に
    加算する。これにより見開きページで 2 GP (左/右) を正しい位置に並置できる。

    GP オブジェクトは ``GP_Z_LIFT_M`` (+1mm) 持ち上げて配置し、用紙塗り
    (z=0) より確実に手前に来るようにする。
    """
    scene = context.scene if context else bpy.context.scene
    if scene is None or work is None:
        return 0
    cols, gap, cw, ch = _resolve_overview_params(scene, work)
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    updated = 0
    for i, page_entry in enumerate(work.pages):
        coll = gp_utils.get_page_collection(page_entry.id)
        if coll is None:
            continue
        ox_mm, oy_mm = page_grid_offset_mm(
            i, cols, gap, cw, ch, start_side, read_direction
        )
        add_x, add_y = page_manual_offset_mm(page_entry)
        ox_mm += add_x
        oy_mm += add_y
        for obj in coll.objects:
            sub_x, sub_y = _obj_subpage_offset_mm(obj)
            # GP は用紙 (z=0) より手前に持ち上げる
            z = GP_Z_LIFT_M if obj.type == "GREASEPENCIL" else 0.0
            new_offset = (mm_to_m(ox_mm + sub_x), mm_to_m(oy_mm + sub_y), z)
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
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    from . import page_range

    for i, page in enumerate(work.pages):
        if not page_range.page_in_range(page):
            continue
        ox_mm, oy_mm = page_grid_offset_mm(
            i, cols, gap, cw, ch, start_side, read_direction
        )
        add_x, add_y = page_manual_offset_mm(work.pages[i])
        ox_mm += add_x
        oy_mm += add_y
        if ox_mm <= x_mm <= ox_mm + cw and oy_mm <= y_mm <= oy_mm + ch:
            return i
    return None
