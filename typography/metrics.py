"""フォントメトリクス計算 (fontTools 共用層).

計画書 3.1.5 の「グリフ選択・メトリクス」層。ビューポート (blf) と書き出し
(Pillow) の両方で同じ結果を得られるよう、純粋な計算のみ提供する。

fontTools は Phase 3 後半で wheels に同梱する想定。現段階ではフォント
ファイルのパスから最低限の情報を取り出せるように、標準ライブラリのみで
動くフォールバック実装を置く。実際の OpenType ``vert`` フィーチャによる
縦書きグリフ切替は fontTools 同梱後に拡張する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# fontTools が未同梱でも import エラーにならないよう lazy import。
try:
    from fontTools.ttLib import TTFont  # type: ignore
    _HAS_FONTTOOLS = True
except ImportError:  # pragma: no cover - fontTools is bundled only after wheels step
    TTFont = None  # type: ignore
    _HAS_FONTTOOLS = False


@dataclass(frozen=True)
class GlyphMetrics:
    advance: float  # 文字送り (em 単位、1.0 = フォントサイズそのまま)
    ascent: float
    descent: float
    use_vertical_variant: bool  # OpenType vert 適用済みか


def has_fonttools() -> bool:
    return _HAS_FONTTOOLS


def approximate_em_width(ch: str) -> float:
    """OpenType なしで概算の 1 文字幅を返す.

    日本語の全角文字は 1.0 em、半角英数字は 0.5 em、絵文字等は 1.0 em を
    デフォルト値とする。fontTools が使える環境では ``glyph_width`` を使う。
    """
    if not ch:
        return 0.0
    code = ord(ch)
    # ASCII 基本範囲
    if 0x0020 <= code <= 0x007E:
        return 0.5
    # 半角カナ
    if 0xFF61 <= code <= 0xFF9F:
        return 0.5
    return 1.0


def is_kinsoku_start(ch: str) -> bool:
    """行頭禁則文字か (、。 」 等を簡易判定)."""
    if not ch:
        return False
    return ch in "、。，．」』）】〉》〕］！？・ー…ゝゞ々"


def is_kinsoku_end(ch: str) -> bool:
    """行末禁則文字か (「 等)."""
    if not ch:
        return False
    return ch in "「『（【〈《〔［"


def is_tatechuyoko_candidate(chars: str) -> bool:
    """連続した半角英数字の塊が縦中横の候補か (2〜4 文字の半角数字)."""
    if not chars or len(chars) > 4:
        return False
    return all(ch.isascii() and ch.isalnum() for ch in chars)


def load_font(font_path: str) -> Optional[object]:
    """fontTools で TTFont を開く。失敗時は None."""
    if not _HAS_FONTTOOLS:
        return None
    try:
        return TTFont(font_path)
    except Exception:
        return None
