"""Helpers for exporting full pages or spread halves."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import export_pipeline, export_psd
from .export_pipeline import ExportLayer, ExportOptions


def _shift_box(box: tuple[int, int, int, int], dx: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    return left + dx, top, right + dx, bottom


def _union_box(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _full_canvas_box(work, page, options: ExportOptions) -> tuple[int, int, int, int]:
    width, height = export_pipeline._page_canvas_size_px(work, page, options)
    return 0, 0, width, height


def _spread_side_crop_box(work, options: ExportOptions, side: str) -> tuple[int, int, int, int]:
    page_width, page_height = export_pipeline._canvas_size_px(work.paper, options)
    if options.area == "canvas":
        box = (0, 0, page_width, page_height)
    else:
        box = export_pipeline._area_rect_px(
            work.paper,
            options,
            is_left_half=(side == "left"),
        )
    if side == "right":
        box = _shift_box(box, page_width)
    return box


def page_crop_box(
    work,
    page,
    options: ExportOptions,
    *,
    spread_side: str | None = None,
) -> tuple[int, int, int, int]:
    """Return the pixel crop box for a page or a spread half."""
    is_spread = bool(getattr(page, "spread", False))
    if is_spread and spread_side in {"left", "right"}:
        return _spread_side_crop_box(work, options, spread_side)
    if is_spread:
        if options.area == "canvas":
            return _full_canvas_box(work, page, options)
        return _union_box(
            [
                _spread_side_crop_box(work, options, "left"),
                _spread_side_crop_box(work, options, "right"),
            ]
        )
    if options.area == "canvas":
        return _full_canvas_box(work, page, options)
    return export_pipeline._area_rect_px(
        work.paper,
        options,
        is_left_half=export_pipeline._is_left_half_page(work, page),
    )


def _crop_needed(
    work,
    page,
    options: ExportOptions,
    crop_box: tuple[int, int, int, int],
) -> bool:
    return crop_box != _full_canvas_box(work, page, options)


def build_page_region_layers(
    work,
    page,
    options: ExportOptions,
    *,
    spread_side: str | None = None,
) -> tuple[list[ExportLayer], tuple[int, int], dict[tuple[str, ...], Any]]:
    """Build layers cropped to a full page or a spread half."""
    layers = export_pipeline.build_page_layers(work, page, options)
    masks = export_pipeline._coma_group_masks(work, page, options)
    crop_box = page_crop_box(work, page, options, spread_side=spread_side)
    if _crop_needed(work, page, options, crop_box):
        layers, size = export_pipeline._crop_layers(layers, crop_box)
        masks = export_pipeline._crop_group_masks(masks, crop_box)
    else:
        size = export_pipeline._page_canvas_size_px(work, page, options)
    return layers, size, masks


def render_page_region(
    work,
    page,
    options: ExportOptions,
    *,
    spread_side: str | None = None,
) -> Any:
    """Render a flattened image for a page or a spread half."""
    if not export_pipeline.has_pillow():
        return None
    layers, size, _masks = build_page_region_layers(
        work,
        page,
        options,
        spread_side=spread_side,
    )
    image = export_pipeline._flatten_layers(layers, size)
    return export_pipeline._convert_flatten_mode(image, options)


def save_page_region_as_psd(
    work,
    page,
    options: ExportOptions,
    out_path: Path,
    *,
    spread_side: str | None = None,
) -> bool:
    """Save a layered PSD for a page or a spread half."""
    if not export_pipeline.has_pillow():
        raise RuntimeError("Pillow が利用できません")
    if not export_psd.can_write_layered_psd():
        raise RuntimeError("PSD レイヤー出力を利用できません")
    if options.color_mode == "cmyk":
        raise RuntimeError("PSD レイヤー出力での CMYK は未対応です")
    layers, size, masks = build_page_region_layers(
        work,
        page,
        options,
        spread_side=spread_side,
    )
    layers = [
        export_pipeline._convert_layer_mode_rgba(layer, options.color_mode)
        for layer in layers
    ]
    if not layers:
        layers = [ExportLayer("empty", export_pipeline._empty_rgba(size), 0, 0)]
    ok = export_psd.save_layers_as_psd(layers, size, out_path, group_masks=masks)
    if not ok:
        raise RuntimeError("PSD 保存に失敗しました")
    return True
