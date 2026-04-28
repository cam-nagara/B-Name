"""書き出し用テキストレンダリング (Pillow).

計画書 3.1.5 / 3.8.4 参照。layout.py の計算結果を受け取り、Pillow 画像
に焼き込む。書き出しパイプライン (Phase 6) から呼ばれる。

Pillow が同梱されていない環境 (Phase 3 時点) では使えないため、遅延
インポートにして fallback を設ける。
"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from ..utils import python_deps
from ..utils import log
from .layout import TypesetResult

python_deps.ensure_bundled_wheels_on_path()

_logger = log.get_logger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
    _HAS_PIL = True
except ImportError:  # pragma: no cover - Pillow is bundled later
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    _HAS_PIL = False


def has_pillow() -> bool:
    return _HAS_PIL


def render_to_image(
    result: TypesetResult,
    image: Any,
    *,
    font_path: str,
    font_path_for_index: Callable[[int], str] | None = None,
    color_for_index: Callable[[int], tuple[int, int, int, int]] | None = None,
    bold_for_index: Callable[[int], bool] | None = None,
    px_per_mm: float,
    origin_xy_px: tuple[float, float] = (0.0, 0.0),
    color: tuple[int, int, int, int] = (0, 0, 0, 255),
    stroke_width_px: int = 0,
    stroke_color: tuple[int, int, int, int] = (255, 255, 255, 255),
) -> None:
    """Pillow Image に組版結果を描画."""
    if not _HAS_PIL:
        _logger.warning("Pillow not bundled; export_renderer disabled")
        return
    draw = ImageDraw.Draw(image)
    font_cache: dict[tuple[str, int], Any] = {}
    for g in result.placements:
        size_px = max(1, int(g.size_pt * px_per_mm * 25.4 / 72.0))
        glyph_font_path = font_path_for_index(g.index) if font_path_for_index is not None else font_path
        cache_key = (glyph_font_path or "", size_px)
        font = font_cache.get(cache_key)
        if font is None:
            try:
                font = ImageFont.truetype(glyph_font_path, size_px)
            except (OSError, IOError):
                font = ImageFont.load_default()
            font_cache[cache_key] = font
        x = origin_xy_px[0] + g.x_mm * px_per_mm
        y = origin_xy_px[1] + g.y_mm * px_per_mm
        # Pillow の座標系は左上原点なので Y 反転
        y_px = image.height - y
        glyph_color = color_for_index(g.index) if color_for_index is not None else color
        kwargs: dict = {"fill": glyph_color}
        if stroke_width_px > 0:
            kwargs["stroke_width"] = stroke_width_px
            kwargs["stroke_fill"] = stroke_color
        draw.text((x, y_px), g.ch, font=font, **kwargs)
        if bold_for_index is not None and bold_for_index(g.index):
            draw.text((x + max(1, size_px // 28), y_px), g.ch, font=font, **kwargs)
