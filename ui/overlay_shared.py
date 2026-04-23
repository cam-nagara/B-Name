"""ビューポート描画 (blf+gpu) と書き出し (Pillow) で共有する計算ロジック.

原稿座標 (mm) での矩形計算のみを行い、描画コードは外で呼び出す。
これにより viewport_renderer と export_renderer で同じレイアウトを保証する
(計画書 3.8.4 参照)。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..utils.geom import Rect, canvas_rect, finish_rect, inner_frame_rect, safe_rect


@dataclass(frozen=True)
class PaperRects:
    canvas: Rect
    finish: Rect
    inner_frame: Rect
    safe: Rect


def compute_paper_rects(paper) -> PaperRects:
    return PaperRects(
        canvas=canvas_rect(paper),
        finish=finish_rect(paper),
        inner_frame=inner_frame_rect(paper),
        safe=safe_rect(paper),
    )


# ---------- 9 通り配置 → 矩形内ローカル座標 (mm) ----------

_ANCHOR_MAP: dict[str, tuple[float, float]] = {
    "top-left": (0.0, 1.0),
    "top-center": (0.5, 1.0),
    "top-right": (1.0, 1.0),
    "middle-left": (0.0, 0.5),
    "middle-center": (0.5, 0.5),
    "middle-right": (1.0, 0.5),
    "bottom-left": (0.0, 0.0),
    "bottom-center": (0.5, 0.0),
    "bottom-right": (1.0, 0.0),
}


def anchor_point(rect: Rect, position: str) -> tuple[float, float]:
    """矩形内で ``position`` (例 'bottom-left') のアンカー座標 (mm) を返す."""
    ax, ay = _ANCHOR_MAP.get(position, (0.0, 0.0))
    return (rect.x + rect.width * ax, rect.y + rect.height * ay)


# ---------- ノンブル配置 (位置+余白) ----------


def nombre_anchor(paper, nombre) -> tuple[float, float]:
    """ノンブルのアンカー座標 (基本枠基準で position + gap を適用)."""
    frame = inner_frame_rect(paper)
    ax, ay = anchor_point(frame, nombre.position)
    gx = nombre.gap_horizontal_mm
    gy = nombre.gap_vertical_mm
    # 上下・左右方向の符号調整
    if nombre.position.startswith("bottom"):
        ay -= gy
    elif nombre.position.startswith("top"):
        ay += gy
    if nombre.position.endswith("left"):
        ax -= gx
    elif nombre.position.endswith("right"):
        ax += gx
    return (ax, ay)


def format_nombre_text(nombre, page_number: int) -> str:
    """ノンブル format 文字列の {page} を置換."""
    try:
        return nombre.format.format(page=page_number)
    except (KeyError, IndexError, ValueError):
        # ユーザーが不正な format を入れた場合は単純にページ番号だけ返す
        return str(page_number)
