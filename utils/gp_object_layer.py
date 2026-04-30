"""1 GP Object = 1 B-Name レイヤー モデル.

新規 GP Object をコマ Collection 直下に生成し、B-Name 安定 ID を stamp する。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import gpencil as gp_utils
from . import layer_object_sync as los
from . import log
from . import object_naming as on

_logger = log.get_logger(__name__)

# 新モデル GP Object の data 名 prefix
PER_LAYER_GP_DATA_PREFIX = "BName_LayerGP_"


def _resolve_unique_data_name(base: str) -> str:
    """``base`` をベースに、まだ未使用の GP data 名を返す.

    既存 data block を別 Object が使っている場合に複数 Object が同 data を
    共有してしまう事故を防ぐため、必ず未使用の名前を採用する。
    """
    coll = gp_utils._gp_data_blocks()
    if base not in coll:
        return base
    for i in range(1, 10000):
        candidate = f"{base}.{i:03d}"
        if candidate not in coll:
            return candidate
    # 例外的に到達したら Blender に任せて .NNN を付けさせる
    return base


def _new_gp_object_for_layer(
    *,
    bname_id: str,
    title: str,
) -> bpy.types.Object:
    """新 GP Object と GP data を生成する (まだ Collection に link しない).

    GP data 名は **必ず未使用** にする。既存 data 名と衝突したら .001 を
    付与した名前を採用し、別 Object との data 共有を防ぐ。
    """
    base_data_name = f"{PER_LAYER_GP_DATA_PREFIX}{bname_id}"
    data_name = _resolve_unique_data_name(base_data_name)
    gp_data = gp_utils.ensure_gpencil(data_name)
    obj_name = title or bname_id  # 後で assign_canonical_name で正規名へ書換え
    # bpy.data.objects.new は同名衝突で .001 を自動付加するので名前指定 OK
    obj = bpy.data.objects.new(obj_name, gp_data)
    # 既定レイヤー (content) を 1 つだけ用意。__bname_mask は後段で必要に応じて。
    if len(gp_data.layers) == 0:
        try:
            gp_utils.ensure_layer(gp_data, "content")
        except Exception:  # noqa: BLE001
            _logger.exception("new GP object: default layer create failed")
    return obj


def create_layer_gp_object(
    *,
    scene: bpy.types.Scene,
    bname_id: str,
    title: str,
    z_index: int,
    parent_kind: str,
    parent_key: str,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    """新 GP Object を生成し、B-Name 安定 ID を stamp してコマ Collection に link.

    既に同 ``bname_id`` の Object が存在すれば再利用する。

    Args:
        bname_id: ``"gp_xxxxxx"`` 形式の安定 ID。
        title: ユーザー表示名。
        z_index: 重なり順 (0 詰め 4 桁化される)。
        parent_kind: ``"page" | "coma" | "folder" | "outside" | "none"``。
        parent_key: 親キー (例: ``"p0001:c01"``)。
        folder_id: フォルダ配下時の folder_id。
    """
    if scene is None or not bname_id:
        return None
    obj = on.find_object_by_bname_id(bname_id, kind="gp")
    if obj is None:
        obj = _new_gp_object_for_layer(bname_id=bname_id, title=title)
    # stamp + link は layer_object_sync 経由 (Phase 0 で実装済)
    los.stamp_layer_object(
        obj,
        kind="gp",
        bname_id=bname_id,
        title=title,
        z_index=z_index,
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=folder_id,
        scene=scene,
    )
    # 黒線材質を確保 (空マテリアルだと Draw モードで白線になる)
    try:
        gp_utils.ensure_default_stroke_material(obj)
    except Exception:  # noqa: BLE001
        _logger.exception("create_layer_gp_object: default material failed")
    return obj


