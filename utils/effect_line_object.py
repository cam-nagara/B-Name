"""効果線 GP Object ヘルパ.

新規効果線 GP Object を生成し、Outliner mirror に登録する。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import gpencil as gp_utils
from . import layer_object_sync as los
from . import log
from . import object_naming as on

_logger = log.get_logger(__name__)

PER_LAYER_EFFECT_DATA_PREFIX = "BName_EffectGP_"
PROP_EFFECT_TARGET = "bname_effect_target"


def _resolve_unique_data_name(base: str) -> str:
    coll = gp_utils._gp_data_blocks()
    if base not in coll:
        return base
    for i in range(1, 10000):
        candidate = f"{base}.{i:03d}"
        if candidate not in coll:
            return candidate
    return base


def _new_effect_gp_object_for_layer(
    *, bname_id: str, title: str
) -> bpy.types.Object:
    base_data_name = f"{PER_LAYER_EFFECT_DATA_PREFIX}{bname_id}"
    data_name = _resolve_unique_data_name(base_data_name)
    gp_data = gp_utils.ensure_gpencil(data_name)
    obj_name = title or bname_id
    obj = bpy.data.objects.new(obj_name, gp_data)
    if len(gp_data.layers) == 0:
        try:
            gp_utils.ensure_layer(gp_data, "content")
        except Exception:  # noqa: BLE001
            _logger.exception("new effect GP: default layer create failed")
    return obj


def create_effect_line_object(
    *,
    scene: bpy.types.Scene,
    bname_id: str,
    title: str,
    z_index: int,
    parent_kind: str,
    parent_key: str,
    folder_id: str = "",
    target_ref: str = "",
) -> Optional[bpy.types.Object]:
    """新規効果線 GP Object を生成し、Outliner mirror に登録."""
    if scene is None or not bname_id:
        return None
    obj = on.find_object_by_bname_id(bname_id, kind="effect")
    if obj is None:
        obj = _new_effect_gp_object_for_layer(bname_id=bname_id, title=title)
    los.stamp_layer_object(
        obj,
        kind="effect",
        bname_id=bname_id,
        title=title,
        z_index=z_index,
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=folder_id,
        scene=scene,
    )
    if target_ref:
        obj[PROP_EFFECT_TARGET] = target_ref
    try:
        gp_utils.ensure_default_stroke_material(obj)
    except Exception:  # noqa: BLE001
        _logger.exception("create_effect_line_object: default material failed")
    try:
        from . import mask_apply

        mask_apply.apply_mask_to_layer_object(obj)
    except Exception:  # noqa: BLE001
        _logger.exception("create_effect_line_object: mask_apply failed")
    return obj
