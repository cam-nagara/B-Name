"""書き出しパイプライン.

Pillow ベースで各要素を個別ラスタ化し、通常画像では合成、PSD では
レイヤー構造を保持して書き出す。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import os
from pathlib import Path
from typing import Any, Sequence

from . import export_psd
from ..ui import overlay_shared
from ..utils import border_geom, log, panel_preview
from ..utils.geom import Rect, m_to_mm, mm_to_px, q_to_mm

_logger = log.get_logger(__name__)

try:
    from PIL import Image, ImageChops, ImageCms, ImageDraw, ImageEnhance, ImageFont  # type: ignore

    _HAS_PIL = True
except ImportError:  # pragma: no cover
    Image = None  # type: ignore
    ImageChops = None  # type: ignore
    ImageCms = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageEnhance = None  # type: ignore
    ImageFont = None  # type: ignore
    _HAS_PIL = False

try:
    import pypdf  # type: ignore

    _HAS_PYPDF = True
except ImportError:  # pragma: no cover
    pypdf = None  # type: ignore
    _HAS_PYPDF = False

def has_pillow() -> bool:
    return _HAS_PIL


def has_pypdf() -> bool:
    return _HAS_PYPDF


def has_psd_tools() -> bool:
    return export_psd.has_psd_tools()


def can_write_layered_psd() -> bool:
    return export_psd.can_write_layered_psd()


@dataclass(frozen=True)
class ExportOptions:
    color_mode: str = "rgb"  # "rgb" | "monochrome" | "grayscale" | "cmyk"
    format: str = "png"  # "png" | "jpeg" | "tiff" | "pdf" | "psd"
    area: str = "withBleed"  # "finish" | "withBleed" | "innerFrame" | "canvas"
    dpi_override: int = 0
    include_border: bool = True
    include_white_margin: bool = True
    include_nombre: bool = True
    include_work_info: bool = True
    include_tombo: bool = False
    include_paper_color: bool = True
    include_panel_previews: bool = True
    icc_profile_path: str = ""


@dataclass(frozen=True)
class ExportLayer:
    name: str
    image: Any
    left: int
    top: int
    group_path: tuple[str, ...] = ()
    visible: bool = True
    opacity: int = 255
    blend_mode: str = "normal"

    @property
    def right(self) -> int:
        return self.left + self.image.width

    @property
    def bottom(self) -> int:
        return self.top + self.image.height


@dataclass(frozen=True)
class ExportMask:
    image: Any
    left: int
    top: int

    @property
    def right(self) -> int:
        return self.left + self.image.width

    @property
    def bottom(self) -> int:
        return self.top + self.image.height


@dataclass(frozen=True)
class _LayerCanvas:
    image: Any
    left: int
    top: int
    canvas_height_px: int
    dpi: int

    def point_px(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        x_px = int(round(mm_to_px(x_mm, self.dpi))) - self.left
        y_px = self.canvas_height_px - int(round(mm_to_px(y_mm, self.dpi))) - self.top
        return (x_px, y_px)

    def points_px(self, pts: Sequence[tuple[float, float]]) -> list[tuple[int, int]]:
        return [self.point_px(x, y) for x, y in pts]


def _dpi(paper, options: ExportOptions) -> int:
    return options.dpi_override if options.dpi_override > 0 else int(paper.dpi)


def _canvas_size_px(paper, options: ExportOptions) -> tuple[int, int]:
    dpi = _dpi(paper, options)
    w = int(round(mm_to_px(paper.canvas_width_mm, dpi)))
    h = int(round(mm_to_px(paper.canvas_height_mm, dpi)))
    return (w, h)


def _page_canvas_size_px(work, page, options: ExportOptions) -> tuple[int, int]:
    w, h = _canvas_size_px(work.paper, options)
    if bool(getattr(page, "spread", False)):
        return (w * 2, h)
    return (w, h)


def _area_rect_px(paper, options: ExportOptions, *, is_left_half: bool = False) -> tuple[int, int, int, int]:
    dpi = _dpi(paper, options)
    rects = overlay_shared.compute_paper_rects(paper, is_left_half=is_left_half)
    w_px, h_px = _canvas_size_px(paper, options)
    if options.area == "canvas":
        return (0, 0, w_px, h_px)
    if options.area == "withBleed":
        r = rects.bleed
    elif options.area == "finish":
        r = rects.finish
    elif options.area == "innerFrame":
        r = rects.inner_frame
    else:
        return (0, 0, w_px, h_px)
    left = int(round(mm_to_px(r.x, dpi)))
    top = h_px - int(round(mm_to_px(r.y2, dpi)))
    right = int(round(mm_to_px(r.x2, dpi)))
    bottom = h_px - int(round(mm_to_px(r.y, dpi)))
    return (left, top, right, bottom)


def _resolve_page_index(work, page) -> int:
    page_id = str(getattr(page, "id", "") or "")
    for index, candidate in enumerate(work.pages):
        if candidate == page:
            return index
        if page_id and str(getattr(candidate, "id", "") or "") == page_id:
            return index
    return max(0, int(getattr(work, "active_page_index", 0)))


def _resolve_page_number(work, page) -> int:
    try:
        start = int(work.nombre.start_number)
    except Exception:  # noqa: BLE001
        start = 1
    return start + _resolve_page_index(work, page)


def _is_active_page(work, page) -> bool:
    try:
        active_index = int(getattr(work, "active_page_index", -1))
    except Exception:  # noqa: BLE001
        return False
    if active_index < 0:
        return False
    return _resolve_page_index(work, page) == active_index


def _resolve_page_offset_mm(work, page) -> tuple[float, float]:
    try:
        import bpy

        from ..utils.page_grid import page_grid_offset_mm, page_manual_offset_mm
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        return (0.0, 0.0)
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return (0.0, 0.0)
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    index = _resolve_page_index(work, page)
    paper = work.paper
    ox, oy = page_grid_offset_mm(
        index,
        cols,
        gap,
        float(paper.canvas_width_mm),
        float(paper.canvas_height_mm),
        getattr(paper, "start_side", "right"),
        getattr(paper, "read_direction", "left"),
    )
    add_x, add_y = page_manual_offset_mm(page)
    return ox + add_x, oy + add_y


def _is_left_half_page(work, page) -> bool:
    try:
        from ..utils.page_grid import is_left_half_page
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        return False
    index = _resolve_page_index(work, page)
    return is_left_half_page(
        index,
        getattr(work.paper, "start_side", "right"),
        getattr(work.paper, "read_direction", "left"),
    )


def _empty_rgba(size: tuple[int, int]) -> Any:
    return Image.new("RGBA", size, (0, 0, 0, 0))


def _rgb255(vec: Sequence[float], alpha: float | None = None) -> tuple[int, int, int, int]:
    a = float(vec[3]) if len(vec) > 3 else 1.0
    if alpha is not None:
        a *= alpha
    return (
        int(round(max(0.0, min(1.0, float(vec[0]))) * 255)),
        int(round(max(0.0, min(1.0, float(vec[1]))) * 255)),
        int(round(max(0.0, min(1.0, float(vec[2]))) * 255)),
        int(round(max(0.0, min(1.0, a)) * 255)),
    )


def _scale_alpha(img, opacity: int) -> Any:
    if opacity >= 255:
        return img
    out = img.copy()
    alpha = out.getchannel("A").point(lambda px: int(round(px * (opacity / 255.0))))
    out.putalpha(alpha)
    return out


def _normalize_opacity(value: Any) -> int:
    try:
        f = float(value)
    except Exception:  # noqa: BLE001
        return 255
    if f <= 0.0:
        return 0
    if f <= 1.0:
        return int(round(f * 255))
    return int(round(max(0.0, min(255.0, f))))


def _blend_mode_name(value: Any) -> str:
    text = str(value or "normal").strip().lower()
    if "." in text:
        text = text.split(".")[-1]
    mapping = {
        "regular": "normal",
        "normal": "normal",
        "multiply": "multiply",
        "screen": "screen",
        "overlay": "overlay",
        "hardlight": "overlay",
        "softlight": "overlay",
        "add": "add",
        "linear_dodge": "add",
    }
    return mapping.get(text, "normal")


def _abspath_maybe(path_str: str) -> str:
    if not path_str:
        return ""
    try:
        import bpy

        return bpy.path.abspath(path_str)
    except Exception:  # noqa: BLE001
        return path_str


def _font_candidates() -> list[str]:
    if os.name == "nt":
        return [
            r"C:\Windows\Fonts\YuGothM.ttc",
            r"C:\Windows\Fonts\YuGothR.ttc",
            r"C:\Windows\Fonts\meiryo.ttc",
            r"C:\Windows\Fonts\msgothic.ttc",
        ]
    return [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    ]


def _resolve_font_path(preferred: str = "") -> str:
    preferred = _abspath_maybe(preferred).strip()
    if preferred and Path(preferred).is_file():
        return preferred
    for candidate in _font_candidates():
        if Path(candidate).is_file():
            return candidate
    return preferred


def _load_font(font_path: str, size_px: int):
    if not font_path:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(font_path, max(1, int(size_px)))
    except (OSError, IOError):
        return ImageFont.load_default()


def _text_bbox(text: str, font, stroke_width_px: int = 0) -> tuple[int, int]:
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4), (0, 0, 0, 0)))
    try:
        box = probe.textbbox((0, 0), text, font=font, stroke_width=stroke_width_px)
        return (max(1, int(math.ceil(box[2] - box[0]))), max(1, int(math.ceil(box[3] - box[1]))))
    except Exception:  # noqa: BLE001
        try:
            return probe.textsize(text, font=font)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return (max(1, len(text) * max(1, font.size // 2)), max(1, getattr(font, "size", 14)))


def _canvas_for_bbox(
    bbox_mm: tuple[float, float, float, float],
    canvas_height_px: int,
    dpi: int,
    *,
    pad_mm: float = 0.0,
) -> _LayerCanvas | None:
    left_mm = float(bbox_mm[0]) - pad_mm
    bottom_mm = float(bbox_mm[1]) - pad_mm
    right_mm = float(bbox_mm[2]) + pad_mm
    top_mm = float(bbox_mm[3]) + pad_mm
    if right_mm <= left_mm or top_mm <= bottom_mm:
        return None
    left_px = int(math.floor(mm_to_px(left_mm, dpi)))
    right_px = int(math.ceil(mm_to_px(right_mm, dpi)))
    top_px = canvas_height_px - int(math.ceil(mm_to_px(top_mm, dpi)))
    bottom_px = canvas_height_px - int(math.floor(mm_to_px(bottom_mm, dpi)))
    width_px = max(1, right_px - left_px)
    height_px = max(1, bottom_px - top_px)
    return _LayerCanvas(_empty_rgba((width_px, height_px)), left_px, top_px, canvas_height_px, dpi)


def _points_bbox(pts: Sequence[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not pts:
        return None
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _intersects_mm(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def _panel_polygon_mm(entry) -> list[tuple[float, float]]:
    if entry.shape_type == "rect":
        return [
            (float(entry.rect_x_mm), float(entry.rect_y_mm)),
            (float(entry.rect_x_mm + entry.rect_width_mm), float(entry.rect_y_mm)),
            (float(entry.rect_x_mm + entry.rect_width_mm), float(entry.rect_y_mm + entry.rect_height_mm)),
            (float(entry.rect_x_mm), float(entry.rect_y_mm + entry.rect_height_mm)),
        ]
    if entry.shape_type == "polygon" and len(entry.vertices) >= 3:
        return [(float(v.x_mm), float(v.y_mm)) for v in entry.vertices]
    return []


def _panel_group_name(entry) -> str:
    return str(getattr(entry, "panel_stem", "") or getattr(entry, "id", "") or "panel")


def _panel_root_group_path(entry) -> tuple[str, ...]:
    return ("panels", _panel_group_name(entry))


def _panel_content_group_path(entry) -> tuple[str, ...]:
    return (*_panel_root_group_path(entry), "content")


def _panel_preview_source(work_dir: Path, page_id: str, entry) -> Path | None:
    return panel_preview.panel_preview_source_path(work_dir, page_id, entry)


def _safe_load_image(path: Path) -> Any | None:
    try:
        with Image.open(str(path)) as opened:
            return opened.convert("RGBA")
    except (OSError, ValueError) as exc:
        _logger.warning("failed to open image %s: %s", path, exc)
        return None


def _line_segments_for_style(
    p0: tuple[int, int],
    p1: tuple[int, int],
    dash: float,
    gap: float,
) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    x0, y0 = p0
    x1, y1 = p1
    dx = float(x1 - x0)
    dy = float(y1 - y0)
    length = math.hypot(dx, dy)
    if length <= 0.0:
        return []
    ux = dx / length
    uy = dy / length
    pos = 0.0
    out: list[tuple[tuple[int, int], tuple[int, int]]] = []
    while pos < length:
        end = min(length, pos + dash)
        sx = int(round(x0 + ux * pos))
        sy = int(round(y0 + uy * pos))
        ex = int(round(x0 + ux * end))
        ey = int(round(y0 + uy * end))
        out.append(((sx, sy), (ex, ey)))
        pos += dash + gap
    return out


def _draw_styled_segment(
    draw,
    p0: tuple[int, int],
    p1: tuple[int, int],
    color: tuple[int, int, int, int],
    width_px: int,
    style: str = "solid",
) -> None:
    width_px = max(1, int(width_px))
    if style == "solid":
        draw.line((p0, p1), fill=color, width=width_px)
        return
    if style == "dashed":
        dash = max(width_px * 4.0, 8.0)
        gap = max(width_px * 2.5, 5.0)
        for seg in _line_segments_for_style(p0, p1, dash, gap):
            draw.line(seg, fill=color, width=width_px)
        return
    if style == "dotted":
        x0, y0 = p0
        x1, y1 = p1
        dx = float(x1 - x0)
        dy = float(y1 - y0)
        length = math.hypot(dx, dy)
        if length <= 0.0:
            return
        ux = dx / length
        uy = dy / length
        spacing = max(width_px * 2.2, 6.0)
        radius = max(1.0, width_px * 0.55)
        pos = 0.0
        while pos <= length:
            cx = x0 + ux * pos
            cy = y0 + uy * pos
            draw.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                fill=color,
                outline=color,
            )
            pos += spacing
        return
    if style == "double":
        dx = float(p1[0] - p0[0])
        dy = float(p1[1] - p0[1])
        length = math.hypot(dx, dy)
        if length <= 0.0:
            return
        nx = -dy / length
        ny = dx / length
        offset = max(2.0, width_px * 1.2)
        inner_width = max(1, int(round(width_px * 0.55)))
        for sign in (-0.5, 0.5):
            ox = nx * offset * sign
            oy = ny * offset * sign
            q0 = (int(round(p0[0] + ox)), int(round(p0[1] + oy)))
            q1 = (int(round(p1[0] + ox)), int(round(p1[1] + oy)))
            draw.line((q0, q1), fill=color, width=inner_width)
        return
    draw.line((p0, p1), fill=color, width=width_px)


def _draw_styled_loop(
    draw,
    pts: Sequence[tuple[int, int]],
    color: tuple[int, int, int, int],
    width_px: int,
    style: str = "solid",
) -> None:
    if len(pts) < 2:
        return
    for i in range(len(pts)):
        _draw_styled_segment(draw, pts[i], pts[(i + 1) % len(pts)], color, width_px, style)


def _draw_panel_border_layer(entry, canvas_height_px: int, dpi: int) -> ExportLayer | None:
    poly_mm = _panel_polygon_mm(entry)
    bbox = _points_bbox(poly_mm)
    if bbox is None:
        return None
    border = entry.border
    canvas = _canvas_for_bbox(bbox, canvas_height_px, dpi, pad_mm=max(float(border.width_mm) * 3.0, 1.0))
    if canvas is None:
        return None
    draw = ImageDraw.Draw(canvas.image)
    base_color = _rgb255(border.color)
    base_width = max(1, int(round(mm_to_px(float(border.width_mm), dpi))))
    override_map = {int(style.edge_index): style for style in entry.edge_styles}
    edge_overrides = []
    if entry.shape_type == "rect":
        edge_overrides = [border.edge_bottom, border.edge_right, border.edge_top, border.edge_left]
    rect_edge_override = any(getattr(ov, "use_override", False) for ov in edge_overrides)
    if (
        len(entry.edge_styles) == 0
        and not rect_edge_override
        and getattr(border, "style", "solid") == "solid"
    ):
        path_mm = border_geom.styled_closed_path_mm(
            poly_mm,
            getattr(border, "corner_type", "square"),
            float(getattr(border, "corner_radius_mm", 0.0)),
        )
        loops = border_geom.stroke_loops_mm(path_mm, float(border.width_mm))
        if loops is not None:
            outer_px = canvas.points_px(loops[0])
            inner_px = canvas.points_px(loops[1])
            for i in range(len(outer_px)):
                j = (i + 1) % len(outer_px)
                draw.polygon(
                    [outer_px[i], outer_px[j], inner_px[j], inner_px[i]],
                    fill=base_color,
                )
            return ExportLayer("border", canvas.image, canvas.left, canvas.top)

    poly_px = canvas.points_px(poly_mm)
    for i in range(len(poly_px)):
        style_name = getattr(border, "style", "solid")
        color = base_color
        width = base_width
        if i in override_map:
            ov = override_map[i]
            color = _rgb255(ov.color)
            width = max(1, int(round(mm_to_px(float(ov.width_mm), dpi))))
        elif i < len(edge_overrides):
            ov = edge_overrides[i]
            if getattr(ov, "use_override", False):
                if not getattr(ov, "visible", True):
                    continue
                style_name = getattr(ov, "style", style_name)
                color = _rgb255(ov.color)
                width = max(1, int(round(mm_to_px(float(ov.width_mm), dpi))))
        _draw_styled_segment(
            draw,
            poly_px[i],
            poly_px[(i + 1) % len(poly_px)],
            color,
            width,
            style_name,
        )
    return ExportLayer("border", canvas.image, canvas.left, canvas.top)


def _draw_panel_white_margin_layer(entry, canvas_height_px: int, dpi: int) -> ExportLayer | None:
    poly_mm = _panel_polygon_mm(entry)
    bbox = _points_bbox(poly_mm)
    if bbox is None:
        return None
    wm = entry.white_margin
    if entry.shape_type != "rect":
        canvas = _canvas_for_bbox(
            (bbox[0] - wm.width_mm, bbox[1] - wm.width_mm, bbox[2] + wm.width_mm, bbox[3] + wm.width_mm),
            canvas_height_px,
            dpi,
        )
        if canvas is None:
            return None
        color = _rgb255(wm.color)
        ImageDraw.Draw(canvas.image).rectangle((0, 0, canvas.image.width, canvas.image.height), fill=color)
        return ExportLayer("white_margin", canvas.image, canvas.left, canvas.top)

    rect = Rect(float(entry.rect_x_mm), float(entry.rect_y_mm), float(entry.rect_width_mm), float(entry.rect_height_mm))
    widths = [float(wm.width_mm)] * 4
    enabled = [bool(wm.enabled)] * 4
    edge_overrides = [wm.edge_bottom, wm.edge_right, wm.edge_top, wm.edge_left]
    for idx, edge in enumerate(edge_overrides):
        if getattr(edge, "use_override", False):
            widths[idx] = float(edge.width_mm)
            enabled[idx] = bool(getattr(edge, "enabled", False))
    left_w = widths[3] if enabled[3] else 0.0
    bottom_w = widths[0] if enabled[0] else 0.0
    right_w = widths[1] if enabled[1] else 0.0
    top_w = widths[2] if enabled[2] else 0.0
    bbox_wm = (rect.x - left_w, rect.y - bottom_w, rect.x2 + right_w, rect.y2 + top_w)
    canvas = _canvas_for_bbox(bbox_wm, canvas_height_px, dpi)
    if canvas is None:
        return None
    draw = ImageDraw.Draw(canvas.image)
    color = _rgb255(wm.color)
    x0, y0 = canvas.point_px(rect.x, rect.y)
    x1, y1 = canvas.point_px(rect.x2, rect.y2)
    left_px = min(x0, x1)
    right_px = max(x0, x1)
    top_px = min(y0, y1)
    bottom_px = max(y0, y1)
    top_w_px = max(0, int(round(mm_to_px(top_w, dpi))))
    bottom_w_px = max(0, int(round(mm_to_px(bottom_w, dpi))))
    left_w_px = max(0, int(round(mm_to_px(left_w, dpi))))
    right_w_px = max(0, int(round(mm_to_px(right_w, dpi))))
    if top_w_px > 0:
        draw.rectangle((left_px - left_w_px, top_px - top_w_px, right_px + right_w_px, top_px), fill=color)
    if bottom_w_px > 0:
        draw.rectangle((left_px - left_w_px, bottom_px, right_px + right_w_px, bottom_px + bottom_w_px), fill=color)
    if left_w_px > 0:
        draw.rectangle((left_px - left_w_px, top_px, left_px, bottom_px), fill=color)
    if right_w_px > 0:
        draw.rectangle((right_px, top_px, right_px + right_w_px, bottom_px), fill=color)
    return ExportLayer("white_margin", canvas.image, canvas.left, canvas.top)


def _draw_panel_background_layer(entry, canvas_height_px: int, dpi: int) -> ExportLayer | None:
    color_src = getattr(entry, "background_color", (1.0, 1.0, 1.0, 0.0))
    color = _rgb255(color_src)
    if color[3] <= 0:
        return None
    poly_mm = _panel_polygon_mm(entry)
    bbox = _points_bbox(poly_mm)
    if bbox is None or len(poly_mm) < 3:
        return None
    canvas = _canvas_for_bbox(bbox, canvas_height_px, dpi)
    if canvas is None:
        return None
    ImageDraw.Draw(canvas.image).polygon(canvas.points_px(poly_mm), fill=color)
    return ExportLayer("background", canvas.image, canvas.left, canvas.top)


def _render_panel_preview_layer(work, page, entry, canvas_size: tuple[int, int], dpi: int) -> ExportLayer | None:
    poly_mm = _panel_polygon_mm(entry)
    bbox = _points_bbox(poly_mm)
    if bbox is None or not work.work_dir:
        return None
    source_path = _panel_preview_source(Path(work.work_dir), page.id, entry)
    if source_path is None:
        return None
    source = _safe_load_image(source_path)
    if source is None:
        return None
    canvas = _canvas_for_bbox(bbox, canvas_size[1], dpi)
    if canvas is None:
        return None
    source = source.resize(canvas.image.size, Image.LANCZOS)
    if entry.shape_type == "polygon" and len(poly_mm) >= 3:
        mask = Image.new("L", canvas.image.size, 0)
        ImageDraw.Draw(mask).polygon(canvas.points_px(poly_mm), fill=255)
        canvas.image.paste(source, (0, 0), mask)
    else:
        canvas.image.paste(source, (0, 0), source)
    return ExportLayer("render", canvas.image, canvas.left, canvas.top)


def _render_panel_mask(entry, canvas_height_px: int, dpi: int) -> ExportMask | None:
    poly_mm = _panel_polygon_mm(entry)
    bbox = _points_bbox(poly_mm)
    if bbox is None or len(poly_mm) < 3:
        return None
    canvas = _canvas_for_bbox(bbox, canvas_height_px, dpi)
    if canvas is None:
        return None
    mask = Image.new("L", canvas.image.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon(canvas.points_px(poly_mm), fill=255)
    return ExportMask(mask, canvas.left, canvas.top)


def _apply_image_adjustments(img, entry) -> Any:
    out = img.convert("RGBA")
    if getattr(entry, "flip_x", False):
        out = out.transpose(Image.FLIP_LEFT_RIGHT)
    if getattr(entry, "flip_y", False):
        out = out.transpose(Image.FLIP_TOP_BOTTOM)
    brightness = float(getattr(entry, "brightness", 0.0))
    if abs(brightness) > 1e-6:
        out = ImageEnhance.Brightness(out).enhance(max(0.0, 1.0 + brightness))
    contrast = float(getattr(entry, "contrast", 0.0))
    if abs(contrast) > 1e-6:
        out = ImageEnhance.Contrast(out).enhance(max(0.0, 1.0 + contrast))
    if getattr(entry, "binarize_enabled", False):
        threshold = int(round(max(0.0, min(1.0, float(entry.binarize_threshold))) * 255))
        alpha = out.getchannel("A")
        mono = out.convert("L").point(lambda px: 255 if px >= threshold else 0)
        out = Image.merge("RGBA", (mono, mono, mono, alpha))
    tint = getattr(entry, "tint_color", None)
    if tint is not None:
        tinted = []
        for band, factor in zip(out.split(), tint):
            tinted.append(band.point(lambda px, k=float(factor): int(round(px * max(0.0, min(1.0, k))))))
        if len(tinted) == 4:
            out = Image.merge("RGBA", tuple(tinted))
    opacity = _normalize_opacity(getattr(entry, "opacity", 1.0))
    if opacity < 255:
        out = _scale_alpha(out, opacity)
    rotation = float(getattr(entry, "rotation_deg", 0.0))
    if abs(rotation) > 1e-6:
        out = out.rotate(-rotation, expand=True, resample=Image.BICUBIC)
    return out


def _render_image_layer(entry, canvas_size: tuple[int, int], dpi: int) -> ExportLayer | None:
    path = Path(_abspath_maybe(getattr(entry, "filepath", "")))
    if not path.is_file():
        return None
    source = _safe_load_image(path)
    if source is None:
        return None
    width_px = max(1, int(round(mm_to_px(float(entry.width_mm), dpi))))
    height_px = max(1, int(round(mm_to_px(float(entry.height_mm), dpi))))
    source = source.resize((width_px, height_px), Image.LANCZOS)
    source = _apply_image_adjustments(source, entry)
    center_x = int(round(mm_to_px(float(entry.x_mm + entry.width_mm * 0.5), dpi)))
    center_y = canvas_size[1] - int(round(mm_to_px(float(entry.y_mm + entry.height_mm * 0.5), dpi)))
    left = center_x - source.width // 2
    top = center_y - source.height // 2
    return ExportLayer(
        str(getattr(entry, "title", "") or path.stem),
        source,
        left,
        top,
        group_path=("image_layers",),
        visible=bool(getattr(entry, "visible", True)),
        opacity=255,
        blend_mode=_blend_mode_name(getattr(entry, "blend_mode", "normal")),
    )


def _render_balloon_layer(entry, canvas_height_px: int, dpi: int) -> ExportLayer | None:
    from . import export_balloon

    return export_balloon.render_balloon_layer(entry, canvas_height_px, dpi)


def _render_text_layer(entry, canvas_height_px: int, dpi: int) -> ExportLayer | None:
    body = (getattr(entry, "body", "") or "").strip()
    if not body:
        return None
    pad_mm = 1.5
    canvas = _canvas_for_bbox(
        (
            float(entry.x_mm),
            float(entry.y_mm),
            float(entry.x_mm + entry.width_mm),
            float(entry.y_mm + entry.height_mm),
        ),
        canvas_height_px,
        dpi,
        pad_mm=pad_mm,
    )
    if canvas is None:
        return None
    from ..typography import export_renderer, layout as text_layout
    from ..utils import text_style

    font_path = _resolve_font_path(str(getattr(entry, "font", "")))
    result = text_layout.typeset(entry, pad_mm, pad_mm, float(entry.width_mm), float(entry.height_mm))
    stroke_width_px = 0
    stroke_color = (255, 255, 255, 255)
    if getattr(entry, "stroke_enabled", False):
        stroke_width_px = max(1, int(round(mm_to_px(float(getattr(entry, "stroke_width_mm", 0.2)), dpi))))
        stroke_color = _rgb255(getattr(entry, "stroke_color", (1.0, 1.0, 1.0, 1.0)))
    export_renderer.render_to_image(
        result,
        canvas.image,
        font_path=font_path,
        font_path_for_index=lambda index: _resolve_font_path(text_style.font_for_index(entry, index)),
        px_per_mm=mm_to_px(1.0, dpi),
        color=_rgb255(getattr(entry, "color", (0.0, 0.0, 0.0, 1.0))),
        stroke_width_px=stroke_width_px,
        stroke_color=stroke_color,
    )
    return ExportLayer(
        str(getattr(entry, "id", "") or "text"),
        canvas.image,
        canvas.left,
        canvas.top,
        group_path=("texts",),
    )


def _render_simple_text_layer(
    text: str,
    *,
    left_mm: float,
    baseline_top_mm: float,
    font_path: str,
    font_size_mm: float,
    color: tuple[int, int, int, int],
    dpi: int,
    canvas_height_px: int,
    group_path: tuple[str, ...],
    name: str,
    anchor_x: str = "left",
    anchor_y: str = "top",
    stroke_width_mm: float = 0.0,
    stroke_color: tuple[int, int, int, int] = (255, 255, 255, 255),
) -> ExportLayer | None:
    if not text:
        return None
    font = _load_font(font_path, max(1, int(round(mm_to_px(font_size_mm, dpi)))))
    stroke_width_px = max(0, int(round(mm_to_px(stroke_width_mm, dpi))))
    text_w, text_h = _text_bbox(text, font, stroke_width_px=stroke_width_px)
    pad_px = max(2, stroke_width_px + 2)
    image = _empty_rgba((text_w + pad_px * 2, text_h + pad_px * 2))
    draw = ImageDraw.Draw(image)
    draw.text((pad_px, pad_px), text, font=font, fill=color, stroke_width=stroke_width_px, stroke_fill=stroke_color)
    x_px = int(round(mm_to_px(left_mm, dpi)))
    y_px = canvas_height_px - int(round(mm_to_px(baseline_top_mm, dpi)))
    if anchor_x == "center":
        x_px -= image.width // 2
    elif anchor_x == "right":
        x_px -= image.width
    if anchor_y == "middle":
        y_px -= image.height // 2
    elif anchor_y == "bottom":
        y_px -= image.height
    return ExportLayer(name, image, x_px, y_px, group_path=group_path)


def _work_info_layers(work, page, canvas_size: tuple[int, int], dpi: int) -> list[ExportLayer]:
    info = getattr(work, "work_info", None)
    if info is None:
        return []
    layers: list[ExportLayer] = []
    rects = overlay_shared.compute_paper_rects(work.paper, is_left_half=_is_left_half_page(work, page))
    inner = rects.inner_frame
    page_text = f"ページ{int(getattr(info, 'page_number_start', 1)) + _resolve_page_index(work, page):04d}"
    items = [
        (info.display_work_name, info.work_name, "work_name"),
        (info.display_episode, f"第{info.episode_number}話" if info.episode_number else "", "episode"),
        (info.display_subtitle, info.subtitle, "subtitle"),
        (info.display_author, info.author, "author"),
        (info.display_page_number, page_text, "page_number"),
    ]
    pad_mm = 2.0
    font_path = _resolve_font_path("")
    for item, text, name in items:
        if item is None or not getattr(item, "enabled", False) or not text:
            continue
        pos = getattr(item, "position", "bottom-left")
        if pos.endswith("left"):
            x_mm = inner.x
            anchor_x = "left"
        elif pos.endswith("right"):
            x_mm = inner.x2
            anchor_x = "right"
        else:
            x_mm = inner.x + inner.width * 0.5
            anchor_x = "center"
        if pos.startswith("top"):
            y_mm = inner.y2 + pad_mm
            anchor_y = "top"
        else:
            y_mm = inner.y - pad_mm
            anchor_y = "bottom"
        font_size_mm = q_to_mm(float(getattr(item, "font_size_q", 20.0)))
        layer = _render_simple_text_layer(
            text,
            left_mm=x_mm,
            baseline_top_mm=y_mm,
            font_path=font_path,
            font_size_mm=font_size_mm,
            color=_rgb255(item.color),
            dpi=dpi,
            canvas_height_px=canvas_size[1],
            group_path=("work_info",),
            name=name,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
        )
        if layer is not None:
            layers.append(layer)
    return layers


def _nombre_layer(work, page, canvas_size: tuple[int, int], dpi: int) -> ExportLayer | None:
    nombre = getattr(work, "nombre", None)
    if nombre is None or not getattr(nombre, "enabled", False):
        return None
    page_number = _resolve_page_number(work, page)
    text = overlay_shared.format_nombre_text(nombre, page_number)
    x_mm, y_mm = overlay_shared.nombre_anchor(work.paper, nombre, is_left_half=_is_left_half_page(work, page))
    font_path = _resolve_font_path(str(getattr(nombre, "font", "")))
    anchor_x = "center"
    pos = getattr(nombre, "position", "bottom-center")
    if pos.endswith("left"):
        anchor_x = "left"
    elif pos.endswith("right"):
        anchor_x = "right"
    anchor_y = "bottom" if pos.startswith("bottom") else "top"
    stroke_width_mm = float(getattr(nombre, "border_width_mm", 0.0)) if getattr(nombre, "border_enabled", False) else 0.0
    stroke_color = _rgb255(getattr(nombre, "border_color", (1.0, 1.0, 1.0, 1.0)))
    return _render_simple_text_layer(
        text,
        left_mm=float(x_mm),
        baseline_top_mm=float(y_mm),
        font_path=font_path,
        font_size_mm=float(getattr(nombre, "font_size_pt", 9.0)) * 25.4 / 72.0,
        color=_rgb255(nombre.color),
        dpi=dpi,
        canvas_height_px=canvas_size[1],
        group_path=("nombre",),
        name="nombre",
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        stroke_width_mm=stroke_width_mm,
        stroke_color=stroke_color,
    )


def _trim_mark_segments(rects) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    fr = rects.finish
    br = rects.bleed
    arm = 10.0
    gap = 5.0
    size = 10.0
    half = size * 0.5
    cx_mid = (fr.x + fr.x2) * 0.5
    cy_mid = (fr.y + fr.y2) * 0.5
    segs = [
        ((br.x - arm, fr.y), (br.x, fr.y)),
        ((fr.x, br.y - arm), (fr.x, br.y)),
        ((br.x - arm, br.y), (br.x, br.y)),
        ((br.x, br.y - arm), (br.x, br.y)),
        ((br.x2, fr.y), (br.x2 + arm, fr.y)),
        ((fr.x2, br.y - arm), (fr.x2, br.y)),
        ((br.x2, br.y), (br.x2 + arm, br.y)),
        ((br.x2, br.y - arm), (br.x2, br.y)),
        ((br.x - arm, fr.y2), (br.x, fr.y2)),
        ((fr.x, br.y2), (fr.x, br.y2 + arm)),
        ((br.x - arm, br.y2), (br.x, br.y2)),
        ((br.x, br.y2), (br.x, br.y2 + arm)),
        ((br.x2, fr.y2), (br.x2 + arm, fr.y2)),
        ((fr.x2, br.y2), (fr.x2, br.y2 + arm)),
        ((br.x2, br.y2), (br.x2 + arm, br.y2)),
        ((br.x2, br.y2), (br.x2, br.y2 + arm)),
    ]
    cy_top = br.y2 + gap + half
    cy_bottom = br.y - gap - half
    cx_left = br.x - gap - half
    cx_right = br.x2 + gap + half
    segs.extend(
        [
            ((cx_mid, cy_top - half), (cx_mid, cy_top + half)),
            ((cx_mid - half, cy_top), (cx_mid + half, cy_top)),
            ((cx_mid, cy_bottom - half), (cx_mid, cy_bottom + half)),
            ((cx_mid - half, cy_bottom), (cx_mid + half, cy_bottom)),
            ((cx_left, cy_mid - half), (cx_left, cy_mid + half)),
            ((cx_left - half, cy_mid), (cx_left + half, cy_mid)),
            ((cx_right, cy_mid - half), (cx_right, cy_mid + half)),
            ((cx_right - half, cy_mid), (cx_right + half, cy_mid)),
        ]
    )
    return segs


def _tombo_layer(work, page, canvas_size: tuple[int, int], dpi: int) -> ExportLayer | None:
    rects = overlay_shared.compute_paper_rects(work.paper, is_left_half=_is_left_half_page(work, page))
    segs = _trim_mark_segments(rects)
    xs = [p[0] for seg in segs for p in seg]
    ys = [p[1] for seg in segs for p in seg]
    canvas = _canvas_for_bbox((min(xs), min(ys), max(xs), max(ys)), canvas_size[1], dpi, pad_mm=1.0)
    if canvas is None:
        return None
    draw = ImageDraw.Draw(canvas.image)
    color = (13, 13, 13, 242)
    width = max(1, int(round(mm_to_px(0.40, dpi))))
    for p0, p1 in segs:
        draw.line((canvas.point_px(*p0), canvas.point_px(*p1)), fill=color, width=width)
    return ExportLayer("tombo", canvas.image, canvas.left, canvas.top, group_path=("tombo",))


def _gp_material_info(obj, stroke) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], bool]:
    color = (0, 0, 0, 255)
    fill = (0, 0, 0, 255)
    show_fill = False
    idx = int(getattr(stroke, "material_index", 0))
    mats = getattr(getattr(obj, "data", None), "materials", None)
    if mats is None or idx < 0 or idx >= len(mats):
        return (color, fill, show_fill)
    mat = mats[idx]
    style = getattr(mat, "grease_pencil", None)
    if style is None:
        return (color, fill, show_fill)
    if hasattr(style, "color"):
        color = _rgb255(style.color)
    if hasattr(style, "fill_color"):
        fill = _rgb255(style.fill_color)
    show_fill = bool(getattr(style, "show_fill", False))
    return (color, fill, show_fill)


def _gp_layer_frame(layer):
    frames = list(getattr(layer, "frames", []))
    if not frames:
        return None
    try:
        import bpy

        current = int(bpy.context.scene.frame_current)
    except Exception:  # noqa: BLE001
        current = None
    if current is None:
        return frames[0]
    exact = [frame for frame in frames if int(getattr(frame, "frame_number", -1)) == current]
    if exact:
        return exact[0]
    earlier = [frame for frame in frames if int(getattr(frame, "frame_number", -1)) <= current]
    if earlier:
        earlier.sort(key=lambda frame: int(getattr(frame, "frame_number", 0)), reverse=True)
        return earlier[0]
    return frames[0]


def _gp_stroke_points_mm(obj, stroke, page_offset_mm: tuple[float, float]) -> tuple[list[tuple[float, float]], float]:
    obj_loc = getattr(obj, "location", None)
    obj_x = float(getattr(obj_loc, "x", 0.0))
    obj_y = float(getattr(obj_loc, "y", 0.0))
    pts: list[tuple[float, float]] = []
    radii: list[float] = []
    for point in getattr(stroke, "points", []):
        pos = getattr(point, "position", None)
        if pos is None:
            continue
        x_mm = m_to_mm(obj_x + float(pos[0])) - page_offset_mm[0]
        y_mm = m_to_mm(obj_y + float(pos[1])) - page_offset_mm[1]
        pts.append((x_mm, y_mm))
        try:
            radii.append(m_to_mm(float(getattr(point, "radius", 0.0002))) * 2.0)
        except Exception:  # noqa: BLE001
            pass
    width_mm = max(radii) if radii else 0.4
    return (pts, max(0.05, width_mm))


def _render_gp_object_layers(
    obj,
    work,
    page,
    canvas_size: tuple[int, int],
    dpi: int,
    *,
    group_root: str,
    page_offset_mm: tuple[float, float],
) -> list[ExportLayer]:
    canvas_bbox = (0.0, 0.0, float(work.paper.canvas_width_mm), float(work.paper.canvas_height_mm))
    out: list[ExportLayer] = []
    data = getattr(obj, "data", None)
    layers = getattr(data, "layers", None)
    if layers is None:
        return out
    try:
        from ..utils import gpencil as gp_utils
        from ..utils import gp_layer_parenting as gp_parent
        from ..utils.layer_hierarchy import page_stack_key, split_child_key
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        gp_utils = None
        gp_parent = None
        page_stack_key = None
        split_child_key = None
    current_page_key = page_stack_key(page) if page_stack_key is not None else ""
    for layer in layers:
        if gp_parent is not None:
            parent_key = gp_parent.parent_key(layer)
            if parent_key:
                layer_page_key, _child_key = split_child_key(parent_key)
                if layer_page_key != current_page_key:
                    continue
        try:
            hidden = (
                gp_utils.layer_effectively_hidden(layer)
                if gp_utils is not None
                else bool(getattr(layer, "hide", False))
            )
            if hidden:
                continue
        except Exception:  # noqa: BLE001
            if bool(getattr(layer, "hide", False)):
                continue
        frame = _gp_layer_frame(layer)
        drawing = getattr(frame, "drawing", None) if frame is not None else None
        strokes = list(getattr(drawing, "strokes", [])) if drawing is not None else []
        if not strokes:
            continue
        stroke_payloads: list[
            tuple[
                list[tuple[float, float]],
                float,
                tuple[int, int, int, int],
                tuple[int, int, int, int],
                bool,
                bool,
            ]
        ] = []
        bbox_pts: list[tuple[float, float]] = []
        for stroke in strokes:
            pts_mm, width_mm = _gp_stroke_points_mm(obj, stroke, page_offset_mm)
            if len(pts_mm) < 2:
                continue
            bbox = _points_bbox(pts_mm)
            if bbox is None:
                continue
            expanded = (bbox[0] - width_mm, bbox[1] - width_mm, bbox[2] + width_mm, bbox[3] + width_mm)
            if not _intersects_mm(expanded, canvas_bbox):
                continue
            stroke_color, fill_color, show_fill = _gp_material_info(obj, stroke)
            cyclic = bool(getattr(stroke, "cyclic", False))
            stroke_payloads.append((pts_mm, width_mm, stroke_color, fill_color, show_fill, cyclic))
            bbox_pts.extend(pts_mm)
        if not stroke_payloads:
            continue
        bbox = _points_bbox(bbox_pts)
        if bbox is None:
            continue
        max_width = max(payload[1] for payload in stroke_payloads)
        canvas = _canvas_for_bbox(bbox, canvas_size[1], dpi, pad_mm=max_width * 2.0)
        if canvas is None:
            continue
        draw = ImageDraw.Draw(canvas.image)
        for pts_mm, width_mm, stroke_color, fill_color, show_fill, cyclic in stroke_payloads:
            pts_px = canvas.points_px(pts_mm)
            width_px = max(1, int(round(mm_to_px(width_mm, dpi))))
            if cyclic and show_fill and len(pts_px) >= 3:
                draw.polygon(pts_px, fill=fill_color)
            draw.line(pts_px, fill=stroke_color, width=width_px)
            if cyclic and len(pts_px) >= 3:
                draw.line([*pts_px, pts_px[0]], fill=stroke_color, width=width_px)
        out.append(
            ExportLayer(
                str(getattr(layer, "name", "layer")),
                canvas.image,
                canvas.left,
                canvas.top,
                group_path=("gp", group_root),
                visible=True,
                opacity=_normalize_opacity(getattr(layer, "opacity", 1.0)),
                blend_mode=_blend_mode_name(getattr(layer, "blend_mode", "normal")),
            )
        )
    return out


def _gp_layers(work, page, canvas_size: tuple[int, int], dpi: int) -> list[ExportLayer]:
    try:
        import bpy

        from ..utils import gpencil as gp_utils
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        return []
    out: list[ExportLayer] = []
    master = gp_utils.get_master_gpencil()
    page_offset_mm = _resolve_page_offset_mm(work, page)
    if master is not None:
        out.extend(
            _render_gp_object_layers(
                master,
                work,
                page,
                canvas_size,
                dpi,
                group_root="master",
                page_offset_mm=page_offset_mm,
            )
        )
    effect_obj = bpy.data.objects.get("BName_EffectLines")
    # 効果線 GP は現状ページ非依存の単一オブジェクトで保持されており、
    # そのまま全ページに適用すると同一ストロークが全ページへ重複出力される。
    # ページ紐付けが実装されるまでは、編集中のアクティブページにのみ出す。
    if effect_obj is not None and effect_obj is not master and _is_active_page(work, page):
        out.extend(
            _render_gp_object_layers(
                effect_obj,
                work,
                page,
                canvas_size,
                dpi,
                group_root="effects",
                page_offset_mm=(0.0, 0.0),
            )
        )
    return out


def build_page_layers(work, page, options: ExportOptions) -> list[ExportLayer]:
    if not _HAS_PIL:
        return []
    paper = work.paper
    dpi = _dpi(paper, options)
    canvas_size = _page_canvas_size_px(work, page, options)
    layers: list[ExportLayer] = []
    if options.include_paper_color:
        layers.append(
            ExportLayer(
                "paper",
                Image.new("RGBA", canvas_size, _rgb255(paper.paper_color)),
                0,
                0,
                group_path=("paper",),
            )
        )
    else:
        layers.append(ExportLayer("paper", _empty_rgba(canvas_size), 0, 0, group_path=("paper",)))

    try:
        import bpy

        image_layers = getattr(bpy.context.scene, "bname_image_layers", None)
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        image_layers = None
    if image_layers is not None:
        for entry in image_layers:
            if not getattr(entry, "visible", True):
                continue
            layer = _render_image_layer(entry, canvas_size, dpi)
            if layer is not None:
                layers.append(layer)

    for panel in sorted(page.panels, key=lambda candidate: int(getattr(candidate, "z_order", 0))):
        panel_group = _panel_root_group_path(panel)
        content_group = _panel_content_group_path(panel)
        if options.include_white_margin and getattr(panel.white_margin, "enabled", False):
            wm_layer = _draw_panel_white_margin_layer(panel, canvas_size[1], dpi)
            if wm_layer is not None:
                layers.append(replace(wm_layer, group_path=panel_group))
        bg_layer = _draw_panel_background_layer(panel, canvas_size[1], dpi)
        if bg_layer is not None:
            layers.append(replace(bg_layer, group_path=content_group))
        if options.include_panel_previews:
            render_layer = _render_panel_preview_layer(work, page, panel, canvas_size, dpi)
            if render_layer is not None:
                layers.append(replace(render_layer, group_path=content_group))
        if options.include_border and getattr(panel.border, "visible", False):
            border_layer = _draw_panel_border_layer(panel, canvas_size[1], dpi)
            if border_layer is not None:
                layers.append(replace(border_layer, group_path=panel_group))

    layers.extend(_gp_layers(work, page, canvas_size, dpi))

    for balloon in getattr(page, "balloons", []):
        layer = _render_balloon_layer(balloon, canvas_size[1], dpi)
        if layer is not None:
            layers.append(layer)

    for text in getattr(page, "texts", []):
        layer = _render_text_layer(text, canvas_size[1], dpi)
        if layer is not None:
            layers.append(layer)

    if options.include_tombo:
        tombo = _tombo_layer(work, page, canvas_size, dpi)
        if tombo is not None:
            layers.append(tombo)

    if options.include_work_info:
        layers.extend(_work_info_layers(work, page, canvas_size, dpi))

    if options.include_nombre:
        nombre = _nombre_layer(work, page, canvas_size, dpi)
        if nombre is not None:
            layers.append(nombre)
    return layers


def _crop_layers(
    layers: Sequence[ExportLayer],
    crop_box: tuple[int, int, int, int],
) -> tuple[list[ExportLayer], tuple[int, int]]:
    crop_left, crop_top, crop_right, crop_bottom = crop_box
    out: list[ExportLayer] = []
    for layer in layers:
        inter_left = max(layer.left, crop_left)
        inter_top = max(layer.top, crop_top)
        inter_right = min(layer.right, crop_right)
        inter_bottom = min(layer.bottom, crop_bottom)
        if inter_right <= inter_left or inter_bottom <= inter_top:
            continue
        src_box = (
            inter_left - layer.left,
            inter_top - layer.top,
            inter_right - layer.left,
            inter_bottom - layer.top,
        )
        out.append(
            replace(
                layer,
                image=layer.image.crop(src_box),
                left=inter_left - crop_left,
                top=inter_top - crop_top,
            )
        )
    return (out, (crop_right - crop_left, crop_bottom - crop_top))


def _panel_group_masks(work, page, options: ExportOptions) -> dict[tuple[str, ...], ExportMask]:
    dpi = _dpi(work.paper, options)
    canvas_size = _page_canvas_size_px(work, page, options)
    masks: dict[tuple[str, ...], ExportMask] = {}
    for panel in sorted(page.panels, key=lambda candidate: int(getattr(candidate, "z_order", 0))):
        mask = _render_panel_mask(panel, canvas_size[1], dpi)
        if mask is not None:
            masks[_panel_content_group_path(panel)] = mask
    return masks


def _crop_group_masks(
    masks: dict[tuple[str, ...], ExportMask],
    crop_box: tuple[int, int, int, int],
) -> dict[tuple[str, ...], ExportMask]:
    crop_left, crop_top, crop_right, crop_bottom = crop_box
    out: dict[tuple[str, ...], ExportMask] = {}
    for path, mask in masks.items():
        inter_left = max(mask.left, crop_left)
        inter_top = max(mask.top, crop_top)
        inter_right = min(mask.right, crop_right)
        inter_bottom = min(mask.bottom, crop_bottom)
        if inter_right <= inter_left or inter_bottom <= inter_top:
            continue
        src_box = (
            inter_left - mask.left,
            inter_top - mask.top,
            inter_right - mask.left,
            inter_bottom - mask.top,
        )
        out[path] = ExportMask(
            mask.image.crop(src_box),
            inter_left - crop_left,
            inter_top - crop_top,
        )
    return out


def _convert_flatten_mode(img, options: ExportOptions):
    if options.color_mode == "monochrome":
        return img.convert("L").convert("1", dither=Image.FLOYDSTEINBERG)
    if options.color_mode == "grayscale":
        return img.convert("L")
    if options.color_mode == "cmyk":
        converted = convert_to_cmyk(img, options.icc_profile_path)
        return converted if converted is not None else img
    return img.convert("RGBA")


def _convert_layer_mode_rgba(layer: ExportLayer, color_mode: str) -> ExportLayer:
    if color_mode == "rgb":
        return layer
    out = layer.image.convert("RGBA")
    alpha = out.getchannel("A")
    if color_mode == "grayscale":
        gray = out.convert("L")
        out = Image.merge("RGBA", (gray, gray, gray, alpha))
    elif color_mode == "monochrome":
        mono = out.convert("L").point(lambda px: 255 if px >= 128 else 0)
        out = Image.merge("RGBA", (mono, mono, mono, alpha))
    return replace(layer, image=out)


def _blend_rgb(base_rgb, src_rgb, mode: str):
    mode = (mode or "normal").lower()
    if mode == "multiply":
        return ImageChops.multiply(base_rgb, src_rgb)
    if mode == "screen":
        return ImageChops.screen(base_rgb, src_rgb)
    if mode == "lighten":
        return ImageChops.lighter(base_rgb, src_rgb)
    if mode == "overlay" and hasattr(ImageChops, "overlay"):
        return ImageChops.overlay(base_rgb, src_rgb)
    if mode in {"add", "linear_dodge"}:
        return ImageChops.add(base_rgb, src_rgb, scale=1.0)
    return src_rgb


def _composite_layer(canvas, layer: ExportLayer) -> None:
    if not layer.visible or layer.opacity <= 0:
        return
    src = layer.image.convert("RGBA")
    if layer.opacity < 255:
        src = _scale_alpha(src, layer.opacity)
    left = max(0, layer.left)
    top = max(0, layer.top)
    right = min(canvas.width, layer.right)
    bottom = min(canvas.height, layer.bottom)
    if right <= left or bottom <= top:
        return
    src_crop = src.crop((left - layer.left, top - layer.top, right - layer.left, bottom - layer.top))
    if layer.blend_mode in ("normal", "", None):
        canvas.alpha_composite(src_crop, dest=(left, top))
        return
    base_region = canvas.crop((left, top, right, bottom))
    base_rgb = base_region.convert("RGB")
    src_rgb = src_crop.convert("RGB")
    blended_rgb = _blend_rgb(base_rgb, src_rgb, layer.blend_mode)
    mask = src_crop.getchannel("A")
    mixed_rgb = Image.composite(blended_rgb, base_rgb, mask)
    alpha_region = base_region.copy()
    alpha_region.alpha_composite(src_crop)
    composed = Image.merge("RGBA", (*mixed_rgb.split(), alpha_region.getchannel("A")))
    canvas.paste(composed, (left, top))


def _flatten_layers(layers: Sequence[ExportLayer], size: tuple[int, int]) -> Any:
    canvas = _empty_rgba(size)
    for layer in layers:
        _composite_layer(canvas, layer)
    return canvas


def render_page(work, page, options: ExportOptions) -> Any:
    if not _HAS_PIL:
        _logger.warning("render_page called without Pillow")
        return None
    layers = build_page_layers(work, page, options)
    crop_box = _area_rect_px(work.paper, options, is_left_half=_is_left_half_page(work, page))
    if options.area != "canvas":
        layers, size = _crop_layers(layers, crop_box)
    else:
        size = _page_canvas_size_px(work, page, options)
    image = _flatten_layers(layers, size)
    return _convert_flatten_mode(image, options)


def merge_pdf(page_image_paths: list[Path], out_path: Path) -> bool:
    if not _HAS_PIL or not page_image_paths:
        return False
    images = []
    for path in page_image_paths:
        try:
            img = Image.open(str(path))
            if img.mode not in ("RGB", "L", "CMYK"):
                img = img.convert("RGB")
            images.append(img)
        except (OSError, ValueError) as exc:
            _logger.warning("pdf: failed to open %s: %s", path, exc)
    if not images:
        return False
    try:
        first, rest = images[0], images[1:]
        first.save(str(out_path), save_all=True, append_images=rest)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.exception("merge_pdf failed: %s", exc)
        return False


def save_page_as_psd(work, page, options: ExportOptions, out_path: Path) -> bool:
    if not _HAS_PIL:
        raise RuntimeError("Pillow が利用できません")
    if not export_psd.can_write_layered_psd():
        raise RuntimeError("PSD レイヤー出力を利用できません")
    if options.color_mode == "cmyk":
        raise RuntimeError("PSD レイヤー出力での CMYK は未対応です")
    layers = build_page_layers(work, page, options)
    group_masks = _panel_group_masks(work, page, options)
    layers = [_convert_layer_mode_rgba(layer, options.color_mode) for layer in layers]
    crop_box = _area_rect_px(work.paper, options, is_left_half=_is_left_half_page(work, page))
    if options.area != "canvas":
        layers, size = _crop_layers(layers, crop_box)
        group_masks = _crop_group_masks(group_masks, crop_box)
    else:
        size = _page_canvas_size_px(work, page, options)
    if not layers:
        layers = [ExportLayer("empty", _empty_rgba(size), 0, 0)]
    ok = export_psd.save_layers_as_psd(layers, size, out_path, group_masks=group_masks)
    if not ok:
        raise RuntimeError("PSD 保存に失敗しました")
    return True


def save_as_psd(img, out_path: Path) -> bool:
    if not _HAS_PIL:
        return False
    return export_psd.save_flat_image_as_psd(img, out_path)


def convert_to_cmyk(img, icc_profile_path: str = "") -> "Image.Image | None":
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
