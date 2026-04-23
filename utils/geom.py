"""幾何計算ユーティリティ.

mm → Blender 座標 (m) 変換、ピクセル→mm、矩形計算など。
B-Name は原稿座標を mm で扱うが、Blender ビューポートは Blender unit
(メートル既定) で描画する。座標系変換は 1 mm = 0.001 m として扱う。
"""

from __future__ import annotations

from dataclasses import dataclass

MM_PER_M = 1000.0
INCH_PER_MM = 1.0 / 25.4
PT_PER_INCH = 72.0
PT_PER_MM = PT_PER_INCH * INCH_PER_MM  # ≈ 2.8346


@dataclass(frozen=True)
class Rect:
    """(x, y) 左下基準の矩形 (単位 mm)."""

    x: float
    y: float
    width: float
    height: float

    @property
    def x2(self) -> float:
        return self.x + self.width

    @property
    def y2(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    def inset(self, dx: float, dy: float | None = None) -> "Rect":
        """上下左右へ dx/dy だけ内側に縮めた矩形."""
        if dy is None:
            dy = dx
        return Rect(self.x + dx, self.y + dy, self.width - dx * 2, self.height - dy * 2)

    def inset_each(self, top: float, bottom: float, left: float, right: float) -> "Rect":
        """辺ごとに異なる値で内側に縮めた矩形."""
        return Rect(
            self.x + left,
            self.y + bottom,
            self.width - left - right,
            self.height - top - bottom,
        )


def mm_to_m(mm: float) -> float:
    return mm / MM_PER_M


def m_to_mm(m: float) -> float:
    return m * MM_PER_M


def mm_to_px(mm: float, dpi: int) -> float:
    return mm * INCH_PER_MM * dpi


def px_to_mm(px: float, dpi: int) -> float:
    return px / dpi / INCH_PER_MM


def pt_to_mm(pt: float) -> float:
    return pt / PT_PER_MM


def mm_to_pt(mm: float) -> float:
    return mm * PT_PER_MM


# ---------- 用紙座標計算 ----------


def canvas_rect(paper) -> Rect:
    """キャンバス全体の矩形 (左下基準、mm)."""
    return Rect(0.0, 0.0, paper.canvas_width_mm, paper.canvas_height_mm)


def finish_rect(paper) -> Rect:
    """仕上がり枠の矩形 (キャンバス中央配置)."""
    cw, ch = paper.canvas_width_mm, paper.canvas_height_mm
    fw, fh = paper.finish_width_mm, paper.finish_height_mm
    return Rect((cw - fw) / 2.0, (ch - fh) / 2.0, fw, fh)


def inner_frame_rect(paper) -> Rect:
    """基本枠 (内枠) の矩形."""
    cw, ch = paper.canvas_width_mm, paper.canvas_height_mm
    iw, ih = paper.inner_frame_width_mm, paper.inner_frame_height_mm
    ox, oy = paper.inner_frame_offset_x_mm, paper.inner_frame_offset_y_mm
    return Rect((cw - iw) / 2.0 + ox, (ch - ih) / 2.0 + oy, iw, ih)


def safe_rect(paper) -> Rect:
    """セーフライン (天/地/ノド/小口) の内側矩形.

    ノド/小口は左右どちらかを示すが、見開きでなければ対称に扱う。
    Phase 1 段階では単一ページ前提で、ノド=左、小口=右として実装。
    """
    canvas = canvas_rect(paper)
    return canvas.inset_each(
        top=paper.safe_top_mm,
        bottom=paper.safe_bottom_mm,
        left=paper.safe_gutter_mm,
        right=paper.safe_fore_edge_mm,
    )
