"""Viewport overlay drawing for selected B-Name effect-line layers."""

from __future__ import annotations

from collections.abc import Callable

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
    if getattr(context.scene, "bname_active_layer_kind", "") != "effect":
        return
    try:
        from ..operators import effect_line_op

        _obj, _layer, bounds = effect_line_op.active_effect_layer_bounds(context)
    except Exception:  # noqa: BLE001
        if logger is not None:
            logger.exception("active effect bounds resolve failed")
        return
    if bounds is None:
        return
    rect = Rect(float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
    draw_rect_outline(rect.inset(-1.0), (1.0, 0.6, 0.0, 0.9), width_mm=0.50)
    for handle in _handle_rects(rect):
        draw_rect_fill(handle, (1.0, 1.0, 1.0, 0.95))
        draw_rect_outline(handle, (1.0, 0.6, 0.0, 0.95), width_mm=0.25)
