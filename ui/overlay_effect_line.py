"""Viewport overlay drawing for selected B-Name effect-line layers."""

from __future__ import annotations

from collections.abc import Callable

from ..utils import object_selection, viewport_colors
from ..utils.geom import Rect

DrawRectFill = Callable[[Rect, tuple[float, float, float, float]], None]
DrawRectOutline = Callable[..., None]

_HANDLE_SIZE_MM = 2.0


def _handle_rects(rect: Rect) -> list[Rect]:
    half = _HANDLE_SIZE_MM * 0.5
    points = (
        (rect.x, rect.y),
        (rect.x + rect.width * 0.5, rect.y),
        (rect.x2, rect.y),
        (rect.x, rect.y + rect.height * 0.5),
        (rect.x2, rect.y + rect.height * 0.5),
        (rect.x, rect.y2),
        (rect.x + rect.width * 0.5, rect.y2),
        (rect.x2, rect.y2),
    )
    return [Rect(x - half, y - half, _HANDLE_SIZE_MM, _HANDLE_SIZE_MM) for x, y in points]


def draw_active_effect_line_bounds(
    context,
    *,
    draw_rect_fill: DrawRectFill,
    draw_rect_outline: DrawRectOutline,
    logger=None,
) -> None:
    selected_names = object_selection.selected_effect_names(context)
    active_effect = getattr(context.scene, "bname_active_layer_kind", "") == "effect"
    if not active_effect and not selected_names:
        return
    try:
        from ..operators import effect_line_op

        obj, layer, bounds = effect_line_op.active_effect_layer_bounds(context)
    except Exception:  # noqa: BLE001
        if logger is not None:
            logger.exception("active effect bounds resolve failed")
        return
    drawn: set[str] = set()
    if active_effect and bounds is not None:
        _draw_bounds(bounds, draw_rect_fill=draw_rect_fill, draw_rect_outline=draw_rect_outline)
        if layer is not None:
            drawn.add(str(getattr(layer, "name", "") or ""))
    if selected_names:
        obj = effect_line_op.layer_stack_utils.get_effect_gp_object()
        layers = getattr(getattr(obj, "data", None), "layers", None) if obj is not None else None
        for selected_name in selected_names:
            if selected_name in drawn or layers is None:
                continue
            selected_layer = effect_line_op.layer_stack_utils._find_gp_layer_by_key(layers, selected_name)
            if selected_layer is None:
                for candidate in layers:
                    if str(getattr(candidate, "name", "") or "") == selected_name:
                        selected_layer = candidate
                        break
            selected_bounds = effect_line_op.effect_layer_bounds(obj, selected_layer)
            if selected_bounds is not None:
                _draw_bounds(selected_bounds, draw_rect_fill=draw_rect_fill, draw_rect_outline=draw_rect_outline)


def _draw_bounds(
    bounds,
    *,
    draw_rect_fill: DrawRectFill,
    draw_rect_outline: DrawRectOutline,
) -> None:
    rect = Rect(float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
    draw_rect_outline(rect.inset(-1.0), viewport_colors.SELECTION, width_mm=0.50)
    for handle in _handle_rects(rect):
        draw_rect_fill(handle, viewport_colors.HANDLE_FILL)
        draw_rect_outline(handle, viewport_colors.HANDLE_OUTLINE, width_mm=0.25)
