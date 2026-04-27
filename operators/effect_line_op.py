"""効果線 Operator.

ビューポート上のドラッグ範囲から Grease Pencil の効果線レイヤーを作成し、
作成済み効果線の移動・リサイズを扱う。
"""

from __future__ import annotations

import json

import bpy
from bpy.types import Operator

from ..core.mode import MODE_PANEL, get_mode
from ..core.work import get_work
from ..utils import log
from ..utils.geom import m_to_mm
from ..utils import layer_stack as layer_stack_utils
from . import panel_modal_state

_logger = log.get_logger(__name__)

_EFFECT_META_PROP = "bname_effect_line_meta"
_EFFECT_MIN_SIZE_MM = 2.0
_EFFECT_HANDLE_HIT_MM = 2.5
_EFFECT_DRAG_EPS_MM = 0.05


def _unique_layer_name(gp_data, base: str) -> str:
    existing = {layer.name for layer in getattr(gp_data, "layers", [])}
    if base not in existing:
        return base
    i = 1
    while True:
        candidate = f"{base}.{i:03d}"
        if candidate not in existing:
            return candidate
        i += 1


def _effect_meta(obj) -> dict:
    data = getattr(obj, "data", None)
    if data is None:
        return {}
    try:
        raw = data.get(_EFFECT_META_PROP, "{}")
    except Exception:  # noqa: BLE001
        return {}
    try:
        parsed = json.loads(str(raw or "{}"))
    except Exception:  # noqa: BLE001
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_effect_meta(obj, meta: dict) -> None:
    data = getattr(obj, "data", None)
    if data is None:
        return
    try:
        data[_EFFECT_META_PROP] = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    except Exception:  # noqa: BLE001
        _logger.exception("effect_line: metadata write failed")


def _layer_meta_key(layer) -> str:
    return str(getattr(layer, "name", "") or "")


def _set_layer_bounds(obj, layer, bounds: tuple[float, float, float, float], *, seed: int | None = None) -> None:
    x, y, w, h = bounds
    meta = _effect_meta(obj)
    key = _layer_meta_key(layer)
    prev = meta.get(key, {}) if isinstance(meta.get(key, {}), dict) else {}
    if seed is None:
        try:
            seed = int(prev.get("seed", 0))
        except Exception:  # noqa: BLE001
            seed = 0
    meta[key] = {
        "x": float(x),
        "y": float(y),
        "w": max(_EFFECT_MIN_SIZE_MM, float(w)),
        "h": max(_EFFECT_MIN_SIZE_MM, float(h)),
        "seed": int(seed or 0),
    }
    _write_effect_meta(obj, meta)


def _remove_layer_bounds(obj, layer) -> None:
    meta = _effect_meta(obj)
    key = _layer_meta_key(layer)
    if key in meta:
        meta.pop(key, None)
        _write_effect_meta(obj, meta)


def _frame_drawing(layer):
    from ..utils import gpencil

    frame = gpencil.ensure_active_frame(layer)
    return getattr(frame, "drawing", None) if frame is not None else None


def _clear_drawing(drawing) -> None:
    if drawing is None:
        return
    try:
        drawing.remove_strokes()
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        count = len(getattr(drawing, "strokes", []))
        if count > 0:
            drawing.remove_strokes(indices=tuple(range(count)))
    except Exception:  # noqa: BLE001
        _logger.exception("effect_line: clear drawing failed")


def _stroke_bounds(layer) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for frame in getattr(layer, "frames", []) or []:
        drawing = getattr(frame, "drawing", None)
        for stroke in getattr(drawing, "strokes", []) or []:
            for point in getattr(stroke, "points", []) or []:
                pos = getattr(point, "position", None)
                if pos is None:
                    continue
                try:
                    xs.append(m_to_mm(float(pos[0])))
                    ys.append(m_to_mm(float(pos[1])))
                except Exception:  # noqa: BLE001
                    continue
    if not xs or not ys:
        return None
    left = min(xs)
    bottom = min(ys)
    return left, bottom, max(_EFFECT_MIN_SIZE_MM, max(xs) - left), max(_EFFECT_MIN_SIZE_MM, max(ys) - bottom)


def effect_layer_bounds(obj, layer) -> tuple[float, float, float, float] | None:
    if obj is None or layer is None:
        return None
    key = _layer_meta_key(layer)
    stored = _effect_meta(obj).get(key)
    if isinstance(stored, dict):
        try:
            x = float(stored.get("x", 0.0))
            y = float(stored.get("y", 0.0))
            w = max(_EFFECT_MIN_SIZE_MM, float(stored.get("w", _EFFECT_MIN_SIZE_MM)))
            h = max(_EFFECT_MIN_SIZE_MM, float(stored.get("h", _EFFECT_MIN_SIZE_MM)))
            return x, y, w, h
        except Exception:  # noqa: BLE001
            pass
    return _stroke_bounds(layer)


def active_effect_layer_bounds(context=None):
    ctx = context or bpy.context
    from ..utils import layer_stack as stack_utils

    obj = stack_utils.get_effect_gp_object()
    if obj is None:
        return None, None, None
    layers = getattr(getattr(obj, "data", None), "layers", None)
    key = str(getattr(getattr(ctx, "scene", None), "bname_active_effect_layer_name", "") or "")
    active = stack_utils._find_gp_layer_by_key(layers, key) if key else None
    if active is None:
        active = getattr(layers, "active", None) if layers is not None else None
    bounds = effect_layer_bounds(obj, active)
    if bounds is None:
        return obj, active, None
    return obj, active, bounds


def _set_active_effect_layer(context, obj, layer) -> None:
    if obj is not None:
        try:
            context.view_layer.objects.active = obj
            obj.select_set(True)
        except Exception:  # noqa: BLE001
            pass
    if obj is not None and layer is not None:
        try:
            obj.data.layers.active = layer
        except Exception:  # noqa: BLE001
            pass
    scene = getattr(context, "scene", None)
    if scene is not None and layer is not None:
        if hasattr(scene, "bname_active_layer_kind"):
            scene.bname_active_layer_kind = "effect"
        if hasattr(scene, "bname_active_effect_layer_name"):
            scene.bname_active_effect_layer_name = layer_stack_utils._node_stack_key(layer)


def _select_effect_layer(context, obj, layer) -> None:
    _set_active_effect_layer(context, obj, layer)
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    uid = layer_stack_utils.target_uid("effect", layer_stack_utils._node_stack_key(layer))
    if stack is not None:
        for i, item in enumerate(stack):
            if layer_stack_utils.stack_item_uid(item) == uid:
                layer_stack_utils.set_active_stack_index_silently(context, i)
                break
    layer_stack_utils.remember_layer_stack_signature(context)
    layer_stack_utils.tag_view3d_redraw(context)


def _seed_for_new_layer(obj) -> int:
    meta = _effect_meta(obj)
    used = []
    for item in meta.values():
        if isinstance(item, dict):
            try:
                used.append(int(item.get("seed", 0)))
            except Exception:  # noqa: BLE001
                pass
    return (max(used) + 1) if used else 1


def _seed_for_layer(obj, layer) -> int:
    stored = _effect_meta(obj).get(_layer_meta_key(layer), {})
    if isinstance(stored, dict):
        try:
            return int(stored.get("seed", 0))
        except Exception:  # noqa: BLE001
            return 0
    return 0


def _write_effect_strokes(context, obj, layer, bounds: tuple[float, float, float, float], *, seed: int | None = None) -> int:
    from ..utils import gpencil
    from . import effect_line_gen

    params = getattr(context.scene, "bname_effect_line_params", None)
    if params is None:
        return 0
    x, y, w, h = bounds
    w = max(_EFFECT_MIN_SIZE_MM, float(w))
    h = max(_EFFECT_MIN_SIZE_MM, float(h))
    cx = float(x) + w * 0.5
    cy = float(y) + h * 0.5
    seed_value = _seed_for_layer(obj, layer) if seed is None else int(seed)
    drawing = _frame_drawing(layer)
    if drawing is None:
        return 0
    _clear_drawing(drawing)
    strokes = effect_line_gen.generate_strokes(
        params,
        center_xy_mm=(cx, cy),
        radius_xy_mm=(w * 0.5, h * 0.5),
        seed=seed_value,
    )
    added = 0
    for stroke in strokes:
        if gpencil.add_stroke_to_drawing(
            drawing,
            stroke.points_xyz,
            radius=stroke.radius,
            cyclic=stroke.cyclic,
        ):
            added += 1
    _set_layer_bounds(obj, layer, (float(x), float(y), w, h), seed=seed_value)
    return added


def _create_effect_layer(context, bounds: tuple[float, float, float, float] | None = None):
    from ..utils import gpencil

    obj = gpencil.ensure_gpencil_object(layer_stack_utils.EFFECT_GP_OBJECT_NAME)
    gp_data = obj.data
    params = getattr(context.scene, "bname_effect_line_params", None)
    suffix = getattr(params, "effect_type", "effect") if params is not None else "effect"
    layer_name = _unique_layer_name(gp_data, f"effect_{suffix}")
    layer = gp_data.layers.new(layer_name)
    gp_data.layers.active = layer
    seed = _seed_for_new_layer(obj)
    if bounds is None:
        bounds = (70.0, 110.0, 80.0, 100.0)
    _write_effect_strokes(context, obj, layer, bounds, seed=seed)
    _select_effect_layer(context, obj, layer)
    layer_stack_utils.sync_layer_stack_after_data_change(context)
    return obj, layer


def _delete_effect_layer(context, obj, layer) -> None:
    if obj is None or layer is None:
        return
    _remove_layer_bounds(obj, layer)
    try:
        obj.data.layers.remove(layer)
    except Exception:  # noqa: BLE001
        return
    if hasattr(context.scene, "bname_active_effect_layer_name"):
        context.scene.bname_active_effect_layer_name = ""
    layer_stack_utils.sync_layer_stack_after_data_change(context)


def _event_world_xy_mm(context, event) -> tuple[float | None, float | None]:
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    from ..utils import geom

    screen = getattr(context, "screen", None)
    if screen is None:
        return None, None
    mouse_x = int(getattr(event, "mouse_x", -10_000_000))
    mouse_y = int(getattr(event, "mouse_y", -10_000_000))
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if not (
                region.x <= mouse_x < region.x + region.width
                and region.y <= mouse_y < region.y + region.height
            ):
                continue
            rv3d = getattr(area.spaces.active, "region_3d", None)
            if rv3d is None:
                continue
            loc = region_2d_to_location_3d(
                region,
                rv3d,
                (mouse_x - region.x, mouse_y - region.y),
                (0.0, 0.0, 0.0),
            )
            if loc is None:
                continue
            return geom.m_to_mm(loc.x), geom.m_to_mm(loc.y)
    return None, None


def _event_in_view3d_window(context, event) -> bool:
    screen = getattr(context, "screen", None)
    if screen is None:
        return False
    mouse_x = int(getattr(event, "mouse_x", -10_000_000))
    mouse_y = int(getattr(event, "mouse_y", -10_000_000))
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if (
                region.x <= mouse_x < region.x + region.width
                and region.y <= mouse_y < region.y + region.height
            ):
                return True
    return False


def _effect_hit_part(bounds: tuple[float, float, float, float], x_mm: float, y_mm: float) -> str:
    x, y, w, h = bounds
    left, bottom, right, top = x, y, x + w, y + h
    threshold = min(_EFFECT_HANDLE_HIT_MM, max(0.35, min(w, h) * 0.25))
    if not (
        left - threshold <= x_mm <= right + threshold
        and bottom - threshold <= y_mm <= top + threshold
    ):
        return ""
    near_left = abs(x_mm - left) <= threshold
    near_right = abs(x_mm - right) <= threshold
    near_bottom = abs(y_mm - bottom) <= threshold
    near_top = abs(y_mm - top) <= threshold
    inside_x = left <= x_mm <= right
    inside_y = bottom <= y_mm <= top
    if near_left and near_top:
        return "top_left"
    if near_right and near_top:
        return "top_right"
    if near_left and near_bottom:
        return "bottom_left"
    if near_right and near_bottom:
        return "bottom_right"
    if near_left and inside_y:
        return "left"
    if near_right and inside_y:
        return "right"
    if near_top and inside_x:
        return "top"
    if near_bottom and inside_x:
        return "bottom"
    if inside_x and inside_y:
        return "body"
    return ""


def _hit_effect_layer(context, x_mm: float, y_mm: float):
    from ..utils import gpencil

    obj = layer_stack_utils.get_effect_gp_object()
    if obj is None:
        return None, None, None, ""
    layers = list(getattr(getattr(obj, "data", None), "layers", []) or [])
    active_key = str(getattr(getattr(context, "scene", None), "bname_active_effect_layer_name", "") or "")
    active = layer_stack_utils._find_gp_layer_by_key(obj.data.layers, active_key) if active_key else None
    if active is None:
        active = getattr(obj.data.layers, "active", None)
    ordered = []
    if active is not None:
        ordered.append(active)
    ordered.extend(layer for layer in reversed(layers) if layer != active)
    for layer in ordered:
        if gpencil.layer_effectively_hidden(layer):
            continue
        bounds = effect_layer_bounds(obj, layer)
        if bounds is None:
            continue
        part = _effect_hit_part(bounds, x_mm, y_mm)
        if part:
            return obj, layer, bounds, part
    return obj, None, None, ""


def _rect_from_points(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    left = min(float(x0), float(x1))
    right = max(float(x0), float(x1))
    bottom = min(float(y0), float(y1))
    top = max(float(y0), float(y1))
    return left, bottom, max(_EFFECT_MIN_SIZE_MM, right - left), max(_EFFECT_MIN_SIZE_MM, top - bottom)


class BNAME_OT_effect_line_generate(Operator):
    bl_idname = "bname.effect_line_generate"
    bl_label = "効果線を生成"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bname_effect_line_params", None) is not None

    def execute(self, context):
        try:
            _obj, layer = _create_effect_layer(context)
            added = len(getattr(_frame_drawing(layer), "strokes", []) or [])
        except Exception as exc:  # noqa: BLE001
            _logger.exception("effect_line_generate failed")
            self.report({"ERROR"}, f"効果線生成失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"効果線生成: {added} ストローク")
        return {"FINISHED"}


class BNAME_OT_effect_line_tool(Operator):
    bl_idname = "bname.effect_line_tool"
    bl_label = "効果線ツール"
    bl_options = {"REGISTER", "UNDO"}

    _externally_finished: bool
    _cursor_modal_set: bool
    _dragging: bool
    _drag_action: str
    _drag_layer_name: str
    _drag_start_x: float
    _drag_start_y: float
    _drag_orig_x: float
    _drag_orig_y: float
    _drag_orig_w: float
    _drag_orig_h: float
    _drag_moved: bool

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded and get_mode(context) != MODE_PANEL)

    def invoke(self, context, _event):
        if panel_modal_state.get_active("effect_line_tool") is not None:
            panel_modal_state.finish_active("effect_line_tool", context, keep_selection=True)
            return {"FINISHED"}
        panel_modal_state.finish_active("knife_cut", context, keep_selection=False)
        panel_modal_state.finish_active("edge_move", context, keep_selection=True)
        panel_modal_state.finish_active("layer_move", context, keep_selection=True)
        panel_modal_state.finish_active("balloon_tool", context, keep_selection=True)
        panel_modal_state.finish_active("text_tool", context, keep_selection=True)
        self._externally_finished = False
        self._cursor_modal_set = panel_modal_state.set_modal_cursor(context, "CROSSHAIR")
        self._clear_drag_state()
        context.window_manager.modal_handler_add(self)
        panel_modal_state.set_active("effect_line_tool", self, context)
        self.report({"INFO"}, "効果線ツール: ドラッグで作成")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            panel_modal_state.clear_active("effect_line_tool", self, context)
            return {"FINISHED", "PASS_THROUGH"}
        if getattr(self, "_dragging", False):
            return self._modal_dragging(context, event)
        if not _event_in_view3d_window(context, event):
            return {"PASS_THROUGH"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if self._should_leave_for_tool_key(event):
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"PASS_THROUGH"}
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return {"PASS_THROUGH"}
        obj, layer, bounds, part = _hit_effect_layer(context, x_mm, y_mm)
        if obj is not None and layer is not None and bounds is not None:
            _select_effect_layer(context, obj, layer)
            self._start_drag(layer, part, x_mm, y_mm, bounds)
            return {"RUNNING_MODAL"}
        obj, layer = _create_effect_layer(
            context,
            (x_mm, y_mm, _EFFECT_MIN_SIZE_MM, _EFFECT_MIN_SIZE_MM),
        )
        self._start_drag(layer, "create", x_mm, y_mm, (x_mm, y_mm, _EFFECT_MIN_SIZE_MM, _EFFECT_MIN_SIZE_MM))
        return {"RUNNING_MODAL"}

    def _should_leave_for_tool_key(self, event) -> bool:
        return (
            event.value == "PRESS"
            and event.type in {"O", "P", "F", "G", "K", "T"}
            and not event.ctrl
            and not event.alt
        )

    def _start_drag(
        self,
        layer,
        action: str,
        x_mm: float,
        y_mm: float,
        bounds: tuple[float, float, float, float],
    ) -> None:
        self._dragging = True
        self._drag_action = "move" if action == "body" else action
        self._drag_layer_name = str(getattr(layer, "name", "") or "")
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_orig_x = float(bounds[0])
        self._drag_orig_y = float(bounds[1])
        self._drag_orig_w = float(bounds[2])
        self._drag_orig_h = float(bounds[3])
        self._drag_moved = False

    def _clear_drag_state(self) -> None:
        self._dragging = False
        self._drag_action = ""
        self._drag_layer_name = ""
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._drag_orig_x = 0.0
        self._drag_orig_y = 0.0
        self._drag_orig_w = 0.0
        self._drag_orig_h = 0.0
        self._drag_moved = False

    def _modal_dragging(self, context, event):
        if event.type == "MOUSEMOVE":
            self._update_drag(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._update_drag(context, event)
            self._finish_drag(context)
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._cancel_drag(context)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def _drag_target(self, context):
        obj = layer_stack_utils.get_effect_gp_object()
        if obj is None:
            return None, None
        layers = getattr(getattr(obj, "data", None), "layers", None)
        if layers is None:
            return obj, None
        for layer in layers:
            if str(getattr(layer, "name", "") or "") == self._drag_layer_name:
                return obj, layer
        return obj, None

    def _update_drag(self, context, event) -> None:
        obj, layer = self._drag_target(context)
        if obj is None or layer is None:
            self._clear_drag_state()
            return
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return
        dx = float(x_mm) - self._drag_start_x
        dy = float(y_mm) - self._drag_start_y
        if abs(dx) > _EFFECT_DRAG_EPS_MM or abs(dy) > _EFFECT_DRAG_EPS_MM:
            self._drag_moved = True
        bounds = self._drag_result_bounds(dx, dy)
        _write_effect_strokes(context, obj, layer, bounds)
        _select_effect_layer(context, obj, layer)

    def _drag_result_bounds(self, dx: float, dy: float) -> tuple[float, float, float, float]:
        action = str(getattr(self, "_drag_action", "") or "")
        x = float(self._drag_orig_x)
        y = float(self._drag_orig_y)
        w = float(self._drag_orig_w)
        h = float(self._drag_orig_h)
        if action == "create":
            return _rect_from_points(self._drag_start_x, self._drag_start_y, self._drag_start_x + dx, self._drag_start_y + dy)
        if action == "move":
            return x + dx, y + dy, w, h
        right = x + w
        top = y + h
        new_left = x
        new_right = right
        new_bottom = y
        new_top = top
        if "left" in action:
            new_left = min(right - _EFFECT_MIN_SIZE_MM, x + dx)
        if "right" in action:
            new_right = max(x + _EFFECT_MIN_SIZE_MM, right + dx)
        if "bottom" in action:
            new_bottom = min(top - _EFFECT_MIN_SIZE_MM, y + dy)
        if "top" in action:
            new_top = max(y + _EFFECT_MIN_SIZE_MM, top + dy)
        return new_left, new_bottom, new_right - new_left, new_top - new_bottom

    def _finish_drag(self, context) -> None:
        obj, layer = self._drag_target(context)
        moved = bool(getattr(self, "_drag_moved", False))
        action = self._drag_action
        if action == "create" and not moved:
            _delete_effect_layer(context, obj, layer)
        elif moved:
            self._push_undo_step("B-Name: 効果線編集")
            layer_stack_utils.sync_layer_stack_after_data_change(context)
        else:
            layer_stack_utils.tag_view3d_redraw(context)
        self._clear_drag_state()

    def _cancel_drag(self, context) -> None:
        obj, layer = self._drag_target(context)
        if obj is not None and layer is not None:
            if self._drag_action == "create":
                _delete_effect_layer(context, obj, layer)
            else:
                bounds = (
                    self._drag_orig_x,
                    self._drag_orig_y,
                    self._drag_orig_w,
                    self._drag_orig_h,
                )
                _write_effect_strokes(context, obj, layer, bounds)
                _select_effect_layer(context, obj, layer)
        self._clear_drag_state()

    def _push_undo_step(self, message: str) -> None:
        try:
            bpy.ops.ed.undo_push(message=message)
        except Exception:  # noqa: BLE001
            _logger.exception("effect_line_tool: undo_push failed")

    def _cleanup(self, context) -> None:
        if getattr(self, "_cursor_modal_set", False):
            panel_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        self._clear_drag_state()

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        _ = keep_selection
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        self._cleanup(context)
        panel_modal_state.clear_active("effect_line_tool", self, context)


_CLASSES = (BNAME_OT_effect_line_generate, BNAME_OT_effect_line_tool)


def register() -> None:
    from ..core.effect_line import BNameEffectLineParams

    bpy.types.Scene.bname_effect_line_params = bpy.props.PointerProperty(
        type=BNameEffectLineParams
    )
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    try:
        del bpy.types.Scene.bname_effect_line_params
    except AttributeError:
        pass
