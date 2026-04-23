"""Grease Pencil v3 ヘルパ.

計画書 10 章参照:
- データブロック: ``bpy.data.grease_pencils_v3``
- レイヤー: ``bpy.types.GreasePencilLayer``
- レイヤーグループ: ``bpy.types.GreasePencilLayerGroup``
- 描画: ``bpy.types.GreasePencilDrawing``

Blender 4.3+ / 5.x の v3 API のみを使い、v2 (``bpy.data.grease_pencil``) は
使わない。
"""

from __future__ import annotations

from typing import Iterable

import bpy

from ..utils import log

_logger = log.get_logger(__name__)


def ensure_gpencil(name: str):
    """名前つき GreasePencil v3 データブロックを取得/生成."""
    gp_data = bpy.data.grease_pencils_v3.get(name)
    if gp_data is None:
        gp_data = bpy.data.grease_pencils_v3.new(name)
    return gp_data


def ensure_gpencil_object(name: str, link_to_collection=True):
    """v3 GreasePencil Object を取得/生成."""
    obj = bpy.data.objects.get(name)
    if obj is None:
        gp_data = ensure_gpencil(name + "_data")
        obj = bpy.data.objects.new(name, gp_data)
        if link_to_collection and bpy.context.scene is not None:
            bpy.context.scene.collection.objects.link(obj)
    return obj


def ensure_layer(gp_data, layer_name: str):
    """GreasePencil v3 レイヤーを取得/生成."""
    layer = gp_data.layers.get(layer_name)
    if layer is None:
        layer = gp_data.layers.new(layer_name)
    return layer


def add_stroke_to_drawing(
    drawing,
    points_xyz: Iterable[tuple[float, float, float]],
    radius: float = 0.01,
    cyclic: bool = False,
) -> bool:
    """GreasePencilDrawing に 1 ストロークを追加.

    Blender 5.x 系の API では ``drawing.add_strokes([n_points])`` で新規
    ストロークを作り、attribute API で ``position`` / ``radius`` を書き込む。
    動作しないバージョンでは False を返す。
    """
    pts = list(points_xyz)
    if not pts:
        return False
    try:
        strokes = drawing.add_strokes([len(pts)])
        stroke = strokes[0]
        stroke.cyclic = cyclic
        pos_attr = drawing.attributes.get("position")
        if pos_attr is None:
            return False
        offset = stroke.points.offset
        for i, (x, y, z) in enumerate(pts):
            pos_attr.data[offset + i].vector = (x, y, z)
        rad_attr = drawing.attributes.get("radius")
        if rad_attr is not None:
            for i in range(len(pts)):
                rad_attr.data[offset + i].value = radius
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("add_stroke_to_drawing failed: %s", exc)
        return False


def ensure_active_frame(layer, frame_number: int | None = None):
    """指定フレームに GreasePencilFrame を取得/生成.

    frame_number=None なら現在のシーンフレーム。
    """
    if frame_number is None:
        frame_number = bpy.context.scene.frame_current
    # v3 は layer.frames リストで管理。既存があれば再利用。
    for frame in layer.frames:
        if frame.frame_number == frame_number:
            return frame
    try:
        return layer.frames.new(frame_number=frame_number)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("frame.new failed: %s", exc)
        return None
