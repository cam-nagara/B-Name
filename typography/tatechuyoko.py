"""縦中横 (半角数字を縦書きの中で横並びに) 処理.

計画書 3.1.5 参照。TextEntry.tatechuyoko_ranges で指定された範囲の
半角文字群を 1 文字分の領域に収めて横向き描画する。
"""

from __future__ import annotations

from .layout import GlyphPlacement


def apply_tatechuyoko(
    placements: list[GlyphPlacement],
    ranges,
) -> list[GlyphPlacement]:
    """指定範囲の文字を縦中横変換.

    範囲内の文字は 1 文字領域に圧縮し、rotation_deg=-90 (時計回り -90°) で
    横向き描画するマーカーを付ける。実際の回転描画は viewport_renderer /
    export_renderer が担当する。
    """
    if not ranges:
        return placements
    marked = list(placements)
    for span in ranges:
        start = span.start
        length = max(1, span.length)
        end = min(start + length, len(marked))
        if start >= end:
            continue
        # 縦中横: 親文字 1 em 領域に横並びで押し込む (各文字は shrink_size に縮小)。
        # 縦書きでも対象文字だけ横書き扱いになるため、親文字の位置を中心として
        # 水平方向に展開する (右→左の縦書きでは中央揃えで左右に並べる)。
        count = end - start
        base = marked[start]
        shrink_size = base.size_pt / max(1, count)
        x = base.x_mm
        y = base.y_mm
        em_mm = base.size_pt * 25.4 / 72.0
        shrink_em_mm = shrink_size * 25.4 / 72.0
        # 親 1 em 領域の左端を基準に shrink_em_mm 間隔で並べる
        left_x = x - em_mm / 2.0
        for i in range(start, end):
            g = marked[i]
            offset_x = shrink_em_mm * (i - start)
            marked[i] = GlyphPlacement(
                ch=g.ch,
                x_mm=left_x + offset_x,
                y_mm=y,
                size_pt=shrink_size,
                rotation_deg=0.0,  # 横並び (回転不要) ですでに「横書き相当」になる
                index=g.index,
            )
    return marked
