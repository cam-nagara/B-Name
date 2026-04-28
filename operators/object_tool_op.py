"""B-Name object tool for viewport selection, moving and box resizing."""

from __future__ import annotations

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..core.work import get_work
from ..utils import detail_popup, edge_selection, layer_stack as layer_stack_utils, object_selection
from . import (
    balloon_op,
    effect_line_op,
    layer_move_session,
    coma_edge_drag_session,
    layer_move_op,
    coma_edge_move_op,
    coma_modal_state,
    coma_picker,
    selection_context_menu,
    text_op,
    view_event_region,
)

_DRAG_EPS_MM = 0.05


def _find_page_by_id(work, page_id: str):
    if work is None:
        return -1, None
    for i, page in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(page, "id", "") or "") == str(page_id or ""):
            return i, page
    return -1, None


def _coma_identity(panel) -> str:
    return str(getattr(panel, "coma_id", "") or getattr(panel, "id", "") or "")


def _find_coma_by_key(work, page_id: str, coma_id: str):
    page_index, page = _find_page_by_id(work, page_id)
    if page is None:
        return -1, None, -1, None
    for i, panel in enumerate(getattr(page, "comas", []) or []):
        if _coma_identity(panel) == str(coma_id or ""):
            return page_index, page, i, panel
    return page_index, page, -1, None


def _find_balloon_by_key(work, page_id: str, item_id: str):
    page_index, page = _find_page_by_id(work, page_id)
    if page is None:
        return -1, None, -1, None
    for i, entry in enumerate(getattr(page, "balloons", []) or []):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return page_index, page, i, entry
    return page_index, page, -1, None


def _find_text_by_key(work, page_id: str, item_id: str):
    page_index, page = _find_page_by_id(work, page_id)
    if page is None:
        return -1, None, -1, None
    for i, entry in enumerate(getattr(page, "texts", []) or []):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return page_index, page, i, entry
    return page_index, page, -1, None


def _find_effect_layer(name: str):
    obj = layer_stack_utils.get_effect_gp_object()
    layers = getattr(getattr(obj, "data", None), "layers", None) if obj is not None else None
    if layers is None:
        return obj, None
    for layer in layers:
        if str(getattr(layer, "name", "") or "") == str(name or ""):
            return obj, layer
    return obj, None


def _event_world_xy_mm(context, event) -> tuple[float | None, float | None]:
    return effect_line_op._event_world_xy_mm(context, event)


def _selection_mode(event) -> str:
    if bool(getattr(event, "ctrl", False)):
        return "toggle"
    if bool(getattr(event, "shift", False)):
        return "add"
    return "single"


def _rect_resize_result(
    action: str,
    x: float,
    y: float,
    w: float,
    h: float,
    dx: float,
    dy: float,
    min_size: float,
) -> tuple[float, float, float, float]:
    if action == "move":
        return x + dx, y + dy, w, h
    right = x + w
    top = y + h
    new_left = x
    new_right = right
    new_bottom = y
    new_top = top
    if "left" in action:
        new_left = min(right - min_size, x + dx)
    if "right" in action:
        new_right = max(x + min_size, right + dx)
    if "bottom" in action:
        new_bottom = min(top - min_size, y + dy)
    if "top" in action:
        new_top = max(y + min_size, top + dy)
    return new_left, new_bottom, new_right - new_left, new_top - new_bottom


class BNAME_OT_object_tool(Operator):
    bl_idname = "bname.object_tool"
    bl_label = "オブジェクトツール"
    bl_options = {"REGISTER", "UNDO"}

    _externally_finished: bool
    _cursor_modal_set: bool
    _dragging: bool
    _drag_action: str
    _drag_start_x: float
    _drag_start_y: float
    _drag_keys: list[str]
    _snapshots: list[dict]
    _drag_moved: bool
    _edge_drag: object | None
    _layer_drag: object | None

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work is not None and getattr(work, "loaded", False))

    def invoke(self, context, _event):
        active = coma_modal_state.get_active("object_tool")
        if active is not None:
            return {"FINISHED"}
        coma_modal_state.finish_all(context, except_tool="object_tool")
        self._externally_finished = False
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "DEFAULT")
        self._clear_drag_state()
        context.window_manager.modal_handler_add(self)
        coma_modal_state.set_active("object_tool", self, context)
        self.report({"INFO"}, "オブジェクトツール: クリックで選択、ドラッグで移動/リサイズ")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            coma_modal_state.clear_active("object_tool", self, context)
            return {"FINISHED", "PASS_THROUGH"}
        if getattr(self, "_dragging", False):
            return self._modal_dragging(context, event)
        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            if selection_context_menu.open_for_object_tool(self, context, event):
                return {"RUNNING_MODAL"}
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if event.type == "ESC" and event.value == "PRESS":
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if event.value == "PRESS" and event.type in {"P", "F", "G", "K", "T"} and not event.ctrl and not event.alt:
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}
        if event.value == "PRESS" and event.type in {"Z", "Y"} and event.ctrl:
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}
        if not view_event_region.is_view3d_window_event(context, event):
            return {"PASS_THROUGH"}
        if event.type != "LEFTMOUSE" or event.value not in {"PRESS", "DOUBLE_CLICK"}:
            return {"PASS_THROUGH"}
        return self._handle_left_press(context, event)

    def _handle_left_press(self, context, event):
        mode = _selection_mode(event)
        if mode == "single" and coma_edge_move_op.extend_selected_handle_at_event(context, event):
            return {"RUNNING_MODAL"}
        hit = self._hit_object(context, event)
        if hit is None:
            if mode == "single" and self._try_start_layer_drag(context, event):
                return {"RUNNING_MODAL"}
            if mode == "single":
                object_selection.clear(context)
                edge_selection.clear_selection(context)
            return {"RUNNING_MODAL"}
        self._activate_hit(context, hit, mode=mode)
        if mode in {"toggle", "add"}:
            return {"RUNNING_MODAL"}
        x_mm, y_mm = self._start_point_for_hit(context, event, hit)
        if x_mm is None or y_mm is None:
            return {"RUNNING_MODAL"}
        if hit["kind"] in {"coma_edge", "coma_vertex"}:
            self._start_coma_edge_drag(context, hit, event, x_mm, y_mm)
        else:
            self._start_object_drag(context, hit, x_mm, y_mm)
        return {"RUNNING_MODAL"}

    def _hit_object(self, context, event) -> dict | None:
        work = get_work(context)
        if work is None:
            return None
        view = view_event_region.view3d_window_under_event(context, event)
        if view is None:
            return None
        area, region, rv3d, mx, my = view
        edge_hit = coma_edge_move_op._pick_edge_or_vertex(work, region, rv3d, int(mx), int(my))
        if edge_hit is not None:
            page = work.pages[int(edge_hit["page"])]
            panel = page.comas[int(edge_hit["coma"])]
            kind = "coma_vertex" if edge_hit.get("type") == "vertex" else "coma_edge"
            hit = dict(edge_hit)
            hit.update({
                "kind": kind,
                "key": object_selection.coma_key(page, panel),
                "area": area,
                "region": region,
                "rv3d": rv3d,
            })
            return hit
        text_hit = self._hit_text(context, event)
        if text_hit is not None:
            return text_hit
        balloon_hit = self._hit_balloon(context, event)
        if balloon_hit is not None:
            return balloon_hit
        effect_hit = self._hit_effect(context, event)
        if effect_hit is not None:
            return effect_hit
        panel_hit = coma_picker.find_coma_at_event(context, event)
        if panel_hit is not None:
            page_index, coma_index = panel_hit
            page = work.pages[page_index]
            panel = page.comas[coma_index]
            return {
                "kind": "coma",
                "page": page_index,
                "coma": coma_index,
                "part": "body",
                "key": object_selection.coma_key(page, panel),
            }
        return None

    def _hit_text(self, context, event) -> dict | None:
        work, page, lx, ly, hit_index, hit_entry, hit_part, _can_create = text_op._resolve_text_hit_from_event(
            context, event
        )
        if work is None or page is None or hit_entry is None or hit_index < 0 or lx is None or ly is None:
            return None
        return {
            "kind": "text",
            "page_id": getattr(page, "id", ""),
            "index": hit_index,
            "part": "move" if hit_part == "body" else hit_part,
            "key": object_selection.text_key(page, hit_entry),
            "local": (float(lx), float(ly)),
        }

    def _hit_balloon(self, context, event) -> dict | None:
        work, page, lx, ly = balloon_op._resolve_page_from_event(context, event)
        if work is None or page is None or lx is None or ly is None:
            return None
        hit_index, hit_entry, hit_part = balloon_op._hit_balloon_entry(page, lx, ly)
        if hit_entry is None or hit_index < 0:
            return None
        return {
            "kind": "balloon",
            "page_id": getattr(page, "id", ""),
            "index": hit_index,
            "part": "move" if hit_part == "body" else hit_part,
            "key": object_selection.balloon_key(page, hit_entry),
            "local": (float(lx), float(ly)),
        }

    def _hit_effect(self, context, event) -> dict | None:
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return None
        obj, layer, bounds, part = effect_line_op._hit_effect_layer(context, x_mm, y_mm)
        if obj is None or layer is None or bounds is None:
            return None
        return {
            "kind": "effect",
            "layer_name": str(getattr(layer, "name", "") or ""),
            "part": "move" if part == "body" else part,
            "key": object_selection.effect_key(layer),
            "world": (float(x_mm), float(y_mm)),
        }

    def _activate_hit(self, context, hit: dict, *, mode: str) -> None:
        work = get_work(context)
        if work is None:
            return
        kind = hit["kind"]
        key = str(hit.get("key", "") or "")
        if kind in {"coma", "coma_edge", "coma_vertex"}:
            page_index = int(hit["page"])
            coma_index = int(hit["coma"])
            page = work.pages[page_index]
            work.active_page_index = page_index
            page.active_coma_index = coma_index
            if hasattr(context.scene, "bname_active_layer_kind"):
                context.scene.bname_active_layer_kind = "coma"
            if kind == "coma_edge":
                edge_selection.set_selection(
                    context,
                    "edge",
                    page_index=page_index,
                    coma_index=coma_index,
                    edge_index=int(hit.get("edge", -1)),
                )
            elif kind == "coma_vertex":
                edge_selection.set_selection(
                    context,
                    "vertex",
                    page_index=page_index,
                    coma_index=coma_index,
                    vertex_index=int(hit.get("vertex", -1)),
                )
            else:
                edge_selection.set_selection(
                    context,
                    "border",
                    page_index=page_index,
                    coma_index=coma_index,
                )
        elif kind == "balloon":
            page_index, page = _find_page_by_id(work, hit.get("page_id", ""))
            if page is not None:
                balloon_op._select_balloon_index(
                    context,
                    work,
                    page,
                    int(hit.get("index", -1)),
                    mode=mode,
                )
                work.active_page_index = page_index
            edge_selection.clear_selection(context)
        elif kind == "text":
            _page_index, page = _find_page_by_id(work, hit.get("page_id", ""))
            if page is not None:
                text_op._select_text_index(context, work, page, int(hit.get("index", -1)))
            edge_selection.clear_selection(context)
        elif kind == "effect":
            obj, layer = _find_effect_layer(hit.get("layer_name", ""))
            if layer is not None:
                effect_line_op._select_effect_layer(context, obj, layer)
            edge_selection.clear_selection(context)
        if kind != "balloon":
            object_selection.select_key(context, key, mode=mode)

    def _start_point_for_hit(self, context, event, hit: dict) -> tuple[float | None, float | None]:
        if "world" in hit:
            return hit["world"]
        if hit["kind"] in {"coma", "coma_edge", "coma_vertex"}:
            view = view_event_region.view3d_window_under_event(context, event)
            if view is None:
                return None, None
            _area, region, rv3d, mx, my = view
            return coma_edge_move_op._region_to_world_mm(region, rv3d, mx, my)
        return _event_world_xy_mm(context, event)

    def _start_coma_edge_drag(self, context, hit: dict, event, x_mm: float, y_mm: float) -> None:
        selection = {
            "type": "vertex" if hit["kind"] == "coma_vertex" else "edge",
            "page": int(hit["page"]),
            "coma": int(hit["coma"]),
        }
        if selection["type"] == "vertex":
            selection["vertex"] = int(hit.get("vertex", -1))
        else:
            selection["edge"] = int(hit.get("edge", -1))
        view = view_event_region.view3d_window_under_event(context, event)
        if view is None:
            return
        area, region, rv3d, _mx, _my = view
        self._edge_drag = coma_edge_drag_session.ComaEdgeDragSession(
            context,
            get_work(context),
            area,
            region,
            rv3d,
            selection,
            (float(x_mm), float(y_mm)),
        )
        self._dragging = True
        self._drag_action = "coma_edge"
        self._drag_moved = False

    def _try_start_layer_drag(self, context, event) -> bool:
        scene = getattr(context, "scene", None)
        if scene is None or getattr(scene, "bname_active_layer_kind", "") not in {"gp", "image"}:
            return False
        item = layer_stack_utils.active_stack_item(context)
        if item is None or getattr(item, "kind", "") not in {"gp", "image"}:
            return False
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return False
        session = layer_move_session.LayerMoveDragSession(context, (float(x_mm), float(y_mm)))
        if not session.started:
            return False
        self._layer_drag = session
        self._dragging = True
        self._drag_action = "layer_move"
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_keys = []
        self._snapshots = []
        self._drag_moved = False
        return True

    def _start_object_drag(self, context, hit: dict, x_mm: float, y_mm: float) -> None:
        action = str(hit.get("part", "move") or "move")
        key = str(hit.get("key", "") or "")
        selected = object_selection.get_keys(context)
        if action == "move" and key in selected:
            keys = selected
        else:
            keys = [key]
        self._dragging = True
        self._drag_action = action
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_keys = keys
        self._snapshots = self._make_snapshots(context, keys, primary_key=key, action=action)
        self._drag_moved = False

    def _make_snapshots(self, context, keys: list[str], *, primary_key: str, action: str) -> list[dict]:
        work = get_work(context)
        snapshots: list[dict] = []
        for key in keys:
            kind, page_id, item_id = object_selection.parse_key(key)
            if action != "move" and key != primary_key:
                continue
            if kind == "coma":
                page_index, page, coma_index, panel = _find_coma_by_key(work, page_id, item_id)
                if panel is None:
                    continue
                poly = coma_edge_move_op._coma_polygon(panel)
                gp_key = layer_stack_utils.gp_parent_key_for_coma(page, panel)
                snapshots.append({
                    "kind": "coma",
                    "page_index": page_index,
                    "page_id": page_id,
                    "coma_id": item_id,
                    "shape": getattr(panel, "shape_type", ""),
                    "rect": (
                        float(getattr(panel, "rect_x_mm", 0.0)),
                        float(getattr(panel, "rect_y_mm", 0.0)),
                        float(getattr(panel, "rect_width_mm", 0.0)),
                        float(getattr(panel, "rect_height_mm", 0.0)),
                    ),
                    "poly": poly,
                    "children": self._panel_child_snapshots(page, panel),
                    "gp": layer_stack_utils.capture_gp_layers_for_parent_keys(context, {gp_key}),
                    "gp_key": gp_key,
                })
            elif kind == "balloon":
                _page_index, page, _idx, entry = _find_balloon_by_key(work, page_id, item_id)
                if entry is None:
                    continue
                snapshots.append({
                    "kind": "balloon",
                    "page_id": page_id,
                    "item_id": item_id,
                    "rect": (float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm)),
                })
            elif kind == "text":
                _page_index, _page, _idx, entry = _find_text_by_key(work, page_id, item_id)
                if entry is None:
                    continue
                snapshots.append({
                    "kind": "text",
                    "page_id": page_id,
                    "item_id": item_id,
                    "rect": (float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm)),
                })
            elif kind == "effect":
                obj, layer = _find_effect_layer(item_id)
                bounds = effect_line_op.effect_layer_bounds(obj, layer)
                if layer is None or bounds is None:
                    continue
                snapshots.append({
                    "kind": "effect",
                    "item_id": item_id,
                    "rect": (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3])),
                })
        return snapshots

    def _panel_child_snapshots(self, page, panel) -> list[tuple[str, str, float, float]]:
        balloons, texts = layer_move_op._panel_children(page, panel)
        snapshots = []
        for balloon in balloons:
            snapshots.append(("balloon", getattr(balloon, "id", ""), float(balloon.x_mm), float(balloon.y_mm)))
        for text in texts:
            snapshots.append(("text", getattr(text, "id", ""), float(text.x_mm), float(text.y_mm)))
        return snapshots

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

    def _update_drag(self, context, event) -> None:
        if self._drag_action == "coma_edge":
            if self._edge_drag is not None and self._edge_drag.apply(event):
                self._drag_moved = True
            return
        if self._drag_action == "layer_move":
            if self._layer_drag is not None and self._layer_drag.apply(context, event):
                self._drag_moved = True
            return
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return
        dx = float(x_mm) - self._drag_start_x
        dy = float(y_mm) - self._drag_start_y
        if abs(dx) > _DRAG_EPS_MM or abs(dy) > _DRAG_EPS_MM:
            self._drag_moved = True
        self._apply_snapshots(context, dx, dy)
        layer_stack_utils.tag_view3d_redraw(context)

    def _apply_snapshots(self, context, dx: float, dy: float) -> None:
        work = get_work(context)
        for snapshot in self._snapshots:
            kind = snapshot["kind"]
            x, y, w, h = snapshot.get("rect", (0.0, 0.0, 0.0, 0.0))
            if kind == "coma":
                _page_index, page, _coma_index, panel = _find_coma_by_key(
                    work,
                    snapshot["page_id"],
                    snapshot["coma_id"],
                )
                if panel is None or page is None:
                    continue
                if self._drag_action == "move":
                    self._apply_panel_move(context, page, panel, snapshot, dx, dy)
                elif getattr(panel, "shape_type", "") == "rect":
                    nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                    panel.rect_x_mm = nx
                    panel.rect_y_mm = ny
                    panel.rect_width_mm = nw
                    panel.rect_height_mm = nh
            elif kind == "balloon":
                _page_index, page, _idx, entry = _find_balloon_by_key(
                    work,
                    snapshot["page_id"],
                    snapshot["item_id"],
                )
                if entry is None or page is None:
                    continue
                nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                balloon_op._set_balloon_rect(page, entry, nx, ny, nw, nh)
            elif kind == "text":
                _page_index, _page, _idx, entry = _find_text_by_key(
                    work,
                    snapshot["page_id"],
                    snapshot["item_id"],
                )
                if entry is None:
                    continue
                nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                text_op._set_text_rect(entry, nx, ny, nw, nh)
            elif kind == "effect":
                obj, layer = _find_effect_layer(snapshot["item_id"])
                if layer is None:
                    continue
                nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                effect_line_op._write_effect_strokes(context, obj, layer, (nx, ny, nw, nh))

    def _apply_panel_move(self, context, page, panel, snapshot: dict, dx: float, dy: float) -> None:
        if snapshot["shape"] == "rect":
            x, y, w, h = snapshot["rect"]
            panel.shape_type = "rect"
            panel.rect_x_mm = x + dx
            panel.rect_y_mm = y + dy
            panel.rect_width_mm = w
            panel.rect_height_mm = h
        else:
            coma_edge_move_op._set_coma_polygon(
                panel,
                [(x + dx, y + dy) for x, y in snapshot["poly"]],
            )
        for child_kind, child_id, x, y in snapshot.get("children", []):
            if child_kind == "balloon":
                idx = balloon_op._find_balloon_index(page, child_id)
                if 0 <= idx < len(page.balloons):
                    balloon_op._move_balloon_with_texts(page, page.balloons[idx], x + dx, y + dy)
            elif child_kind == "text":
                idx = text_op._find_text_index(page, child_id)
                if 0 <= idx < len(page.texts):
                    page.texts[idx].x_mm = x + dx
                    page.texts[idx].y_mm = y + dy
        layer_stack_utils.restore_gp_layer_snapshots(snapshot.get("gp", []))
        layer_stack_utils.translate_gp_layers_for_parent_keys(context, {snapshot["gp_key"]}, dx, dy)

    def _finish_drag(self, context) -> None:
        moved = bool(getattr(self, "_drag_moved", False))
        changed = moved
        edge_session = self._drag_action == "coma_edge"
        layer_session = self._drag_action == "layer_move"
        if self._drag_action == "coma_edge" and self._edge_drag is not None:
            changed = bool(self._edge_drag.finish())
        elif self._drag_action == "layer_move" and self._layer_drag is not None:
            changed = bool(self._layer_drag.finish(context))
        if changed:
            if not edge_session and not layer_session:
                try:
                    bpy.ops.ed.undo_push(message="B-Name: オブジェクト編集")
                except Exception:  # noqa: BLE001
                    pass
            if not layer_session:
                layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        elif edge_session and not moved:
            detail_popup.open_active_detail_deferred(context)
        elif not layer_session and self._drag_action != "coma_edge":
            detail_popup.open_active_detail_deferred(context)
        self._clear_drag_state()

    def _cancel_drag(self, context) -> None:
        if self._drag_action == "layer_move" and self._layer_drag is not None:
            self._layer_drag.cancel(context)
        elif self._drag_action == "coma_edge" and self._edge_drag is not None:
            self._edge_drag.cancel()
        elif self._drag_action != "coma_edge":
            self._apply_snapshots(context, 0.0, 0.0)
        self._clear_drag_state()
        layer_stack_utils.tag_view3d_redraw(context)

    def _clear_drag_state(self) -> None:
        self._dragging = False
        self._drag_action = ""
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._drag_keys = []
        self._snapshots = []
        self._drag_moved = False
        self._edge_drag = None
        self._layer_drag = None

    def _cleanup(self, context) -> None:
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        if getattr(self, "_drag_action", "") == "coma_edge" and self._edge_drag is not None:
            self._edge_drag.cancel()
        elif getattr(self, "_drag_action", "") == "layer_move" and self._layer_drag is not None:
            self._layer_drag.cancel(context)
        self._clear_drag_state()

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        if not keep_selection:
            object_selection.clear(context)
        self._cleanup(context)
        coma_modal_state.clear_active("object_tool", self, context)


_CLASSES = (BNAME_OT_object_tool,)


def register() -> None:
    bpy.types.WindowManager.bname_object_selection_keys = StringProperty(default="")
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    try:
        del bpy.types.WindowManager.bname_object_selection_keys
    except AttributeError:
        pass
