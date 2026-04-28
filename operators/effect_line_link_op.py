"""リンク効果線の作成と連動伝播."""

from __future__ import annotations

import uuid

import bpy
from bpy.types import Operator

from ..utils import layer_stack as layer_stack_utils, log, object_selection

_logger = log.get_logger(__name__)

LINK_ID_PROP = "link_id"

_LINKED_SHAPE_FIELDS = {
    "rotation_deg",
    "start_shape",
    "start_to_coma_frame",
    "start_rounded_corner_enabled",
    "start_rounded_corner_radius_mm",
    "start_cloud_bump_width_mm",
    "start_cloud_bump_height_mm",
    "start_cloud_offset_percent",
    "start_cloud_sub_width_ratio",
    "start_cloud_sub_height_ratio",
    "end_shape",
    "end_rounded_corner_enabled",
    "end_rounded_corner_radius_mm",
    "end_cloud_bump_width_mm",
    "end_cloud_bump_height_mm",
    "end_cloud_offset_percent",
    "end_cloud_sub_width_ratio",
    "end_cloud_sub_height_ratio",
}


def _effect_link_id(effect_op, obj, layer) -> str:
    entry = effect_op._effect_meta(obj).get(effect_op._layer_meta_key(layer), {})
    if not isinstance(entry, dict):
        return ""
    return str(entry.get(LINK_ID_PROP, "") or "")


def _set_effect_link_id(effect_op, obj, layer, link_id: str) -> None:
    if obj is None or layer is None:
        return
    key = effect_op._layer_meta_key(layer)
    if not key:
        return
    meta = effect_op._effect_meta(obj)
    entry = meta.get(key, {}) if isinstance(meta.get(key, {}), dict) else {}
    entry = dict(entry)
    if link_id:
        entry[LINK_ID_PROP] = str(link_id)
    else:
        entry.pop(LINK_ID_PROP, None)
    meta[key] = entry
    effect_op._write_effect_meta(obj, meta)


def _ensure_effect_link_pair(effect_op, obj, source_layer, dest_layer) -> str:
    link_id = _effect_link_id(effect_op, obj, source_layer)
    if not link_id:
        link_id = uuid.uuid4().hex
        _set_effect_link_id(effect_op, obj, source_layer, link_id)
    _set_effect_link_id(effect_op, obj, dest_layer, link_id)
    return link_id


def _copy_linked_shape_params(source_params: dict, dest_params: dict) -> dict:
    out = dict(dest_params or {})
    for field in _LINKED_SHAPE_FIELDS:
        if field in source_params:
            out[field] = source_params[field]
    return out


def _params_proxy_from_data(effect_op, context, data: dict):
    scene_params = getattr(context.scene, "bname_effect_line_params", None)
    if scene_params is None:
        return None
    return effect_op._EffectParamProxy(scene_params, data)


def propagate_linked_effect_strokes(
    context,
    obj,
    source_layer,
    bounds: tuple[float, float, float, float],
    source_params_data: dict,
) -> None:
    from . import effect_line_op

    link_id = _effect_link_id(effect_line_op, obj, source_layer)
    if not link_id:
        return
    meta = effect_line_op._effect_meta(obj)
    source_key = effect_line_op._layer_meta_key(source_layer)
    layers = getattr(getattr(obj, "data", None), "layers", None)
    for key, entry in list(meta.items()):
        if key == source_key or not isinstance(entry, dict):
            continue
        if str(entry.get(LINK_ID_PROP, "") or "") != link_id:
            continue
        peer_layer = layer_stack_utils._find_gp_layer_by_key(layers, key)
        if peer_layer is None:
            continue
        peer_params = _copy_linked_shape_params(
            source_params_data,
            effect_line_op._layer_params_data(obj, peer_layer),
        )
        params_proxy = _params_proxy_from_data(effect_line_op, context, peer_params)
        if params_proxy is None:
            continue
        effect_line_op._write_effect_strokes(
            context,
            obj,
            peer_layer,
            bounds,
            seed=effect_line_op._seed_for_layer(obj, peer_layer),
            params_override=params_proxy,
            propagate_link=False,
        )


class BNAME_OT_effect_line_create_linked(Operator):
    bl_idname = "bname.effect_line_create_linked"
    bl_label = "リンク効果線を作成"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if getattr(getattr(context, "scene", None), "bname_active_layer_kind", "") != "effect":
            return False
        from . import effect_line_op

        obj, layer, bounds = effect_line_op.active_effect_layer_bounds(context)
        return obj is not None and layer is not None and bounds is not None

    def execute(self, context):
        from . import effect_line_op

        obj, source_layer, bounds = effect_line_op.active_effect_layer_bounds(context)
        if obj is None or source_layer is None or bounds is None:
            self.report({"ERROR"}, "リンク元の効果線が選択されていません")
            return {"CANCELLED"}
        layers = getattr(getattr(obj, "data", None), "layers", None)
        if layers is None:
            return {"CANCELLED"}
        try:
            context.view_layer.objects.active = obj
            obj.select_set(True)
            obj.data.layers.active = source_layer
            result = bpy.ops.grease_pencil.layer_duplicate("EXEC_DEFAULT", empty_keyframes=False)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("effect_line_create_linked: layer duplicate failed")
            self.report({"ERROR"}, f"リンク効果線の複製に失敗しました: {exc}")
            return {"CANCELLED"}
        if "FINISHED" not in result:
            return {"CANCELLED"}
        linked_layer = getattr(layers, "active", None)
        if linked_layer is None or linked_layer == source_layer:
            self.report({"ERROR"}, "複製された効果線レイヤーを取得できません")
            return {"CANCELLED"}
        effect_line_op.copy_layer_effect_meta(obj, source_layer, linked_layer)
        _ensure_effect_link_pair(effect_line_op, obj, source_layer, linked_layer)
        effect_line_op._write_effect_strokes(context, obj, linked_layer, bounds)
        effect_line_op._select_effect_layer(context, obj, linked_layer)
        object_selection.select_key(
            context,
            object_selection.effect_key(linked_layer),
            mode="single",
        )
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        self.report({"INFO"}, "リンク効果線を作成しました")
        return {"FINISHED"}


_CLASSES = (BNAME_OT_effect_line_create_linked,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
