"""コマ編集カメラ用のページ参照画像生成ヘルパ."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..core.work import find_page_by_id
from ..io import export_pipeline
from . import log, page_grid, panel_preview, paths
from .geom import mm_to_px
from .panel_camera_constants import (
    DEFAULT_REF_DPI,
    KOMA_REF_PREFIX,
    NAME_REF_PREFIX,
    REFERENCE_DIR_NAME,
)

_logger = log.get_logger(__name__)


class ReferenceImage:
    def __init__(
        self,
        path: Path,
        label: str,
        kind: str,
        page_id: str,
        visible: bool,
        *,
        full_page_mask: bool = False,
        page_count: int = 1,
        render_side: str = "full",
    ) -> None:
        self.path = Path(path)
        self.label = label
        self.kind = kind
        self.page_id = page_id
        self.visible = visible
        self.full_page_mask = full_page_mask
        self.page_count = max(1, int(page_count))
        self.render_side = render_side if render_side in {"left", "right", "full"} else "full"


def ensure_reference_images(work, current_page_id: str, panel_stem: str) -> list[ReferenceImage]:
    """現在コマ用のページ全体マスク下絵を生成して返す."""
    if not export_pipeline.has_pillow():
        _logger.warning("panel camera references require Pillow")
        return _collect_existing_reference_images(work, current_page_id, panel_stem)
    work_dir = Path(work.work_dir)
    ref_dir = reference_dir(work_dir)
    ref_dir.mkdir(parents=True, exist_ok=True)

    refs: list[ReferenceImage] = []
    include_work_blend_mtime = _has_master_gpencil()
    page = find_page_by_id(work, current_page_id)
    panel = _resolve_panel(work, current_page_id, panel_stem)
    if page is not None and panel is not None:
        page_count, render_side, _width_mm, _height_mm = _reference_frame_info(work, current_page_id, panel_stem)
        current_page_ref = _ensure_page_reference(work, work_dir, page, ref_dir, include_work_blend_mtime)
        mate_page = _find_spread_mate_page(work, current_page_id)
        mate_page_ref = None
        if mate_page is not None:
            mate_page_ref = _ensure_page_reference(work, work_dir, mate_page, ref_dir, include_work_blend_mtime)
        masked_page = _koma_ref_path(ref_dir, page.id, panel_stem)
        if _panel_mask_is_stale((current_page_ref, mate_page_ref), masked_page):
            _render_current_panel_page_mask(work, page, panel, current_page_ref, mate_page, mate_page_ref, masked_page)
        if masked_page.is_file():
            refs.insert(
                0,
                ReferenceImage(
                    masked_page,
                    f"{KOMA_REF_PREFIX}_{page.id}_{panel_stem}",
                    "koma",
                    page.id,
                    visible=True,
                    full_page_mask=True,
                    page_count=page_count,
                    render_side=render_side,
                ),
            )
    return refs


def reference_dir(work_dir: Path) -> Path:
    return paths.assets_dir(Path(work_dir)) / REFERENCE_DIR_NAME


def _collect_existing_reference_images(work, current_page_id: str, panel_stem: str) -> list[ReferenceImage]:
    """Pillow が無い環境でも、既存PNGやコマプレビューを下絵として拾う."""
    work_dir = Path(getattr(work, "work_dir", "") or "")
    ref_dir = reference_dir(work_dir)
    refs: list[ReferenceImage] = []
    page_count, render_side, _width_mm, _height_mm = _reference_frame_info(work, current_page_id, panel_stem)
    masked_page = _koma_ref_path(ref_dir, current_page_id, panel_stem)
    legacy_crop = ref_dir / f"{KOMA_REF_PREFIX}_{current_page_id}_{panel_stem}.png"
    crop = masked_page if masked_page.is_file() else legacy_crop
    if crop.is_file():
        refs.insert(
            0,
            ReferenceImage(
                crop,
                f"{KOMA_REF_PREFIX}_{current_page_id}_{panel_stem}",
                "koma",
                current_page_id,
                visible=True,
                full_page_mask=crop == masked_page,
                page_count=page_count,
                render_side=render_side,
            ),
        )
        return refs
    panel = _resolve_panel(work, current_page_id, panel_stem)
    source = panel_preview.panel_preview_source_path(work_dir, current_page_id, panel)
    if source is not None and source.is_file():
        refs.insert(
            0,
            ReferenceImage(
                source,
                f"{KOMA_REF_PREFIX}_{current_page_id}_{panel_stem}",
                "koma",
                current_page_id,
                visible=True,
            ),
        )
    return refs


def _page_ref_path(ref_dir: Path, page_id: str) -> Path:
    return ref_dir / f"{NAME_REF_PREFIX}_pageclean_{page_id}.png"


def _koma_ref_path(ref_dir: Path, page_id: str, panel_stem: str) -> Path:
    return ref_dir / f"{KOMA_REF_PREFIX}_{page_id}_{panel_stem}_page.png"


def _reference_frame_info(work, page_id: str, panel_stem: str = "") -> tuple[int, str, float, float]:
    paper = getattr(work, "paper", None) if work is not None else None
    page = find_page_by_id(work, page_id) if work is not None and page_id else None
    page_width = float(getattr(paper, "canvas_width_mm", 0.0) or 0.0)
    page_height = float(getattr(paper, "canvas_height_mm", 0.0) or 0.0)
    if page is None or page_width <= 0.0 or page_height <= 0.0:
        return 1, "full", page_width, page_height
    if bool(getattr(page, "spread", False)):
        panel = _resolve_panel(work, page_id, panel_stem)
        return 2, _spread_panel_side(panel, page_width), page_width, page_height
    mate = _find_spread_mate_page(work, page_id)
    if mate is None:
        return 1, "full", page_width, page_height
    side = "left" if _is_page_left_half(work, page_id) else "right"
    return 2, side, page_width, page_height


def _spread_panel_side(panel, page_width_mm: float) -> str:
    bbox = _panel_bbox(panel)
    if bbox is None or page_width_mm <= 0.0:
        return "full"
    center_x = (bbox[0] + bbox[2]) * 0.5
    return "left" if center_x < page_width_mm else "right"


def _ensure_page_reference(work, work_dir: Path, page, ref_dir: Path, include_work_blend_mtime: bool) -> Path | None:
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return None
    out = _page_ref_path(ref_dir, page_id)
    if _reference_is_stale(work_dir, page, out, include_work_blend=include_work_blend_mtime):
        _render_page_reference(work, page, out)
    return out if out.is_file() else None


def _find_spread_mate_page(work, current_page_id: str):
    pages = list(getattr(work, "pages", []) or [])
    current_index = next(
        (i for i, page in enumerate(pages) if str(getattr(page, "id", "") or "") == current_page_id),
        -1,
    )
    if current_index < 0:
        return None
    current_page = pages[current_index]
    if bool(getattr(current_page, "spread", False)):
        return None
    paper = getattr(work, "paper", None)
    start_side = str(getattr(paper, "start_side", "right") or "right")
    read_direction = str(getattr(paper, "read_direction", "left") or "left")
    if read_direction == "down":
        return None
    current_slot = page_grid._logical_slot_index(current_index, start_side, read_direction)
    mate_slot = current_slot - 1 if current_slot % 2 else current_slot + 1
    for index, page in enumerate(pages):
        if index == current_index or bool(getattr(page, "spread", False)):
            continue
        slot = page_grid._logical_slot_index(index, start_side, read_direction)
        if slot == mate_slot:
            return page
    return None


def _is_page_left_half(work, page_id: str) -> bool:
    pages = list(getattr(work, "pages", []) or [])
    page_index = next((i for i, page in enumerate(pages) if str(getattr(page, "id", "") or "") == page_id), 0)
    paper = getattr(work, "paper", None)
    start_side = str(getattr(paper, "start_side", "right") or "right")
    read_direction = str(getattr(paper, "read_direction", "left") or "left")
    return page_grid.is_left_half_page(page_index, start_side, read_direction)


def _render_page_reference(work, page, out: Path) -> bool:
    try:
        options = export_pipeline.ExportOptions(
            format="png",
            color_mode="rgb",
            area="canvas",
            dpi_override=DEFAULT_REF_DPI,
            include_tombo=False,
            include_paper_color=True,
            include_panel_previews=False,
        )
        img = export_pipeline.render_page(work, page, options)
        if img is None:
            return False
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out))
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("panel camera page reference render failed: %s", getattr(page, "id", ""))
        return False


def _render_current_panel_page_mask(work, page, panel, page_ref: Path | None, mate_page, mate_ref: Path | None, out: Path) -> bool:
    Image = export_pipeline.Image
    ImageDraw = export_pipeline.ImageDraw
    if Image is None or ImageDraw is None or page_ref is None:
        return False
    try:
        with Image.open(str(page_ref)) as opened:
            page_img = opened.convert("RGBA")
    except Exception:  # noqa: BLE001
        return False
    mate_img = None
    if mate_page is not None and mate_ref is not None and mate_ref.is_file():
        try:
            with Image.open(str(mate_ref)) as opened:
                mate_img = opened.convert("RGBA")
        except Exception:  # noqa: BLE001
            mate_img = None
    canvas, panel_offset_x = _compose_page_reference_pair(work, page, page_img, mate_img)
    points = _panel_points_px(panel, page_img.height, DEFAULT_REF_DPI, panel_offset_x)
    if len(points) < 3:
        return False
    try:
        alpha = canvas.getchannel("A")
        draw = ImageDraw.Draw(alpha)
        draw.polygon(points, fill=0)
        canvas.putalpha(alpha)
        out.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(str(out))
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("panel camera page mask render failed: %s", getattr(page, "id", ""))
        return False


def _compose_page_reference_pair(work, page, page_img, mate_img):
    Image = export_pipeline.Image
    if Image is None:
        return page_img.copy(), 0
    if mate_img is None:
        return page_img.copy(), 0
    page_is_left = _is_page_left_half(work, str(getattr(page, "id", "") or ""))
    width = page_img.width + mate_img.width
    height = max(page_img.height, mate_img.height)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if page_is_left:
        canvas.paste(page_img, (0, 0))
        canvas.paste(mate_img, (page_img.width, 0))
        return canvas, 0
    canvas.paste(mate_img, (0, 0))
    canvas.paste(page_img, (mate_img.width, 0))
    return canvas, mate_img.width


def _panel_points_px(panel, image_height: int, dpi: int, offset_x: int) -> list[tuple[int, int]]:
    points = _panel_points_mm(panel)
    out: list[tuple[int, int]] = []
    for x_mm, y_mm in points:
        x = offset_x + int(round(mm_to_px(x_mm, dpi)))
        y = image_height - int(round(mm_to_px(y_mm, dpi)))
        out.append((x, y))
    return out


def _panel_points_mm(panel) -> list[tuple[float, float]]:
    if panel is None:
        return []
    if getattr(panel, "shape_type", "") == "rect":
        x = float(getattr(panel, "rect_x_mm", 0.0))
        y = float(getattr(panel, "rect_y_mm", 0.0))
        w = float(getattr(panel, "rect_width_mm", 0.0))
        h = float(getattr(panel, "rect_height_mm", 0.0))
        if w <= 0.0 or h <= 0.0:
            return []
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return [(float(v.x_mm), float(v.y_mm)) for v in getattr(panel, "vertices", [])]


def _reference_is_stale(work_dir: Path, page, out: Path, *, include_work_blend: bool) -> bool:
    if not out.is_file():
        return True
    latest = _path_mtime(paths.work_meta_path(work_dir))
    if include_work_blend:
        latest = max(latest, _path_mtime(paths.work_blend_path(work_dir)))
    latest = max(latest, _path_mtime(paths.pages_meta_path(work_dir)))
    latest = max(latest, _path_mtime(paths.page_meta_path(work_dir, page.id)))
    for panel in getattr(page, "panels", []):
        source = panel_preview.panel_preview_source_path(work_dir, page.id, panel)
        if source is not None:
            latest = max(latest, _path_mtime(source))
    return _path_mtime(out) < latest


def _has_master_gpencil() -> bool:
    try:
        from . import gpencil as gp_utils

        return gp_utils.get_master_gpencil() is not None
    except Exception:  # noqa: BLE001
        return False


def _panel_mask_is_stale(page_refs: Iterable[Path | None], out: Path) -> bool:
    valid_refs = [Path(ref) for ref in page_refs if ref is not None and Path(ref).is_file()]
    if not valid_refs:
        return False
    if not out.is_file():
        return True
    out_mtime = _path_mtime(out)
    return any(out_mtime < _path_mtime(ref) for ref in valid_refs)


def _path_mtime(path: Path) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def _resolve_panel(work, page_id: str, panel_stem: str):
    page = find_page_by_id(work, page_id) if work is not None else None
    if page is None:
        return None
    for panel in getattr(page, "panels", []):
        if getattr(panel, "panel_stem", "") == panel_stem:
            return panel
    return None


def _panel_bbox_size(panel) -> tuple[float, float]:
    bbox = _panel_bbox(panel)
    if bbox is None:
        return 0.0, 0.0
    return max(0.0, bbox[2] - bbox[0]), max(0.0, bbox[3] - bbox[1])


def _panel_bbox(panel) -> tuple[float, float, float, float] | None:
    if panel is None:
        return None
    if getattr(panel, "shape_type", "") == "rect":
        x = float(getattr(panel, "rect_x_mm", 0.0))
        y = float(getattr(panel, "rect_y_mm", 0.0))
        w = float(getattr(panel, "rect_width_mm", 0.0))
        h = float(getattr(panel, "rect_height_mm", 0.0))
        if w <= 0.0 or h <= 0.0:
            return None
        return x, y, x + w, y + h
    verts = [(float(v.x_mm), float(v.y_mm)) for v in getattr(panel, "vertices", [])]
    if not verts:
        return None
    xs = [p[0] for p in verts]
    ys = [p[1] for p in verts]
    return min(xs), min(ys), max(xs), max(ys)
