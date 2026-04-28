"""Pencil+4 line-width synchronization for B-Name fisheye reduction mode."""

from __future__ import annotations

from collections.abc import Iterable

import bpy

from ...utils import log

_logger = log.get_logger(__name__)

PENCIL4_GROUP_PREFIX = "Pencil+ 4 Line Node Tree"
BRUSH_NODE_PREFIX = "Brush Settings"
ORIGINAL_SIZE_PROP = "original_size"


def iter_brush_nodes() -> Iterable[object]:
    """Pencil+4 の Brush Settings ノードだけを返す。"""
    for group in getattr(bpy.data, "node_groups", []):
        if not getattr(group, "name", "").startswith(PENCIL4_GROUP_PREFIX):
            continue
        for node in getattr(group, "nodes", []):
            if getattr(node, "name", "").startswith(BRUSH_NODE_PREFIX):
                yield node


def save_widths() -> int:
    """現在の Pencil+4 線幅を復元用の基準値として保存する。"""
    count = 0
    for node in iter_brush_nodes():
        try:
            node[ORIGINAL_SIZE_PROP] = float(node.size)
            count += 1
        except Exception:  # noqa: BLE001
            _logger.warning("Pencil+4 width save skipped for %s", getattr(node, "name", ""), exc_info=True)
    if count == 0:
        _logger.info("Pencil+4 ノードグループ未検出。線幅保存をスキップ")
    return count


def apply_scale(scale: float, *, ensure_saved: bool = False) -> int:
    """保存済み線幅に縮小率を掛けて適用する。"""
    scale = max(0.0, float(scale))
    count = 0
    seen = 0
    for node in iter_brush_nodes():
        seen += 1
        try:
            if ORIGINAL_SIZE_PROP not in node:
                if not ensure_saved:
                    continue
                node[ORIGINAL_SIZE_PROP] = float(node.size)
            node.size = float(node[ORIGINAL_SIZE_PROP]) * scale
            count += 1
        except Exception:  # noqa: BLE001
            _logger.warning("Pencil+4 width scale skipped for %s", getattr(node, "name", ""), exc_info=True)
    if seen == 0:
        _logger.info("Pencil+4 ノードグループ未検出。線幅連動をスキップ")
    return count


def restore() -> int:
    """保存済み線幅を Pencil+4 ノードへ戻す。"""
    count = 0
    seen = 0
    for node in iter_brush_nodes():
        seen += 1
        try:
            if ORIGINAL_SIZE_PROP not in node:
                continue
            node.size = float(node[ORIGINAL_SIZE_PROP])
            count += 1
        except Exception:  # noqa: BLE001
            _logger.warning("Pencil+4 width restore skipped for %s", getattr(node, "name", ""), exc_info=True)
    if seen == 0:
        _logger.info("Pencil+4 ノードグループ未検出。線幅復元をスキップ")
    return count
