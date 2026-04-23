"""書き出しパイプライン (計画書 3.8.4).

Pillow ベースで原稿画像を合成する。draw_handler によるオーバーレイは
レンダリング出力に焼き込まれないため、同じレイアウト計算を Pillow で
再実行して焼き込む。

Phase 6a (MVP): PNG/JPEG/TIFF、カラーモード RGB/モノクロ/グレースケール、
単一ページ + 複数ページ一括、連番命名、進捗。
Phase 6b: PDF 結合 (pypdf)。
Phase 6c: PSD (psd-tools)。
Phase 6d: CMYK (littleCMS)。

Pillow が同梱されていない環境では export 系の Operator は無効化される。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..ui import overlay_shared
from ..utils import log
from ..utils.geom import mm_to_px

_logger = log.get_logger(__name__)

try:
    from PIL import Image, ImageDraw, ImageCms  # type: ignore
    _HAS_PIL = True
except ImportError:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageCms = None  # type: ignore
    _HAS_PIL = False

try:
    import pypdf  # type: ignore
    _HAS_PYPDF = True
except ImportError:  # pragma: no cover
    pypdf = None  # type: ignore
    _HAS_PYPDF = False

try:
    from psd_tools import PSDImage  # type: ignore
    from psd_tools.api.layers import PixelLayer  # type: ignore
    _HAS_PSD = True
except ImportError:  # pragma: no cover
    PSDImage = None  # type: ignore
    PixelLayer = None  # type: ignore
    _HAS_PSD = False


def has_pillow() -> bool:
    return _HAS_PIL


def has_pypdf() -> bool:
    return _HAS_PYPDF


def has_psd_tools() -> bool:
    return _HAS_PSD


@dataclass(frozen=True)
class ExportOptions:
    """書き出しオプション (3.8.1 / 3.8.2 抜粋)."""

    color_mode: str = "rgb"  # "rgb" | "monochrome" | "grayscale" | "cmyk"
    format: str = "png"       # "png" | "jpeg" | "tiff" | "pdf" | "psd"
    area: str = "withBleed"   # "finish" | "withBleed" | "innerFrame" | "canvas"
    dpi_override: int = 0     # 0 で paper.dpi をそのまま使用
    include_border: bool = True
    include_white_margin: bool = True
    include_nombre: bool = True
    include_work_info: bool = True
    include_tombo: bool = False
    include_paper_color: bool = True
    icc_profile_path: str = ""  # Phase 6d: CMYK 変換用 ICC プロファイル


# ---------- ユーティリティ ----------


def _dpi(paper, options: ExportOptions) -> int:
    return options.dpi_override if options.dpi_override > 0 else int(paper.dpi)


def _canvas_size_px(paper, options: ExportOptions) -> tuple[int, int]:
    dpi = _dpi(paper, options)
    w = int(round(mm_to_px(paper.canvas_width_mm, dpi)))
    h = int(round(mm_to_px(paper.canvas_height_mm, dpi)))
    return (w, h)


def _area_rect_px(paper, options: ExportOptions) -> tuple[int, int, int, int]:
    """書き出し範囲の (left, top, right, bottom) を px で返す (Pillow crop 用)."""
    dpi = _dpi(paper, options)
    rects = overlay_shared.compute_paper_rects(paper)
    w_px, h_px = _canvas_size_px(paper, options)
    if options.area == "canvas":
        return (0, 0, w_px, h_px)
    if options.area == "withBleed":
        r = rects.finish.inset(-paper.bleed_mm)
    elif options.area == "finish":
        r = rects.finish
    elif options.area == "innerFrame":
        r = rects.inner_frame
    else:
        return (0, 0, w_px, h_px)
    # mm → px (Pillow は左上原点)
    left = int(round(mm_to_px(r.x, dpi)))
    top = h_px - int(round(mm_to_px(r.y2, dpi)))
    right = int(round(mm_to_px(r.x2, dpi)))
    bottom = h_px - int(round(mm_to_px(r.y, dpi)))
    return (left, top, right, bottom)


# ---------- メインパイプライン ----------


def render_page(work, page, options: ExportOptions) -> Any:
    """単一ページを Pillow Image として合成.

    返り値: PIL.Image.Image (実行時) もしくは None (Pillow 未同梱時)。
    書き出しパイプライン (計画書 3.8.4) の工程を簡易実装:
      1. 紙面ベース画像 (用紙色)
      2. コマ枠画像 (_preview.png / _thumb.png 優先、無ければ白)
      3. Grease Pencil レイヤー合成 (TODO: Phase 6 後半)
      4. コマ枠線・白フチ
      5. ノンブル・作品情報 (TODO: fontTools + Pillow)
    """
    if not _HAS_PIL:
        _logger.warning("render_page called without Pillow")
        return None

    paper = work.paper
    dpi = _dpi(paper, options)
    size = _canvas_size_px(paper, options)

    # 1. ベース画像
    if options.include_paper_color:
        fill = tuple(int(round(c * 255)) for c in paper.paper_color[:3]) + (255,)
    else:
        fill = (255, 255, 255, 255)
    img = Image.new("RGBA", size, fill)

    # 2. コマのレンダー結果を貼り込み (Z 順昇順)
    _paste_panel_renders(img, work, page, paper, dpi)

    # 4. コマ枠線・白フチ
    if options.include_border or options.include_white_margin:
        _draw_panel_borders(img, page, paper, dpi, options)

    # 5. ノンブル (TODO: 実フォント使用)
    if options.include_nombre and work.nombre.enabled:
        _draw_nombre_placeholder(img, work, dpi)

    # 6. カラーモード変換
    if options.color_mode == "monochrome":
        img = img.convert("L").convert("1", dither=Image.FLOYDSTEINBERG)
    elif options.color_mode == "grayscale":
        img = img.convert("L")
    elif options.color_mode == "cmyk":
        # Phase 6d: ICC プロファイルが指定されていれば ImageCms で高品質変換
        converted = convert_to_cmyk(img, options.icc_profile_path)
        if converted is not None:
            img = converted
    else:
        img = img.convert("RGBA")

    # 7. area で crop
    if options.area != "canvas":
        crop = _area_rect_px(paper, options)
        img = img.crop(crop)
    return img


def _paste_panel_renders(img, work, page, paper, dpi: int) -> None:
    """各コマのレンダー結果 (_preview.png / _thumb.png) を貼り付け."""
    if page is None:
        return
    work_dir = Path(work.work_dir) if work.work_dir else None
    if work_dir is None:
        return
    h_px = img.height
    for entry in sorted(page.panels, key=lambda p: p.z_order):
        if entry.shape_type != "rect":
            continue
        # preview → thumb の順で試す
        try:
            index = int(entry.panel_stem.split("_", 1)[1])
        except (ValueError, IndexError):
            continue
        from ..utils import paths as paths_mod

        preview = paths_mod.panel_preview_path(work_dir, page.id, index)
        thumb = paths_mod.panel_thumb_path(work_dir, page.id, index)
        source = preview if preview.is_file() else thumb if thumb.is_file() else None
        if source is None:
            continue
        try:
            panel_img = Image.open(str(source)).convert("RGBA")
        except (OSError, ValueError):
            continue
        px_w = int(round(mm_to_px(entry.rect_width_mm, dpi)))
        px_h = int(round(mm_to_px(entry.rect_height_mm, dpi)))
        if px_w <= 0 or px_h <= 0:
            continue
        panel_img = panel_img.resize((px_w, px_h), Image.LANCZOS)
        left = int(round(mm_to_px(entry.rect_x_mm, dpi)))
        top = h_px - int(round(mm_to_px(entry.rect_y_mm + entry.rect_height_mm, dpi)))
        img.alpha_composite(panel_img, dest=(left, top))


def _draw_panel_borders(img, page, paper, dpi: int, options: ExportOptions) -> None:
    draw = ImageDraw.Draw(img)
    h_px = img.height
    for entry in sorted(page.panels, key=lambda p: p.z_order):
        if entry.shape_type != "rect":
            continue
        left = int(round(mm_to_px(entry.rect_x_mm, dpi)))
        bottom_y_mm = entry.rect_y_mm
        top = h_px - int(round(mm_to_px(bottom_y_mm + entry.rect_height_mm, dpi)))
        right = int(round(mm_to_px(entry.rect_x_mm + entry.rect_width_mm, dpi)))
        bottom = h_px - int(round(mm_to_px(bottom_y_mm, dpi)))

        # 白フチ (先に描く、枠線の外側)
        wm = entry.white_margin
        if options.include_white_margin and wm.enabled and wm.width_mm > 0:
            w_px = int(round(mm_to_px(wm.width_mm, dpi)))
            color = tuple(int(round(c * 255)) for c in wm.color[:3]) + (255,)
            draw.rectangle(
                (left - w_px, top - w_px, right + w_px, bottom + w_px),
                fill=color,
            )

        # 枠線
        b = entry.border
        if options.include_border and b.visible:
            color = tuple(int(round(c * 255)) for c in b.color[:3]) + (255,)
            width_px = max(1, int(round(mm_to_px(b.width_mm, dpi))))
            draw.rectangle((left, top, right, bottom), outline=color, width=width_px)


# ---------- Phase 6b/c/d: PDF 結合 / PSD / CMYK ----------


def merge_pdf(page_image_paths: list[Path], out_path: Path) -> bool:
    """複数の画像を 1 つの PDF に結合.

    Pillow で各画像を 1 ページ分 PDF 化 → pypdf で結合する簡易実装。
    pypdf が無い場合は Pillow のみで multi-page PDF を生成。
    """
    if not _HAS_PIL or not page_image_paths:
        return False
    images = []
    for p in page_image_paths:
        try:
            img = Image.open(str(p))
            if img.mode not in ("RGB", "L", "CMYK"):
                img = img.convert("RGB")
            images.append(img)
        except (OSError, ValueError) as exc:
            _logger.warning("pdf: failed to open %s: %s", p, exc)
    if not images:
        return False
    try:
        first, rest = images[0], images[1:]
        first.save(str(out_path), save_all=True, append_images=rest)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.exception("merge_pdf failed: %s", exc)
        return False


def save_as_psd(img, out_path: Path) -> bool:
    """Pillow Image を PSD として保存.

    psd-tools が同梱されている場合は PixelLayer で単一レイヤーとして書き出す。
    未同梱なら Pillow の PSD 書き出し (Pillow は読み込み専用なので失敗) を
    試行してフォールバック。
    """
    if not _HAS_PIL:
        return False
    if _HAS_PSD:
        try:
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            psd = PSDImage.new(mode="RGBA", size=img.size)
            PixelLayer.frompil(img, psd, layer_name="B-Name").insert(psd, 0)
            psd.save(str(out_path))
            return True
        except Exception as exc:  # noqa: BLE001
            _logger.exception("psd save failed: %s", exc)
    # Pillow のみのフォールバック (PSD 書き込みは未対応のため失敗する)
    try:
        img.save(str(out_path), format="PSD")
        return True
    except (OSError, ValueError) as exc:
        _logger.warning("psd fallback save failed: %s", exc)
        return False


def convert_to_cmyk(img, icc_profile_path: str = "") -> "Image.Image | None":
    """RGB → CMYK 変換 (計画書 Phase 6d).

    ICC プロファイルが指定されていれば ImageCms で高品質変換、
    それ以外は Pillow 既定の convert("CMYK")。
    """
    if not _HAS_PIL:
        return None
    if img.mode == "CMYK":
        return img
    if icc_profile_path and ImageCms is not None:
        try:
            srgb = ImageCms.createProfile("sRGB")
            cmyk = ImageCms.ImageCmsProfile(icc_profile_path)
            transform = ImageCms.buildTransform(srgb, cmyk, "RGB", "CMYK")
            return ImageCms.applyTransform(img.convert("RGB"), transform)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("ImageCms transform failed, fallback: %s", exc)
    return img.convert("CMYK")


def _draw_nombre_placeholder(img, work, dpi: int) -> None:
    """ノンブル描画 (Phase 6a 暫定: Pillow 既定フォント)."""
    if not _HAS_PIL:
        return
    draw = ImageDraw.Draw(img)
    n = work.nombre
    text = n.format.replace("{page}", str(n.start_number))
    # 原稿下端中央、基本枠の 5mm 下
    from ..utils.geom import inner_frame_rect

    frame = inner_frame_rect(work.paper)
    h_px = img.height
    x = int(round(mm_to_px(frame.x + frame.width / 2.0, dpi)))
    y = h_px - int(round(mm_to_px(frame.y - n.gap_vertical_mm, dpi)))
    color = tuple(int(round(c * 255)) for c in n.color[:3]) + (255,)
    draw.text((x, y), text, fill=color)
