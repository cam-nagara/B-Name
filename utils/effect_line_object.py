"""効果線の Object kind 統一ヘルパ (Phase 5b).

計画書 ``docs/outliner_object_layer_plan_2026-04-30.md`` Phase 5b を実装。
既存の効果線は ``BName_EffectLines`` 単一 GP Object に複数 GP layer で管理
されている。Phase 5b では layer 単位を独立 GP Object 化する移行関数と、
既存 master 効果線 Object に kind="effect" を stamp する登録関数を提供する。

Phase 2 の gp_object_layer.py と同方針 (master を残置 + Object 群を追加)。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import gpencil as gp_utils
from . import layer_object_sync as los
from . import log
from . import object_naming as on

_logger = log.get_logger(__name__)

EFFECT_GP_OBJECT_NAME = "BName_EffectLines"
PER_LAYER_EFFECT_DATA_PREFIX = "BName_EffectGP_"
PROP_MIGRATED_FROM_EFFECT = "bname_migrated_from_effect_layer"
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


def list_master_effect_layers() -> list[bpy.types.GreasePencilLayer]:
    obj = bpy.data.objects.get(EFFECT_GP_OBJECT_NAME)
    if obj is None or getattr(obj, "type", "") != "GREASEPENCIL":
        return []
    layers = getattr(obj.data, "layers", None)
    if layers is None:
        return []
    return list(layers)


def register_master_effect_object(scene: bpy.types.Scene) -> Optional[bpy.types.Object]:
    """既存 master 効果線 Object を Outliner mirror に登録.

    Phase 5b では master 自体を消さず、bname_kind="effect_legacy" として
    stamp する。Outliner で D&D 可能になり、Phase 6 でユーザー判断による
    廃棄を促す。
    """
    obj = bpy.data.objects.get(EFFECT_GP_OBJECT_NAME)
    if obj is None:
        return None
    on.stamp_identity(
        obj,
        kind="effect_legacy",
        bname_id="effect_master",
        title="効果線 (master)",
        z_index=200,
        managed=True,
    )
    return obj


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
    """新規効果線 GP Object を生成し、Outliner mirror に登録.

    target_ref: フキダシ/コマ参照 (例: "balloon_001" / "p0001:c01")。
                custom property ``bname_effect_target`` に保存される。
    """
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
    return obj


def migrate_master_effect_lines_to_objects(
    *,
    scene: bpy.types.Scene,
    parent_kind: str,
    parent_key: str,
    base_z_index: int = 200,
    z_step: int = 10,
    dry_run: bool = True,
) -> dict:
    """master ``BName_EffectLines`` の各 layer を新 GP Object 群へ展開."""
    plan = {"would_migrate": [], "migrated": [], "skipped": []}
    layers = list_master_effect_layers()
    if not layers:
        return plan
    z = base_z_index
    for layer in layers:
        layer_name = str(getattr(layer, "name", "") or "")
        if not layer_name:
            plan["skipped"].append({"reason": "no name", "z_index": z})
            z += z_step
            continue
        bname_id = f"effect_master_{layer_name}"
        existing = on.find_object_by_bname_id(bname_id, kind="effect")
        if existing is not None:
            plan["skipped"].append({
                "layer": layer_name, "reason": "already migrated",
                "obj": existing.name, "z_index": z,
            })
            z += z_step
            continue
        plan["would_migrate"].append({
            "layer": layer_name, "bname_id": bname_id, "z_index": z,
        })
        if not dry_run:
            obj = create_effect_line_object(
                scene=scene,
                bname_id=bname_id,
                title=layer_name,
                z_index=z,
                parent_kind=parent_kind,
                parent_key=parent_key,
            )
            if obj is not None:
                obj[PROP_MIGRATED_FROM_EFFECT] = layer_name
                plan["migrated"].append({
                    "layer": layer_name, "obj": obj.name, "z_index": z,
                })
        z += z_step
    return plan
