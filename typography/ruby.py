"""ルビ配置 (モノルビ/グループルビ/熟語ルビ).

計画書 3.1.5 参照。親文字配置 (layout.typeset の結果) に対して、ルビ
スパン (TextEntry.ruby_spans) を元にルビ文字の座標を計算する。
"""

from __future__ import annotations

from dataclasses import dataclass

from .layout import GlyphPlacement


@dataclass(frozen=True)
class RubyPlacement:
    ch: str
    x_mm: float
    y_mm: float
    size_pt: float


def compute_ruby_placements(
    parent_glyphs: list[GlyphPlacement],
    ruby_spans,
    ruby_size_ratio: float = 0.5,
    ruby_offset_mm: float = 0.3,
) -> list[RubyPlacement]:
    """親文字の配置とルビスパンからルビ座標を計算.

    縦書き前提: ルビは親文字の右側 (X+) に小さく並ぶ。横書きなら上側 (Y+)。
    MVP では縦書きのみ対応 (書字方向は呼出側で区別する)。
    """
    out: list[RubyPlacement] = []
    for span in ruby_spans:
        start = span.start
        length = max(1, span.length)
        end = start + length
        if start >= len(parent_glyphs):
            continue
        covered = parent_glyphs[start : min(end, len(parent_glyphs))]
        if not covered:
            continue
        ruby_text = span.ruby_text
        if not ruby_text:
            continue
        parent_size = covered[0].size_pt
        ruby_size = parent_size * ruby_size_ratio
        # 親文字列の中心 Y (縦書き時)
        avg_y = sum(g.y_mm for g in covered) / len(covered)
        parent_x = covered[0].x_mm
        ruby_x = parent_x + (parent_size * 0.5 / 72.0 * 25.4) + ruby_offset_mm
        # ルビは親範囲に均等に配置 (グループルビ前提)
        count = len(ruby_text)
        if count == 0:
            continue
        top_y = max(g.y_mm for g in covered)
        bottom_y = min(g.y_mm for g in covered)
        total = top_y - bottom_y
        step = total / max(1, count - 1) if count > 1 else 0.0
        for i, rch in enumerate(ruby_text):
            ry = top_y - step * i if count > 1 else (top_y + bottom_y) / 2
            out.append(RubyPlacement(ch=rch, x_mm=ruby_x, y_mm=ry, size_pt=ruby_size))
    return out
