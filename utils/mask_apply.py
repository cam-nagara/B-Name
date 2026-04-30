"""コマ/ページマスクをレイヤーに適用する.

`utils/mask_object.py` で生成した mask Mesh Object を実際にレイヤー
Object 側で参照して、コマ枠/ページ枠の外をクリップする。

実装方針:
    - Mesh 系レイヤー (raster / image plane / balloon plane / text plane):
      Boolean Modifier (Intersect) で実形状クリップ。Modifier は冪等で
      ensure_*_mask_modifier 関数で保守する。
    - GP 系レイヤー (gp / effect): GreasePencil v3 の **Mask Modifier** で
      mask Object の形状内側のみ表示する。

Boolean は EEVEE Next でも動作する一般 Modifier。GP の Mask Modifier は
``GreasePencilMaskModifier`` (旧名 ``GP_Mask``) で、5.1 GP v3 でも有効。

mask Object 自体は ``hide_render=True`` だが、Boolean / Mask Modifier の
target として参照するだけなら hidden でも動作する。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import log
from . import mask_object as mo
from . import object_naming as on

_logger = log.get_logger(__name__)

MOD_NAME_COMA_MASK = "BName Coma Mask"
MOD_NAME_PAGE_MASK = "BName Page Mask"


def _resolve_coma_mask_object(parent_key: str) -> Optional[bpy.types.Object]:
    """parent_key (例 "p0001:c01") からコママスク Object を取得."""
    if not parent_key or ":" not in parent_key:
        return None
    page_id, coma_id = parent_key.split(":", 1)
    name = f"{mo.COMA_MASK_NAME_PREFIX}{page_id}_{coma_id}"
    return bpy.data.objects.get(name)


def _resolve_page_mask_object(parent_key: str) -> Optional[bpy.types.Object]:
    """parent_key (page_id) からページマスク Object を取得."""
    page_id = parent_key.split(":", 1)[0] if parent_key else ""
    if not page_id:
        return None
    name = f"{mo.PAGE_MASK_NAME_PREFIX}{page_id}"
    return bpy.data.objects.get(name)


def _ensure_boolean_intersect_modifier(
    obj: bpy.types.Object, mod_name: str, target: bpy.types.Object
) -> None:
    """Mesh Object に Boolean Intersect Modifier を ensure (target 形状で切抜)."""
    if obj is None or target is None or obj.type != "MESH":
        return
    mod = obj.modifiers.get(mod_name)
    if mod is None:
        try:
            mod = obj.modifiers.new(name=mod_name, type="BOOLEAN")
        except Exception:  # noqa: BLE001
            _logger.exception("mask_apply: boolean modifier create failed")
            return
    try:
        mod.operation = "INTERSECT"
        mod.solver = "FAST"
        mod.object = target
    except Exception:  # noqa: BLE001
        _logger.exception("mask_apply: boolean modifier setup failed")


def _ensure_gp_mask_modifier(
    obj: bpy.types.Object, mod_name: str, target: bpy.types.Object
) -> None:
    """GP Object に対する mask の適用は Blender 5.1 GP v3 では mask_layers を
    使う方式に分岐するが、外部 Mesh Object をマスク source にする一般的手段は
    無い。Phase 5d で `__bname_mask` 内蔵レイヤー方式を実装するまで記録のみ。
    """
    # 既存 modifier があれば剥がす (古い Modifier が残っている場合の掃除)
    existing = obj.modifiers.get(mod_name) if obj is not None else None
    if existing is not None:
        try:
            obj.modifiers.remove(existing)
        except Exception:  # noqa: BLE001
            pass
    _logger.debug(
        "mask_apply: GP %s への外部 mask 適用は未対応 (Phase 5d で対応予定)",
        getattr(obj, "name", "?"),
    )


def _remove_modifier_if_present(obj: bpy.types.Object, mod_name: str) -> None:
    if obj is None:
        return
    mod = obj.modifiers.get(mod_name)
    if mod is None:
        return
    try:
        obj.modifiers.remove(mod)
    except Exception:  # noqa: BLE001
        pass


def apply_mask_to_layer_object(obj: bpy.types.Object) -> None:
    """1 つのレイヤー Object にコマ/ページマスクを適用する.

    parent_key を見て:
        - "<page>:<coma>" 形式 → コママスク Modifier を ensure (ページマスクは外す)
        - "<page>" 形式 → ページマスク Modifier を ensure (コママスクは外す)
        - 空 / outside → どちらも外す
    """
    if obj is None or not on.is_managed(obj):
        return
    parent_key = str(obj.get(on.PROP_PARENT_KEY, "") or "")
    coma_target = _resolve_coma_mask_object(parent_key)
    page_target = _resolve_page_mask_object(parent_key)

    obj_type = getattr(obj, "type", "")
    if obj_type == "MESH":
        if coma_target is not None:
            _ensure_boolean_intersect_modifier(obj, MOD_NAME_COMA_MASK, coma_target)
            _remove_modifier_if_present(obj, MOD_NAME_PAGE_MASK)
        elif page_target is not None and ":" not in parent_key:
            _ensure_boolean_intersect_modifier(obj, MOD_NAME_PAGE_MASK, page_target)
            _remove_modifier_if_present(obj, MOD_NAME_COMA_MASK)
        else:
            _remove_modifier_if_present(obj, MOD_NAME_COMA_MASK)
            _remove_modifier_if_present(obj, MOD_NAME_PAGE_MASK)
    elif obj_type == "GREASEPENCIL":
        if coma_target is not None:
            _ensure_gp_mask_modifier(obj, MOD_NAME_COMA_MASK, coma_target)
            _remove_modifier_if_present(obj, MOD_NAME_PAGE_MASK)
        elif page_target is not None and ":" not in parent_key:
            _ensure_gp_mask_modifier(obj, MOD_NAME_PAGE_MASK, page_target)
            _remove_modifier_if_present(obj, MOD_NAME_COMA_MASK)
        else:
            _remove_modifier_if_present(obj, MOD_NAME_COMA_MASK)
            _remove_modifier_if_present(obj, MOD_NAME_PAGE_MASK)


def apply_masks_to_all_managed(scene: bpy.types.Scene) -> int:
    """全 B-Name 管理 Object にマスクを適用する。適用件数を返す."""
    if scene is None:
        return 0
    n = 0
    for obj in on.iter_managed_objects():
        apply_mask_to_layer_object(obj)
        n += 1
    return n
