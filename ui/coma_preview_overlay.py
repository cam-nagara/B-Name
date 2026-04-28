"""コマプレビュー画像をビューポート上のコマ形状へ描画するヘルパ."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..utils import image_transparency
from ..utils import coma_preview
from ..utils.geom import mm_to_m


def draw_coma_preview(work, page, entry, ox_mm: float = 0.0, oy_mm: float = 0.0) -> bool:
    """cNN_preview/thumb をコマ形状内へ描画する."""
    if work is None or page is None or not getattr(work, "work_dir", ""):
        return False
    poly = _coma_polygon_mm(entry)
    if len(poly) < 3:
        return False
    source = coma_preview.coma_preview_source_path(Path(work.work_dir), page.id, entry)
    if source is None:
        return False
    source = _display_source_for_panel(source, entry)
    img = _ensure_bpy_image_current(source)
    if img is None:
        return False
    bbox = _bbox(poly)
    if bbox is None:
        return False
    min_x, min_y, max_x, max_y = bbox
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0.0 or height <= 0.0:
        return False

    verts = [
        (mm_to_m(x + ox_mm), mm_to_m(y + oy_mm), 0.0)
        for x, y in poly
    ]
    uvs = [
        ((x - min_x) / width, (y - min_y) / height)
        for x, y in poly
    ]
    indices = [(0, i, i + 1) for i in range(1, len(poly) - 1)]
    if not indices:
        return False

    try:
        import gpu.texture as gpu_texture  # type: ignore

        tex = gpu_texture.from_image(img)
    except Exception:  # noqa: BLE001
        return False

    shader = gpu.shader.from_builtin("IMAGE")
    batch = batch_for_shader(
        shader,
        "TRIS",
        {"pos": verts, "texCoord": uvs},
        indices=indices,
    )
    shader.bind()
    shader.uniform_sampler("image", tex)
    gpu.state.blend_set("ALPHA")
    batch.draw(shader)
    return True


def _display_source_for_panel(source: Path, entry) -> Path:
    if not image_transparency.coma_background_is_transparent(entry):
        return source
    from ..io import export_pipeline

    Image = export_pipeline.Image
    if Image is None:
        return source
    try:
        source_mtime = source.stat().st_mtime
    except OSError:
        return source
    cache_path = _transparent_cache_path(source)
    try:
        cache_mtime = cache_path.stat().st_mtime
    except OSError:
        cache_mtime = -1.0
    if cache_path.is_file() and cache_mtime >= source_mtime:
        return cache_path
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(str(source)) as opened:
            image = image_transparency.make_background_transparent(opened)
            image.save(str(cache_path))
        return cache_path
    except Exception:  # noqa: BLE001
        return source


def _transparent_cache_path(source: Path) -> Path:
    resolved = str(Path(source).resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "bname_coma_preview_alpha" / f"{digest}.png"


def _coma_polygon_mm(entry) -> list[tuple[float, float]]:
    shape = getattr(entry, "shape_type", "")
    if shape == "rect":
        x = float(getattr(entry, "rect_x_mm", 0.0))
        y = float(getattr(entry, "rect_y_mm", 0.0))
        w = float(getattr(entry, "rect_width_mm", 0.0))
        h = float(getattr(entry, "rect_height_mm", 0.0))
        if w <= 0.0 or h <= 0.0:
            return []
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if shape == "polygon":
        return [(float(v.x_mm), float(v.y_mm)) for v in getattr(entry, "vertices", [])]
    return []


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _ensure_bpy_image_current(path: Path):
    abspath = str(Path(path).resolve())
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return None

    for img in bpy.data.images:
        try:
            if str(Path(bpy.path.abspath(img.filepath)).resolve()) != abspath:
                continue
            if float(img.get("_bname_mtime", -1.0)) != mtime:
                img.reload()
                img["_bname_mtime"] = mtime
            return img
        except Exception:  # noqa: BLE001
            continue

    try:
        img = bpy.data.images.load(abspath, check_existing=True)
        img["_bname_mtime"] = mtime
        return img
    except Exception:  # noqa: BLE001
        return None
