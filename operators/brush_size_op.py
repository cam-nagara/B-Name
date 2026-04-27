"""ブラシサイズを Ctrl+Alt+ドラッグで調整する Operator."""

from __future__ import annotations

import math

import bpy
from bpy.types import Operator
import gpu
from gpu_extras.batch import batch_for_shader

from ..core.work import get_work

_GP_PAINT_ATTRS = ("grease_pencil_paint", "gpencil_paint", "gpencil_v3_paint")
_CIRCLE_SEGMENTS = 96


def _draw_polyline(points: list[tuple[float, float]], color, *, width: float = 1.0) -> None:
    if len(points) < 2:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": points})
    try:
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(width)
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)
    finally:
        try:
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set("NONE")
        except Exception:  # noqa: BLE001
            pass


def _circle_points(
    cx: float,
    cy: float,
    radius: float,
    *,
    start_angle: float = 0.0,
) -> list[tuple[float, float]]:
    radius = max(0.0, float(radius))
    if radius <= 0.0:
        return []
    points = []
    for i in range(_CIRCLE_SEGMENTS + 1):
        angle = start_angle + 2.0 * math.pi * i / _CIRCLE_SEGMENTS
        points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    return points


def _distance_px(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def _draw_brush_size_preview(op) -> None:
    if not bool(getattr(op, "_preview_active", False)):
        return
    cx = float(getattr(op, "_start_mouse_region_x", 0.0))
    cy = float(getattr(op, "_start_mouse_region_y", 0.0))
    mx = float(getattr(op, "_current_mouse_region_x", cx))
    my = float(getattr(op, "_current_mouse_region_y", cy))
    radius = max(
        0.0,
        float(getattr(op, "_preview_radius", getattr(op, "_last_size", 0))),
    )
    start_angle = math.atan2(my - cy, mx - cx) if radius > 0.0 else 0.0
    try:
        _draw_polyline(
            _circle_points(cx, cy, radius, start_angle=start_angle),
            (0.2, 0.65, 1.0, 0.95),
            width=2.0,
        )
        _draw_polyline([(cx, cy), (mx, my)], (0.2, 0.65, 1.0, 0.45), width=1.0)
        cross = 5.0
        _draw_polyline([(cx - cross, cy), (cx + cross, cy)], (1.0, 1.0, 1.0, 0.85), width=1.0)
        _draw_polyline([(cx, cy - cross), (cx, cy + cross)], (1.0, 1.0, 1.0, 0.85), width=1.0)
    except Exception:  # noqa: BLE001
        pass


def _active_gp_brush(context):
    tool_settings = getattr(context, "tool_settings", None)
    if tool_settings is None:
        return None
    for attr in _GP_PAINT_ATTRS:
        paint = getattr(tool_settings, attr, None)
        brush = getattr(paint, "brush", None) if paint is not None else None
        if brush is not None and hasattr(brush, "size"):
            return brush
    return None


def _active_gp_object(context):
    obj = getattr(context, "active_object", None)
    if obj is not None and getattr(obj, "type", "") == "GREASEPENCIL":
        return obj
    try:
        from ..utils import gpencil as gp_utils

        return gp_utils.get_master_gpencil()
    except Exception:  # noqa: BLE001
        return None


def _is_gp_paint_context(context) -> bool:
    obj = _active_gp_object(context)
    if obj is None:
        return False
    mode = str(getattr(context, "mode", "") or getattr(obj, "mode", ""))
    return "PAINT" in mode and _active_gp_brush(context) is not None


def _brush_size_limits(brush, *, allow_zero: bool = False) -> tuple[int, int]:
    prop = None
    try:
        prop = brush.bl_rna.properties["size"]
    except Exception:  # noqa: BLE001
        prop = None
    min_size = 0 if allow_zero else 1
    hard_max = getattr(prop, "hard_max", 500) if prop is not None else 500
    max_size = int(max(min_size, hard_max if hard_max else 500))
    return min_size, max_size


def _clamp_size(value: float, brush, *, allow_zero: bool = False) -> int:
    min_size, max_size = _brush_size_limits(brush, allow_zero=allow_zero)
    return max(min_size, min(max_size, int(round(value))))


def _set_brush_size(brush, size: int) -> int:
    size = _clamp_size(size, brush)
    brush.size = size
    return size


def _ensure_drawable_brush_size(brush) -> int:
    current_size = int(getattr(brush, "size", 1))
    if current_size < 1:
        return _set_brush_size(brush, 1)
    return current_size


class BNAME_OT_brush_size_drag(Operator):
    """Ctrl+Alt+ドラッグで Grease Pencil ブラシサイズを変更する."""

    bl_idname = "bname.brush_size_drag"
    bl_label = "ブラシサイズドラッグ調整"
    bl_options = {"REGISTER", "BLOCKING"}

    _brush: object | None
    _start_mouse_region_x: int
    _start_mouse_region_y: int
    _current_mouse_region_x: int
    _current_mouse_region_y: int
    _start_size: int
    _last_size: int
    _display_size: int
    _preview_radius: float
    _draw_handler: object | None
    _preview_active: bool

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded and _is_gp_paint_context(context))

    def invoke(self, context, event):
        if (
            event.type != "LEFTMOUSE"
            or event.value != "PRESS"
            or not event.ctrl
            or not event.alt
        ):
            return {"PASS_THROUGH"}
        brush = _active_gp_brush(context)
        if brush is None:
            return {"PASS_THROUGH"}
        self._brush = brush
        self._start_mouse_region_x = int(getattr(event, "mouse_region_x", 0))
        self._start_mouse_region_y = int(getattr(event, "mouse_region_y", 0))
        self._current_mouse_region_x = self._start_mouse_region_x
        self._current_mouse_region_y = self._start_mouse_region_y
        self._start_size = _ensure_drawable_brush_size(brush)
        self._preview_radius = 0.0
        self._display_size = 0
        self._last_size = self._start_size
        self._draw_handler = None
        self._preview_active = True
        try:
            self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
                _draw_brush_size_preview,
                (self,),
                "WINDOW",
                "POST_PIXEL",
            )
        except Exception:  # noqa: BLE001
            self._draw_handler = None
        context.window_manager.modal_handler_add(self)
        self._set_status(context)
        self._tag_redraw(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        brush = getattr(self, "_brush", None)
        if brush is None:
            self._finish(context)
            return {"CANCELLED"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            _set_brush_size(brush, self._start_size)
            self._finish(context)
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._finish(context)
            return {"FINISHED"}
        if event.type == "MOUSEMOVE":
            self._current_mouse_region_x = int(getattr(event, "mouse_region_x", 0))
            self._current_mouse_region_y = int(getattr(event, "mouse_region_y", 0))
            radius = _distance_px(
                self._start_mouse_region_x,
                self._start_mouse_region_y,
                self._current_mouse_region_x,
                self._current_mouse_region_y,
            )
            self._preview_radius = radius
            self._display_size = _clamp_size(radius, brush, allow_zero=True)
            self._last_size = _set_brush_size(
                brush,
                radius,
            )
            self._set_status(context)
            self._tag_redraw(context)
        return {"RUNNING_MODAL"}

    def _set_status(self, context) -> None:
        area = getattr(context, "area", None)
        if area is not None:
            try:
                area.header_text_set(f"ブラシサイズ: {self._display_size}px")
            except Exception:  # noqa: BLE001
                pass
        workspace = getattr(context, "workspace", None)
        if workspace is not None:
            try:
                workspace.status_text_set("Ctrl+Alt+ドラッグ: ブラシサイズ / Esc: 取消")
            except Exception:  # noqa: BLE001
                pass

    def _clear_status(self, context) -> None:
        area = getattr(context, "area", None)
        if area is not None:
            try:
                area.header_text_set(None)
            except Exception:  # noqa: BLE001
                pass
        workspace = getattr(context, "workspace", None)
        if workspace is not None:
            try:
                workspace.status_text_set(None)
            except Exception:  # noqa: BLE001
                pass

    def _tag_redraw(self, context) -> None:
        area = getattr(context, "area", None)
        if area is not None:
            try:
                area.tag_redraw()
            except Exception:  # noqa: BLE001
                pass

    def _finish(self, context) -> None:
        self._preview_active = False
        handler = getattr(self, "_draw_handler", None)
        if handler is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
            except Exception:  # noqa: BLE001
                pass
            self._draw_handler = None
        self._clear_status(context)
        self._tag_redraw(context)


_CLASSES = (BNAME_OT_brush_size_drag,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
