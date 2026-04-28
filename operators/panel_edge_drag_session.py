"""Reusable panel edge/vertex drag session for modal tools."""

from __future__ import annotations

import bpy

from ..utils import log
from . import panel_edge_move_op

_logger = log.get_logger(__name__)


class PanelEdgeDragSession:
    """枠線選択オペレータ外から辺/頂点ドラッグ処理を再利用するセッション."""

    def __init__(
        self,
        context,
        work,
        area,
        region,
        rv3d,
        selection: dict,
        start_world: tuple[float, float] | None,
    ) -> None:
        self._context = context
        self._work = work
        self._area = area
        self._region = region
        self._rv3d = rv3d
        self._selection = selection
        self._drag_start_world = start_world
        self._original_geometry = None
        self._restore_states = []
        self._drag_moved = False
        panel_edge_move_op.BNAME_OT_panel_edge_move._capture_original_geometry(self)
        self._restore_states = self._capture_restore_states()

    def _to_window(self, ev):
        return ev.mouse_x - self._region.x, ev.mouse_y - self._region.y

    def _tag_redraw(self) -> None:
        if self._region is not None:
            self._region.tag_redraw()

    def _push_undo_step(self, message: str) -> None:
        try:
            bpy.ops.ed.undo_push(message=message)
        except Exception:  # noqa: BLE001
            _logger.exception("edge_drag_session: undo_push failed")

    def report(self, _levels, message: str) -> None:
        _logger.info(message)

    def apply(self, event) -> bool:
        before = bool(getattr(self, "_drag_moved", False))
        panel_edge_move_op.BNAME_OT_panel_edge_move._apply_drag(self, event)
        self._tag_redraw()
        return bool(getattr(self, "_drag_moved", False)) or before

    def finish(self, message: str = "B-Name: 枠線移動") -> bool:
        changed = panel_edge_move_op.BNAME_OT_panel_edge_move._geometry_changed(self)
        if changed:
            panel_edge_move_op.BNAME_OT_panel_edge_move._save_changes(self)
            self._push_undo_step(message)
        self._tag_redraw()
        return bool(changed)

    def cancel(self) -> None:
        if self._work is None:
            return
        for state in self._restore_states:
            page_index = int(state["page"])
            panel_index = int(state["panel"])
            if not (0 <= page_index < len(self._work.pages)):
                continue
            page = self._work.pages[page_index]
            if not (0 <= panel_index < len(page.panels)):
                continue
            panel = page.panels[panel_index]
            if state["shape"] == "rect":
                x, y, w, h = state["rect"]
                panel.shape_type = "rect"
                panel.rect_x_mm = x
                panel.rect_y_mm = y
                panel.rect_width_mm = w
                panel.rect_height_mm = h
            else:
                panel_edge_move_op._set_panel_polygon(panel, state["poly"])
        self._tag_redraw()

    def _capture_restore_states(self) -> list[dict]:
        if self._work is None:
            return []
        refs: set[tuple[int, int]] = set()
        sel = self._selection
        if sel is not None:
            refs.add((int(sel.get("page", -1)), int(sel.get("panel", -1))))
        original = self._original_geometry or {}
        for key in ("adjacent_edges", "vertex_adjacent_edges", "shared_vertices"):
            for item in original.get(key, []) or []:
                refs.add((int(item.get("page", -1)), int(item.get("panel", -1))))
        states = []
        for page_index, panel_index in sorted(refs):
            if not (0 <= page_index < len(self._work.pages)):
                continue
            page = self._work.pages[page_index]
            if not (0 <= panel_index < len(page.panels)):
                continue
            panel = page.panels[panel_index]
            states.append({
                "page": page_index,
                "panel": panel_index,
                "shape": str(getattr(panel, "shape_type", "") or ""),
                "rect": (
                    float(getattr(panel, "rect_x_mm", 0.0)),
                    float(getattr(panel, "rect_y_mm", 0.0)),
                    float(getattr(panel, "rect_width_mm", 0.0)),
                    float(getattr(panel, "rect_height_mm", 0.0)),
                ),
                "poly": panel_edge_move_op._panel_polygon(panel),
            })
        return states
