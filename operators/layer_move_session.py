"""Reusable layer move drag session for tools outside the layer-move modal."""

from __future__ import annotations

from ..core.work import get_work
from ..utils import layer_stack as layer_stack_utils, page_grid
from . import layer_move_op, panel_picker


class LayerMoveDragSession:
    """既存のレイヤー移動処理をオブジェクトツールから再利用するセッション."""

    def __init__(self, context, start_world: tuple[float, float]) -> None:
        self._target = None
        self._snapshots = []
        self._last_world = start_world
        self._dragging = False
        self._moved = False
        self._started = bool(layer_move_op.BNAME_OT_layer_move_tool._begin_drag(self, context, start_world))

    @property
    def started(self) -> bool:
        return bool(self._started)

    @property
    def moved(self) -> bool:
        return bool(self._moved)

    def report(self, _levels, _message: str) -> None:
        return

    def _capture_snapshot(self, context, kind: str, resolved: dict) -> None:
        layer_move_op.BNAME_OT_layer_move_tool._capture_snapshot(self, context, kind, resolved)

    def _restore_snapshots(self, context) -> None:
        layer_move_op.BNAME_OT_layer_move_tool._restore_snapshots(self, context)

    def _apply_delta(self, context, dx_mm: float, dy_mm: float) -> bool:
        return bool(layer_move_op.BNAME_OT_layer_move_tool._apply_delta(self, context, dx_mm, dy_mm))

    def _push_undo_step(self) -> None:
        layer_move_op.BNAME_OT_layer_move_tool._push_undo_step(self)

    def apply(self, context, event) -> bool:
        coords = panel_picker._event_world_mm(context, event)
        if coords is None or self._last_world is None or not self._dragging:
            return False
        dx = coords[0] - self._last_world[0]
        dy = coords[1] - self._last_world[1]
        if dx == 0.0 and dy == 0.0:
            return False
        if self._apply_delta(context, dx, dy):
            self._last_world = coords
            self._moved = True
            layer_stack_utils.apply_stack_order(context)
            page_grid.apply_page_collection_transforms(context, get_work(context))
            layer_stack_utils.tag_view3d_redraw(context)
            return True
        return False

    def finish(self, context) -> bool:
        moved = bool(self._moved)
        if moved:
            self._push_undo_step()
            layer_stack_utils.sync_layer_stack(context)
        self._target = None
        self._snapshots = []
        self._last_world = None
        self._dragging = False
        self._moved = False
        return moved

    def cancel(self, context) -> None:
        self._restore_snapshots(context)
        self._target = None
        self._snapshots = []
        self._last_world = None
        self._dragging = False
        self._moved = False
        layer_stack_utils.tag_view3d_redraw(context)
