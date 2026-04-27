"""縦書き・横書きレイアウトエンジン.

計画書 3.1.5 参照。テキスト文字列と矩形領域 (mm) を受け取り、各文字の
配置座標 (mm) と行分割情報を計算する。描画はしない。

ビューポート用 (blf) / 書き出し用 (Pillow) で共通で呼ばれるよう、純粋
データ返却のみに徹する。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..utils.geom import q_to_pt
from . import metrics


@dataclass(frozen=True)
class GlyphPlacement:
    """1 文字の配置."""

    ch: str
    x_mm: float
    y_mm: float
    size_pt: float  # この文字の描画サイズ (縦中横などで変わる)
    rotation_deg: float  # 0=通常、-90=縦中横の横向き等
    index: int = -1  # 元本文内の文字インデックス


@dataclass(frozen=True)
class TypesetResult:
    """テキスト組版結果."""

    placements: list[GlyphPlacement]
    overflow: bool  # 収まり切らずに切れたか


def _mm_per_em_at(size_pt: float) -> float:
    """フォントサイズ (pt) 1em あたりの mm."""
    # 1 pt = 1/72 inch = 25.4/72 mm
    return size_pt * 25.4 / 72.0


def typeset_vertical(
    text: str,
    region_x_mm: float,
    region_y_mm: float,
    region_width_mm: float,
    region_height_mm: float,
    font_size_pt: float = 9.0,
    line_height: float = 1.4,
    letter_spacing: float = 0.0,
) -> TypesetResult:
    """縦書きで文字を配置.

    - 右→左の行進行
    - 文字は上→下
    - 句読点・括弧の簡易約物処理 (将来拡張)
    - 禁則処理 (行頭/行末) は簡易版
    """
    placements: list[GlyphPlacement] = []
    em_mm = _mm_per_em_at(font_size_pt)
    line_pitch_mm = em_mm * line_height
    char_pitch_mm = em_mm * (1.0 + letter_spacing)

    # 右上から始まる: 1 行目 = 右端列
    col_index = 0
    row_index = 0
    overflow = False

    for text_index, ch in enumerate(text):
        if ch == "\n":
            col_index += 1
            row_index = 0
            continue
        # 現在の列 X 座標 (右端から左へ)
        x = region_x_mm + region_width_mm - em_mm / 2.0 - col_index * line_pitch_mm
        # 現在の行 Y 座標 (上端から下へ)
        y = region_y_mm + region_height_mm - em_mm - row_index * char_pitch_mm

        if x < region_x_mm:
            overflow = True
            break
        if y < region_y_mm:
            col_index += 1
            row_index = 0
            continue

        # 禁則処理: 行頭に禁則文字が来たら前の行末にぶら下げて追加 (簡易版)。
        # 新しい行の 1 文字目になるのを避け、前行の最終文字のさらに 1 段下に置く。
        if row_index == 0 and metrics.is_kinsoku_start(ch) and placements:
            prev = placements[-1]
            placements.append(
                GlyphPlacement(
                    ch=ch,
                    x_mm=prev.x_mm,
                    y_mm=prev.y_mm - char_pitch_mm,
                    size_pt=font_size_pt,
                    rotation_deg=0.0,
                    index=text_index,
                )
            )
            # row_index はそのまま (次の文字も新行の先頭扱い)
            continue

        placements.append(
            GlyphPlacement(
                ch=ch,
                x_mm=x,
                y_mm=y,
                size_pt=font_size_pt,
                rotation_deg=0.0,
                index=text_index,
            )
        )
        row_index += 1

    return TypesetResult(placements=placements, overflow=overflow)


def typeset_horizontal(
    text: str,
    region_x_mm: float,
    region_y_mm: float,
    region_width_mm: float,
    region_height_mm: float,
    font_size_pt: float = 9.0,
    line_height: float = 1.4,
    letter_spacing: float = 0.0,
) -> TypesetResult:
    """横書きで文字を配置 (左→右、上→下)."""
    placements: list[GlyphPlacement] = []
    em_mm = _mm_per_em_at(font_size_pt)
    line_pitch_mm = em_mm * line_height
    char_pitch_mm = em_mm * (1.0 + letter_spacing)

    row = 0
    col = 0
    overflow = False
    for text_index, ch in enumerate(text):
        if ch == "\n":
            row += 1
            col = 0
            continue
        x = region_x_mm + col * char_pitch_mm
        y = region_y_mm + region_height_mm - em_mm - row * line_pitch_mm
        if y < region_y_mm:
            overflow = True
            break
        if x + char_pitch_mm > region_x_mm + region_width_mm:
            row += 1
            col = 0
            continue
        placements.append(
            GlyphPlacement(
                ch=ch,
                x_mm=x,
                y_mm=y,
                size_pt=font_size_pt,
                rotation_deg=0.0,
                index=text_index,
            )
        )
        col += 1
    return TypesetResult(placements=placements, overflow=overflow)


def typeset(
    text_entry,
    region_x_mm: float,
    region_y_mm: float,
    region_width_mm: float,
    region_height_mm: float,
) -> TypesetResult:
    """PropertyGroup TextEntry からレイアウトを実行."""
    font_size_pt = float(
        q_to_pt(float(getattr(text_entry, "font_size_q", 20.0)))
        if hasattr(text_entry, "font_size_q")
        else getattr(text_entry, "font_size_pt", 9.0)
    )
    if text_entry.writing_mode == "horizontal":
        return typeset_horizontal(
            text_entry.body,
            region_x_mm,
            region_y_mm,
            region_width_mm,
            region_height_mm,
            font_size_pt=font_size_pt,
            line_height=text_entry.line_height,
            letter_spacing=text_entry.letter_spacing,
        )
    return typeset_vertical(
        text_entry.body,
        region_x_mm,
        region_y_mm,
        region_width_mm,
        region_height_mm,
        font_size_pt=font_size_pt,
        line_height=text_entry.line_height,
        letter_spacing=text_entry.letter_spacing,
    )
