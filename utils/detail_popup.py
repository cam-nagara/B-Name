"""選択レイヤーの詳細設定ダイアログを開く補助."""

from __future__ import annotations

import bpy

from . import layer_stack as layer_stack_utils
from . import log

_logger = log.get_logger(__name__)


def _active_detail_index(context) -> int:
    scene = getattr(context, "scene", None)
    if scene is None:
        return -1
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return -1
    index = int(getattr(scene, "bname_active_layer_stack_index", -1))
    if 0 <= index < len(stack):
        return index
    return -1


def open_active_detail(context) -> bool:
    """現在選択中のレイヤー詳細を、既存の詳細設定ダイアログで開く."""
    index = _active_detail_index(context)
    if index < 0:
        return False
    try:
        result = bpy.ops.bname.layer_stack_detail(
            "INVOKE_DEFAULT",
            index=index,
            preserve_edge_selection=True,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("detail popup: failed to open active layer detail")
        return False
    return "FINISHED" in result or "RUNNING_MODAL" in result


def open_active_detail_deferred(context, *, delay: float = 0.01) -> bool:
    """modal のイベント処理が抜けた直後に詳細設定を開く."""
    return open_active_detail_deferred_if(context, lambda: True, delay=delay)


def open_active_detail_deferred_if(context, predicate, *, delay: float = 0.01) -> bool:
    """predicate が真のままなら、少し後で詳細設定を開く."""
    scene = getattr(context, "scene", None)
    if scene is None:
        return False
    scene_name = str(getattr(scene, "name", "") or "")

    def _open():
        try:
            if not bool(predicate()):
                return None
        except Exception:  # noqa: BLE001
            _logger.exception("detail popup: predicate failed")
            return None
        current_scene = bpy.data.scenes.get(scene_name)
        if current_scene is None:
            return None
        ctx = bpy.context
        if getattr(ctx, "window", None) is None or getattr(ctx, "scene", None) is None:
            return None
        open_active_detail(ctx)
        return None

    try:
        bpy.app.timers.register(_open, first_interval=max(0.0, float(delay)))
    except Exception:  # noqa: BLE001
        _logger.exception("detail popup: failed to schedule active layer detail")
        return False
    return True
