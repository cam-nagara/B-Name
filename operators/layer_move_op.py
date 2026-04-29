"""ビューポート上で統合レイヤー行をドラッグ移動するツール."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..core.mode import MODE_COMA, MODE_PAGE, get_mode
from ..core.work import get_active_page, get_work
from ..utils import geom, gp_layer_parenting as gp_parent, layer_stack as layer_stack_utils, page_grid
from ..utils.layer_hierarchy import coma_stack_key, page_stack_key
from . import coma_modal_state, coma_picker, view_event_region


def _move_panel(panel, dx_mm: float, dy_mm: float) -> None:
    if getattr(panel, "shape_type", "") == "rect":
        panel.rect_x_mm += dx_mm
        panel.rect_y_mm += dy_mm
        return
    for vertex in getattr(panel, "vertices", []):
        vertex.x_mm += dx_mm
        vertex.y_mm += dy_mm


def _move_balloon(page, balloon, dx_mm: float, dy_mm: float) -> None:
    balloon.x_mm += dx_mm
    balloon.y_mm += dy_mm
    bid = str(getattr(balloon, "id", "") or "")
    if not bid:
        return
    for text in getattr(page, "texts", []):
        if getattr(text, "parent_balloon_id", "") == bid:
            text.x_mm += dx_mm
            text.y_mm += dy_mm


def _entry_center(entry) -> tuple[float, float]:
    return (
        float(getattr(entry, "x_mm", 0.0)) + float(getattr(entry, "width_mm", 0.0)) * 0.5,
        float(getattr(entry, "y_mm", 0.0)) + float(getattr(entry, "height_mm", 0.0)) * 0.5,
    )


def _entry_parent_matches_panel(page, panel, entry) -> bool | None:
    parent_key = str(getattr(entry, "parent_key", "") or "")
    if not parent_key:
        return None
    page_key = page_stack_key(page)
    parent_kind = str(getattr(entry, "parent_kind", "") or "")
    if parent_kind == "page" or parent_key in {str(getattr(page, "id", "") or ""), page_key}:
        return False
    target_stem = str(getattr(panel, "coma_id", "") or "")
    target_matches = {
        coma_stack_key(page, panel),
        str(getattr(panel, "id", "") or ""),
        target_stem,
        f"{getattr(page, 'id', '')}:{target_stem}",
    }
    if parent_key in target_matches:
        return True
    if parent_kind == "coma" or ":" in parent_key:
        for candidate in getattr(page, "comas", []):
            stem = str(getattr(candidate, "coma_id", "") or "")
            if parent_key in {
                coma_stack_key(page, candidate),
                str(getattr(candidate, "id", "") or ""),
                stem,
                f"{getattr(page, 'id', '')}:{stem}",
            }:
                return False
    return None


def _balloon_parent_matches_panel(page, panel, balloon) -> bool | None:
    return _entry_parent_matches_panel(page, panel, balloon)


def _panel_children(page, panel):
    balloons = []
    texts = []
    target_stem = str(getattr(panel, "coma_id", "") or "")
    for balloon in getattr(page, "balloons", []):
        parent_match = _balloon_parent_matches_panel(page, panel, balloon)
        if parent_match is True:
            balloons.append(balloon)
            continue
        if parent_match is False:
            continue
        hit = layer_stack_utils.coma_containing_point(page, *_entry_center(balloon))
        if hit is not None and str(getattr(hit, "coma_id", "") or "") == target_stem:
            balloons.append(balloon)
    attached_texts = {
        getattr(text, "id", "")
        for balloon in balloons
        for text in getattr(page, "texts", [])
        if getattr(text, "parent_balloon_id", "") == getattr(balloon, "id", "")
    }
    for text in getattr(page, "texts", []):
        if getattr(text, "id", "") in attached_texts:
            continue
        parent_match = _entry_parent_matches_panel(page, panel, text)
        if parent_match is True:
            texts.append(text)
            continue
        if parent_match is False:
            continue
        hit = layer_stack_utils.coma_containing_point(page, *_entry_center(text))
        if hit is not None and str(getattr(hit, "coma_id", "") or "") == target_stem:
            texts.append(text)
    return balloons, texts


def _snapshot_panel(panel):
    return {
        "shape": getattr(panel, "shape_type", ""),
        "rect": (
            float(getattr(panel, "rect_x_mm", 0.0)),
            float(getattr(panel, "rect_y_mm", 0.0)),
        ),
        "verts": [(float(v.x_mm), float(v.y_mm)) for v in getattr(panel, "vertices", [])],
    }


def _restore_panel(panel, snapshot) -> None:
    if snapshot["shape"] == "rect":
        panel.rect_x_mm, panel.rect_y_mm = snapshot["rect"]
        return
    for vertex, (x_mm, y_mm) in zip(getattr(panel, "vertices", []), snapshot["verts"], strict=False):
        vertex.x_mm = x_mm
        vertex.y_mm = y_mm


def _point_inside_active_panel(context, x_mm: float, y_mm: float) -> bool:
    work = get_work(context)
    page = get_active_page(context)
    if work is None or page is None:
        return True
    idx = int(getattr(page, "active_coma_index", -1))
    if not (0 <= idx < len(page.comas)):
        return True
    hit = layer_stack_utils.coma_containing_point(page, x_mm, y_mm)
    return (
        hit is not None
        and str(getattr(hit, "coma_id", "") or "")
        == str(getattr(page.comas[idx], "coma_id", "") or "")
    )


def _move_would_violate_layer_scope(context, page, entry, dx_mm: float, dy_mm: float) -> bool:
    kind = get_mode(context)
    cx, cy = _entry_center(entry)
    nx, ny = cx + dx_mm, cy + dy_mm
    if kind == MODE_PAGE:
        return layer_stack_utils.coma_containing_point(page, nx, ny) is not None
    if kind == MODE_COMA:
        return not _point_inside_active_panel(context, nx, ny)
    return False


class BNAME_OT_layer_move_tool(Operator):
    bl_idname = "bname.layer_move_tool"
    bl_label = "レイヤー移動ツール"
    bl_options = {"REGISTER", "UNDO"}

    _last_world: tuple[float, float] | None
    _target: dict | None
    _snapshots: list[tuple[str, object, object]]
    _dragging: bool
    _moved: bool
    _externally_finished: bool
    _cursor_modal_set: bool

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded and getattr(context.scene, "bname_layer_stack", None) is not None)

    def invoke(self, context, event):
        active = coma_modal_state.get_active("layer_move")
        if active is not None:
            active.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        coma_modal_state.finish_active("coma_vertex_edit", context, keep_selection=True)
        coma_modal_state.finish_active("knife_cut", context, keep_selection=False)
        coma_modal_state.finish_active("edge_move", context, keep_selection=True)
        coma_modal_state.finish_active("balloon_tool", context, keep_selection=True)
        coma_modal_state.finish_active("text_tool", context, keep_selection=True)
        coma_modal_state.finish_active("effect_line_tool", context, keep_selection=True)
        self._last_world = None
        self._target = None
        self._snapshots = []
        self._dragging = False
        self._moved = False
        self._externally_finished = False
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "SCROLL_XY")
        context.window_manager.modal_handler_add(self)
        coma_modal_state.set_active("layer_move", self, context)
        self.report({"INFO"}, "レイヤー移動ツール: ビューポート上でドラッグ")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            coma_modal_state.clear_active("layer_move", self, context)
            return {"FINISHED", "PASS_THROUGH"}
        if (
            not bool(getattr(self, "_dragging", False))
            and view_event_region.modal_navigation_ui_passthrough(self, context, event)
        ):
            return {"PASS_THROUGH"}
        if (
            event.value == "PRESS"
            and event.type == "F"
            and not event.ctrl
            and not event.alt
            and not event.shift
        ):
            self.finish_from_external(context, keep_selection=True)
            try:
                bpy.ops.bname.coma_knife_cut("INVOKE_DEFAULT")
            except Exception:  # noqa: BLE001
                pass
            return {"FINISHED"}
        if (
            event.value == "PRESS"
            and event.type == "G"
            and not event.ctrl
            and not event.alt
            and not event.shift
        ):
            self.finish_from_external(context, keep_selection=True)
            try:
                bpy.ops.bname.coma_edge_move("INVOKE_DEFAULT")
            except Exception:  # noqa: BLE001
                pass
            return {"FINISHED"}
        if (
            event.value == "PRESS"
            and event.type == "T"
            and not event.ctrl
            and not event.alt
            and not event.shift
        ):
            self.finish_from_external(context, keep_selection=True)
            try:
                bpy.ops.bname.text_tool("INVOKE_DEFAULT")
            except Exception:  # noqa: BLE001
                pass
            return {"FINISHED"}
        if (
            event.value == "PRESS"
            and event.type == "K"
            and not event.ctrl
            and not event.alt
            and not event.shift
        ):
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if (
            event.value == "PRESS"
            and event.type in {"O", "P", "COMMA", "PERIOD", "Z", "X"}
            and not event.ctrl
            and not event.alt
        ):
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self._restore_snapshots(context)
            layer_stack_utils.tag_view3d_redraw(context)
            self._cleanup(context)
            coma_modal_state.clear_active("layer_move", self, context)
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if not view_event_region.is_view3d_window_event(context, event):
                return {"PASS_THROUGH"}
            coords = coma_picker._event_world_mm(context, event)
            if coords is None:
                return {"PASS_THROUGH"}
            if not self._begin_drag(context, coords):
                return {"RUNNING_MODAL"}
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            if not self._dragging and not view_event_region.is_view3d_window_event(context, event):
                return {"PASS_THROUGH"}
            if self._dragging:
                if self._moved:
                    self._push_undo_step()
                    layer_stack_utils.sync_layer_stack(context)
                self._target = None
                self._snapshots = []
                self._last_world = None
                self._dragging = False
                self._moved = False
                return {"RUNNING_MODAL"}
            return {"RUNNING_MODAL"}
        if event.type != "MOUSEMOVE":
            return {"PASS_THROUGH"}
        if not view_event_region.is_view3d_window_event(context, event):
            return {"RUNNING_MODAL"} if self._dragging else {"PASS_THROUGH"}
        coords = coma_picker._event_world_mm(context, event)
        if coords is None or self._last_world is None or not self._dragging:
            return {"PASS_THROUGH"}
        dx = coords[0] - self._last_world[0]
        dy = coords[1] - self._last_world[1]
        if dx == 0.0 and dy == 0.0:
            return {"RUNNING_MODAL"}
        if self._apply_delta(context, dx, dy):
            self._last_world = coords
            self._moved = True
            layer_stack_utils.apply_stack_order(context)
            page_grid.apply_page_collection_transforms(context, get_work(context))
            layer_stack_utils.tag_view3d_redraw(context)
        return {"RUNNING_MODAL"}

    def _begin_drag(self, context, coords: tuple[float, float]) -> bool:
        item = layer_stack_utils.active_stack_item(context)
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        if item is None or resolved is None or resolved.get("target") is None:
            self.report({"WARNING"}, "移動するレイヤーを選択してください")
            return False
        self._target = resolved
        self._snapshots = []
        self._capture_snapshot(context, item.kind, resolved)
        self._last_world = coords
        self._dragging = True
        self._moved = False
        return True

    def _push_undo_step(self) -> None:
        try:
            bpy.ops.ed.undo_push(message="B-Name: レイヤー移動")
        except Exception:  # noqa: BLE001
            pass

    def _cleanup(self, context) -> None:
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        _ = keep_selection
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        self._cleanup(context)
        coma_modal_state.clear_active("layer_move", self, context)

    def _capture_snapshot(self, context, kind: str, resolved: dict) -> None:
        target = resolved.get("target")
        page = resolved.get("page")
        if kind == "page":
            self._snapshots.append(("page", target, (target.offset_x_mm, target.offset_y_mm)))
            self._snapshots.append(
                ("gp_layers", None, layer_stack_utils.capture_gp_layers_for_parent_keys(
                    context, layer_stack_utils.gp_parent_keys_for_page(target)
                ))
            )
            self._snapshots.append(
                ("effect_layers", None, layer_stack_utils.capture_effect_layers_for_parent_keys(
                    context, layer_stack_utils.gp_parent_keys_for_page(target)
                ))
            )
        elif kind == "coma":
            self._snapshots.append(("coma", target, _snapshot_panel(target)))
            if page is not None:
                self._snapshots.append(
                    ("gp_layers", None, layer_stack_utils.capture_gp_layers_for_parent_keys(
                        context, {layer_stack_utils.gp_parent_key_for_coma(page, target)}
                    ))
                )
                self._snapshots.append(
                    ("effect_layers", None, layer_stack_utils.capture_effect_layers_for_parent_keys(
                        context, {layer_stack_utils.gp_parent_key_for_coma(page, target)}
                    ))
                )
                self._snapshots.append(
                    ("raster_layers", None, layer_stack_utils.capture_raster_layers_for_parent_keys(
                        context, {layer_stack_utils.gp_parent_key_for_coma(page, target)}
                    ))
                )
                balloons, texts = _panel_children(page, target)
                attached_text_ids: set[str] = set()
                for balloon in balloons:
                    self._snapshots.append(("balloon", balloon, (balloon.x_mm, balloon.y_mm)))
                    bid = str(getattr(balloon, "id", "") or "")
                    for text in getattr(page, "texts", []):
                        if getattr(text, "parent_balloon_id", "") == bid:
                            attached_text_ids.add(str(getattr(text, "id", "") or ""))
                            self._snapshots.append(("attached_text", text, (text.x_mm, text.y_mm)))
                for text in texts:
                    if str(getattr(text, "id", "") or "") in attached_text_ids:
                        continue
                    self._snapshots.append(("text", text, (text.x_mm, text.y_mm)))
        elif kind in {"balloon", "text", "image"}:
            self._snapshots.append((kind, target, (target.x_mm, target.y_mm)))
            if kind == "balloon" and page is not None:
                bid = str(getattr(target, "id", "") or "")
                for text in getattr(page, "texts", []):
                    if getattr(text, "parent_balloon_id", "") == bid:
                        self._snapshots.append(("attached_text", text, (text.x_mm, text.y_mm)))
        elif kind == "gp":
            self._snapshots.append(("gp_layers", None, gp_parent.capture_layers([target])))
        elif kind == "effect":
            self._snapshots.append(("effect_layers", None, gp_parent.capture_layers([target])))

    def _restore_snapshots(self, context) -> None:
        for kind, target, data in self._snapshots:
            if kind == "page":
                target.offset_x_mm, target.offset_y_mm = data
            elif kind == "coma":
                _restore_panel(target, data)
            elif kind in {"balloon", "text", "attached_text", "image"}:
                target.x_mm, target.y_mm = data
            elif kind == "gp_layers":
                layer_stack_utils.restore_gp_layer_snapshots(data)
            elif kind == "effect_layers":
                layer_stack_utils.restore_gp_layer_snapshots(data)
            elif kind == "raster_layers":
                layer_stack_utils.restore_raster_layer_snapshots(context, data)
        page_grid.apply_page_collection_transforms(context, get_work(context))

    def _apply_delta(self, context, dx_mm: float, dy_mm: float) -> bool:
        if self._target is None:
            return False
        kind = self._target.get("kind")
        target = self._target.get("target")
        page = self._target.get("page") or get_active_page(context)
        if kind == "page":
            target.offset_x_mm += dx_mm
            target.offset_y_mm += dy_mm
            layer_stack_utils.translate_gp_layers_for_parent_keys(
                context, layer_stack_utils.gp_parent_keys_for_page(target), dx_mm, dy_mm
            )
            layer_stack_utils.translate_effect_layers_for_parent_keys(
                context, layer_stack_utils.gp_parent_keys_for_page(target), dx_mm, dy_mm
            )
            return True
        if kind == "coma":
            _move_panel(target, dx_mm, dy_mm)
            for child_kind, child, _data in self._snapshots:
                if child_kind == "balloon":
                    _move_balloon(page, child, dx_mm, dy_mm)
                elif child_kind == "text":
                    child.x_mm += dx_mm
                    child.y_mm += dy_mm
            if page is not None:
                layer_stack_utils.translate_gp_layers_for_parent_keys(
                    context, {layer_stack_utils.gp_parent_key_for_coma(page, target)}, dx_mm, dy_mm
                )
                layer_stack_utils.translate_effect_layers_for_parent_keys(
                    context, {layer_stack_utils.gp_parent_key_for_coma(page, target)}, dx_mm, dy_mm
                )
                layer_stack_utils.translate_raster_layers_for_parent_keys(
                    context, {layer_stack_utils.gp_parent_key_for_coma(page, target)}, dx_mm, dy_mm
                )
            return True
        if kind == "balloon" and page is not None:
            if _move_would_violate_layer_scope(context, page, target, dx_mm, dy_mm):
                return False
            _move_balloon(page, target, dx_mm, dy_mm)
            return True
        if kind == "text" and page is not None:
            if _move_would_violate_layer_scope(context, page, target, dx_mm, dy_mm):
                return False
            target.x_mm += dx_mm
            target.y_mm += dy_mm
            return True
        if kind == "image":
            page = get_active_page(context)
            if page is not None and _move_would_violate_layer_scope(context, page, target, dx_mm, dy_mm):
                return False
            target.x_mm += dx_mm
            target.y_mm += dy_mm
            return True
        if kind == "gp":
            gp_parent.translate_layer(target, dx_mm, dy_mm)
            return True
        if kind == "effect":
            gp_parent.translate_layer(target, dx_mm, dy_mm)
            return True
        obj = self._target.get("object")
        if obj is not None:
            obj.location.x += geom.mm_to_m(dx_mm)
            obj.location.y += geom.mm_to_m(dy_mm)
            return True
        return False


_CLASSES = (BNAME_OT_layer_move_tool,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
