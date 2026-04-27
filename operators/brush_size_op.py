"""ブラシサイズを Ctrl+Alt+ドラッグで調整する Operator."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..core.work import get_work

_GP_PAINT_ATTRS = ("grease_pencil_paint", "gpencil_paint", "gpencil_v3_paint")
_SENSITIVITY = 0.5


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


def _brush_size_limits(brush) -> tuple[int, int]:
    prop = None
    try:
        prop = brush.bl_rna.properties["size"]
    except Exception:  # noqa: BLE001
        prop = None
    min_size = int(max(1, getattr(prop, "hard_min", 1) if prop is not None else 1))
    hard_max = getattr(prop, "hard_max", 500) if prop is not None else 500
    max_size = int(max(min_size, hard_max if hard_max else 500))
    return min_size, max_size


def _clamp_size(value: float, brush) -> int:
    min_size, max_size = _brush_size_limits(brush)
    return max(min_size, min(max_size, int(round(value))))


def _set_brush_size(brush, size: int) -> int:
    size = _clamp_size(size, brush)
    brush.size = size
    return size


class BNAME_OT_brush_size_drag(Operator):
    """Ctrl+Alt+ドラッグで Grease Pencil ブラシサイズを変更する."""

    bl_idname = "bname.brush_size_drag"
    bl_label = "ブラシサイズドラッグ調整"
    bl_options = {"REGISTER", "BLOCKING"}

    _brush: object | None
    _start_mouse_x: int
    _start_size: int
    _last_size: int

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
        self._start_mouse_x = int(event.mouse_x)
        self._start_size = int(getattr(brush, "size", 1))
        self._last_size = self._start_size
        context.window_manager.modal_handler_add(self)
        self._set_status(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        brush = getattr(self, "_brush", None)
        if brush is None:
            self._clear_status(context)
            return {"CANCELLED"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            _set_brush_size(brush, self._start_size)
            self._clear_status(context)
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._clear_status(context)
            return {"FINISHED"}
        if event.type == "MOUSEMOVE":
            delta = int(event.mouse_x) - self._start_mouse_x
            self._last_size = _set_brush_size(
                brush,
                self._start_size + delta * _SENSITIVITY,
            )
            self._set_status(context)
            area = getattr(context, "area", None)
            if area is not None:
                area.tag_redraw()
        return {"RUNNING_MODAL"}

    def _set_status(self, context) -> None:
        area = getattr(context, "area", None)
        if area is not None:
            try:
                area.header_text_set(f"ブラシサイズ: {self._last_size}px")
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
