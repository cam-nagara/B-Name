"""統合レイヤースタックの同期・選択・並び替えヘルパ."""

from __future__ import annotations

from dataclasses import dataclass

import bpy

from ..core.work import get_active_page
from . import gpencil as gp_utils
from . import log

_logger = log.get_logger(__name__)

EFFECT_GP_OBJECT_NAME = "BName_EffectLines"


@dataclass(frozen=True)
class LayerTarget:
    kind: str
    key: str
    label: str
    parent_key: str = ""
    depth: int = 0

    @property
    def uid(self) -> str:
        return target_uid(self.kind, self.key)


def target_uid(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def stack_item_uid(item) -> str:
    return target_uid(getattr(item, "kind", ""), getattr(item, "key", ""))


def get_effect_gp_object():
    obj = bpy.data.objects.get(EFFECT_GP_OBJECT_NAME)
    if obj is not None and getattr(obj, "type", "") == "GREASEPENCIL":
        return obj
    return None


def _node_stack_key(node) -> str:
    try:
        ptr = int(node.as_pointer())
    except Exception:  # noqa: BLE001
        ptr = 0
    if ptr:
        return f"ptr_{ptr:x}"
    return str(getattr(node, "name", "") or "")


def _ensure_node_stack_key(node, used: set[str], prefix: str) -> str:
    key = _node_stack_key(node)
    if key and key not in used:
        used.add(key)
        return key
    i = 1
    while True:
        candidate = f"{prefix}_{i:04d}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        i += 1


def _ensure_unique_id(entry, used: set[str], prefix: str) -> str:
    key = str(getattr(entry, "id", "") or "").strip()
    if key and key not in used:
        used.add(key)
        return key
    i = 1
    while True:
        candidate = f"{prefix}_{i:04d}"
        if candidate not in used:
            try:
                entry.id = candidate
            except Exception:  # noqa: BLE001
                pass
            used.add(candidate)
            return candidate
        i += 1


def _iter_gp_targets(obj, *, kind: str, depth: int = 0, parent_key: str = ""):
    data = getattr(obj, "data", None)
    used: set[str] = set()
    nodes = getattr(data, "root_nodes", None)
    if nodes is None:
        layers = getattr(data, "layers", None)
        if layers is None:
            return
        for layer in reversed(list(layers)):
            key = _ensure_node_stack_key(layer, used, kind)
            yield LayerTarget(kind, key, layer.name, parent_key, depth)
        return
    yield from _iter_gp_node_targets(
        nodes, kind=kind, depth=depth, parent_key=parent_key, used=used
    )


def _iter_gp_node_targets(nodes, *, kind: str, depth: int, parent_key: str, used: set[str]):
    for node in reversed(list(nodes)):
        if gp_utils.is_layer_group(node):
            group_key = _ensure_node_stack_key(node, used, "folder")
            yield LayerTarget("gp_folder", group_key, node.name, parent_key, depth)
            if bool(getattr(node, "is_expanded", True)):
                yield from _iter_gp_node_targets(
                    getattr(node, "children", []),
                    kind=kind,
                    depth=depth + 1,
                    parent_key=group_key,
                    used=used,
                )
        else:
            key = _ensure_node_stack_key(node, used, kind)
            yield LayerTarget(kind, key, node.name, parent_key, depth)


def collect_targets(context) -> list[LayerTarget]:
    """現在のページ/シーンから、前面→背面の統合レイヤー候補を返す."""
    scene = context.scene
    page = get_active_page(context)
    targets: list[LayerTarget] = []

    if page is not None:
        used_text: set[str] = set()
        for entry in reversed(list(getattr(page, "texts", []))):
            key = _ensure_unique_id(entry, used_text, "text")
            label = getattr(entry, "body", "") or key
            targets.append(LayerTarget("text", key, label))

        used_balloon: set[str] = set()
        for entry in reversed(list(getattr(page, "balloons", []))):
            key = _ensure_unique_id(entry, used_balloon, "balloon")
            targets.append(LayerTarget("balloon", key, key))

    effect_obj = get_effect_gp_object()
    if effect_obj is not None:
        targets.extend(_iter_gp_targets(effect_obj, kind="effect"))

    image_layers = getattr(scene, "bname_image_layers", None)
    if image_layers is not None:
        used_image: set[str] = set()
        for entry in reversed(list(image_layers)):
            key = _ensure_unique_id(entry, used_image, "image")
            label = getattr(entry, "title", "") or key
            targets.append(LayerTarget("image", key, label))

    gp_obj = gp_utils.get_master_gpencil()
    if gp_obj is not None:
        targets.extend(_iter_gp_targets(gp_obj, kind="gp"))

    return targets


def _set_item_from_target(item, target: LayerTarget) -> None:
    item.kind = target.kind
    item.key = target.key
    item.label = target.label
    item.parent_key = target.parent_key
    item.depth = target.depth


def sync_layer_stack(context, *, preserve_active_index: bool = False):
    """統合レイヤーリストを実データに同期する。

    既存行の並びは維持し、消えた実体だけを削除、新規実体だけを前面側へ
    追加する。これにより UIList 側のD&D並び替えを上書きしない。
    """
    scene = context.scene
    stack = getattr(scene, "bname_layer_stack", None)
    if stack is None:
        return None
    old_active_index = int(getattr(scene, "bname_active_layer_stack_index", -1))

    targets = collect_targets(context)
    target_by_uid = {target.uid: target for target in targets}

    for i in range(len(stack) - 1, -1, -1):
        if stack_item_uid(stack[i]) not in target_by_uid:
            stack.remove(i)

    existing = {stack_item_uid(item) for item in stack}
    for item in stack:
        target = target_by_uid.get(stack_item_uid(item))
        if target is not None:
            _set_item_from_target(item, target)

    missing = [target for target in targets if target.uid not in existing]
    for target in reversed(missing):
        item = stack.add()
        _set_item_from_target(item, target)
        stack.move(len(stack) - 1, 0)

    if preserve_active_index and 0 <= old_active_index < len(stack):
        scene.bname_active_layer_stack_index = old_active_index
    else:
        _sync_active_stack_index(context)
    return stack


def _active_key_from_scene(context) -> tuple[str, str] | None:
    scene = context.scene
    kind = getattr(scene, "bname_active_layer_kind", "gp")
    page = get_active_page(context)

    if kind == "gp_folder":
        key = str(getattr(scene, "bname_active_gp_folder_key", "") or "")
        obj = gp_utils.get_master_gpencil()
        groups = getattr(getattr(obj, "data", None), "layer_groups", None)
        group = _find_gp_group_by_key(groups, key)
        if group is not None:
            key = _node_stack_key(group)
            scene.bname_active_gp_folder_key = key
            return "gp_folder", key
    if kind == "image":
        coll = getattr(scene, "bname_image_layers", None)
        idx = int(getattr(scene, "bname_active_image_layer_index", -1))
        if coll is not None and 0 <= idx < len(coll):
            return "image", getattr(coll[idx], "id", "")
    if kind == "balloon" and page is not None:
        idx = int(getattr(page, "active_balloon_index", -1))
        if 0 <= idx < len(page.balloons):
            return "balloon", getattr(page.balloons[idx], "id", "")
    if kind == "text" and page is not None:
        idx = int(getattr(page, "active_text_index", -1))
        if 0 <= idx < len(page.texts):
            return "text", getattr(page.texts[idx], "id", "")
    if kind == "effect":
        key = str(getattr(scene, "bname_active_effect_layer_name", "") or "")
        obj = get_effect_gp_object()
        layers = getattr(getattr(obj, "data", None), "layers", None)
        layer = _find_gp_layer_by_key(layers, key)
        if layer is None and layers is not None:
            layer = getattr(layers, "active", None)
        if layer is not None:
            return "effect", _node_stack_key(layer)

    obj = gp_utils.get_master_gpencil()
    layer = getattr(getattr(obj, "data", None), "layers", None)
    active = getattr(layer, "active", None) if layer is not None else None
    if active is not None:
        return "gp", _node_stack_key(active)
    return None


def _sync_active_stack_index(context) -> None:
    scene = context.scene
    stack = getattr(scene, "bname_layer_stack", None)
    if stack is None:
        return
    active_key = _active_key_from_scene(context)
    if active_key is not None:
        uid = target_uid(*active_key)
        for i, item in enumerate(stack):
            if stack_item_uid(item) == uid:
                scene.bname_active_layer_stack_index = i
                return
    idx = int(getattr(scene, "bname_active_layer_stack_index", -1))
    if idx >= len(stack):
        scene.bname_active_layer_stack_index = len(stack) - 1
    elif idx < -1:
        scene.bname_active_layer_stack_index = -1


def _find_by_id(coll, key: str):
    for i, entry in enumerate(coll):
        if getattr(entry, "id", "") == key:
            return i, entry
    return -1, None


def _find_gp_layer_by_key(layers, key: str):
    if layers is None:
        return None
    for layer in layers:
        if _node_stack_key(layer) == key or getattr(layer, "name", "") == key:
            return layer
    return None


def _find_gp_group_by_key(groups, key: str):
    if groups is None:
        return None
    for group in groups:
        if _node_stack_key(group) == key or getattr(group, "name", "") == key:
            return group
    return None


def resolve_stack_item(context, item):
    """スタック行が参照する実体を辞書で返す。見つからなければ None."""
    if item is None:
        return None
    kind = getattr(item, "kind", "")
    key = getattr(item, "key", "")
    scene = context.scene
    page = get_active_page(context)

    if kind == "gp":
        obj = gp_utils.get_master_gpencil()
        layer = getattr(getattr(obj, "data", None), "layers", None)
        target = _find_gp_layer_by_key(layer, key)
        return {"kind": kind, "target": target, "object": obj, "index": -1}
    if kind == "gp_folder":
        obj = gp_utils.get_master_gpencil()
        groups = getattr(getattr(obj, "data", None), "layer_groups", None)
        target = _find_gp_group_by_key(groups, key)
        return {"kind": kind, "target": target, "object": obj, "index": -1}
    if kind == "image":
        coll = getattr(scene, "bname_image_layers", None)
        if coll is None:
            return None
        idx, entry = _find_by_id(coll, key)
        return {"kind": kind, "target": entry, "object": None, "index": idx}
    if kind == "balloon" and page is not None:
        idx, entry = _find_by_id(page.balloons, key)
        return {"kind": kind, "target": entry, "object": None, "index": idx}
    if kind == "text" and page is not None:
        idx, entry = _find_by_id(page.texts, key)
        return {"kind": kind, "target": entry, "object": None, "index": idx}
    if kind == "effect":
        obj = get_effect_gp_object()
        layers = getattr(getattr(obj, "data", None), "layers", None)
        target = _find_gp_layer_by_key(layers, key)
        return {"kind": kind, "target": target, "object": obj, "index": -1}
    return None


def active_stack_item(context):
    stack = sync_layer_stack(context)
    if stack is None:
        return None
    idx = int(getattr(context.scene, "bname_active_layer_stack_index", -1))
    if 0 <= idx < len(stack):
        return stack[idx]
    return None


def _set_active_object(context, obj) -> None:
    if obj is None or context.view_layer is None:
        return
    try:
        context.view_layer.objects.active = obj
        obj.select_set(True)
    except Exception:  # noqa: BLE001
        pass


def select_stack_index(context, index: int) -> bool:
    stack = sync_layer_stack(context, preserve_active_index=True)
    if stack is None or not (0 <= index < len(stack)):
        return False
    context.scene.bname_active_layer_stack_index = index
    item = stack[index]
    resolved = resolve_stack_item(context, item)
    if resolved is None or resolved.get("target") is None:
        return False

    kind = item.kind
    scene = context.scene
    page = get_active_page(context)
    if kind == "gp":
        obj = resolved.get("object")
        layer = resolved.get("target")
        _set_active_object(context, obj)
        try:
            obj.data.layers.active = layer
            gp_utils.ensure_active_frame(layer)
            gp_utils.ensure_layer_material(obj, layer, activate=True, assign_existing=True)
        except Exception:  # noqa: BLE001
            _logger.exception("select gp layer failed")
        scene.bname_active_layer_kind = "gp"
        scene.bname_active_gp_folder_key = ""
    elif kind == "gp_folder":
        _set_active_object(context, resolved.get("object"))
        scene.bname_active_gp_folder_key = item.key
        scene.bname_active_layer_kind = "gp_folder"
    elif kind == "image":
        scene.bname_active_image_layer_index = int(resolved.get("index", -1))
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = "image"
    elif kind == "balloon" and page is not None:
        page.active_balloon_index = int(resolved.get("index", -1))
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = "balloon"
    elif kind == "text" and page is not None:
        page.active_text_index = int(resolved.get("index", -1))
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = "text"
    elif kind == "effect":
        obj = resolved.get("object")
        layer = resolved.get("target")
        _set_active_object(context, obj)
        try:
            obj.data.layers.active = layer
        except Exception:  # noqa: BLE001
            pass
        scene.bname_active_effect_layer_name = item.key
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = "effect"
    tag_view3d_redraw(context)
    return True


def move_stack_item(context, from_index: int, to_index: int) -> bool:
    stack = sync_layer_stack(context, preserve_active_index=True)
    if stack is None or len(stack) == 0:
        return False
    to_index = max(0, min(to_index, len(stack) - 1))
    if not (0 <= from_index < len(stack)) or from_index == to_index:
        return False
    stack.move(from_index, to_index)
    context.scene.bname_active_layer_stack_index = to_index
    apply_stack_order(context)
    select_stack_index(context, to_index)
    return True


def _reorder_collection(coll, desired_back_to_front: list[str], key_fn) -> None:
    actual = [key_fn(entry) for entry in coll]
    desired = [key for key in desired_back_to_front if key in actual]
    desired.extend(key for key in actual if key not in desired)
    for target_index, key in enumerate(desired):
        current_index = next(
            (i for i, entry in enumerate(coll) if key_fn(entry) == key),
            -1,
        )
        if current_index >= 0 and current_index != target_index:
            coll.move(current_index, target_index)


def _restore_active_collection_index(owner, prop_name: str, coll, active_key: str) -> None:
    for i, entry in enumerate(coll):
        if getattr(entry, "id", "") == active_key:
            setattr(owner, prop_name, i)
            return
    setattr(owner, prop_name, 0 if len(coll) > 0 else -1)


def _apply_simple_collection_orders(context, stack) -> None:
    scene = context.scene
    image_layers = getattr(scene, "bname_image_layers", None)
    if image_layers is not None:
        active_key = ""
        idx = int(getattr(scene, "bname_active_image_layer_index", -1))
        if 0 <= idx < len(image_layers):
            active_key = getattr(image_layers[idx], "id", "")
        front = [item.key for item in stack if item.kind == "image"]
        _reorder_collection(image_layers, list(reversed(front)), lambda entry: entry.id)
        if active_key:
            _restore_active_collection_index(
                scene, "bname_active_image_layer_index", image_layers, active_key
            )

    page = get_active_page(context)
    if page is None:
        return
    active_balloon = ""
    if 0 <= page.active_balloon_index < len(page.balloons):
        active_balloon = page.balloons[page.active_balloon_index].id
    front = [item.key for item in stack if item.kind == "balloon"]
    _reorder_collection(page.balloons, list(reversed(front)), lambda entry: entry.id)
    if active_balloon:
        _restore_active_collection_index(page, "active_balloon_index", page.balloons, active_balloon)

    active_text = ""
    if 0 <= page.active_text_index < len(page.texts):
        active_text = page.texts[page.active_text_index].id
    front = [item.key for item in stack if item.kind == "text"]
    _reorder_collection(page.texts, list(reversed(front)), lambda entry: entry.id)
    if active_text:
        _restore_active_collection_index(page, "active_text_index", page.texts, active_text)


def _node_uid_for_stack(node, *, effect: bool) -> str:
    if effect:
        return target_uid("effect", _node_stack_key(node))
    if gp_utils.is_layer_group(node):
        return target_uid("gp_folder", _node_stack_key(node))
    return target_uid("gp", _node_stack_key(node))


def _siblings_for_parent(gp_data, parent_key: str):
    if not parent_key:
        return list(getattr(gp_data, "root_nodes", []))
    groups = getattr(gp_data, "layer_groups", None)
    group = _find_gp_group_by_key(groups, parent_key)
    if group is None:
        return []
    return list(getattr(group, "children", []))


def _move_gp_node(gp_data, node, direction: str) -> None:
    try:
        if gp_utils.is_layer_group(node):
            gp_data.layer_groups.move(node, direction)
        else:
            gp_data.layers.move(node, direction)
    except Exception:  # noqa: BLE001
        _logger.exception("gp node move failed: %s %s", getattr(node, "name", ""), direction)


def _reorder_gp_parent(gp_data, parent_key: str, desired_front_uids: list[str], *, effect: bool):
    siblings = _siblings_for_parent(gp_data, parent_key)
    actual = [_node_uid_for_stack(node, effect=effect) for node in siblings]
    desired_back = [uid for uid in reversed(desired_front_uids) if uid in actual]
    desired_back.extend(uid for uid in actual if uid not in desired_back)
    for target_index, uid in enumerate(desired_back):
        guard = 0
        while guard < 128:
            siblings = _siblings_for_parent(gp_data, parent_key)
            current_index = next(
                (i for i, node in enumerate(siblings)
                 if _node_uid_for_stack(node, effect=effect) == uid),
                -1,
            )
            if current_index < 0 or current_index == target_index:
                break
            node = siblings[current_index]
            _move_gp_node(gp_data, node, "DOWN" if current_index > target_index else "UP")
            guard += 1


def _apply_gp_order(obj, stack, *, effect: bool) -> None:
    gp_data = getattr(obj, "data", None)
    if gp_data is None:
        return
    by_parent: dict[str, list[str]] = {}
    for item in stack:
        if effect:
            if item.kind != "effect":
                continue
        elif item.kind not in {"gp", "gp_folder"}:
            continue
        by_parent.setdefault(getattr(item, "parent_key", ""), []).append(stack_item_uid(item))
    for parent_key, uids in by_parent.items():
        _reorder_gp_parent(gp_data, parent_key, uids, effect=effect)


def apply_stack_order(context) -> None:
    stack = getattr(context.scene, "bname_layer_stack", None)
    if stack is None:
        return
    _apply_simple_collection_orders(context, stack)
    gp_obj = gp_utils.get_master_gpencil()
    if gp_obj is not None:
        _apply_gp_order(gp_obj, stack, effect=False)
    effect_obj = get_effect_gp_object()
    if effect_obj is not None:
        _apply_gp_order(effect_obj, stack, effect=True)
    tag_view3d_redraw(context)


def delete_stack_index(context, index: int) -> bool:
    stack = sync_layer_stack(context, preserve_active_index=True)
    if stack is None or not (0 <= index < len(stack)):
        return False
    item = stack[index]
    resolved = resolve_stack_item(context, item)
    if resolved is None or resolved.get("target") is None:
        stack.remove(index)
        return True
    kind = item.kind
    scene = context.scene
    page = get_active_page(context)

    if kind == "gp":
        obj = resolved.get("object")
        try:
            obj.data.layers.remove(resolved["target"])
        except Exception:  # noqa: BLE001
            return False
    elif kind == "gp_folder":
        obj = resolved.get("object")
        if not gp_utils.remove_layer_group_preserve_children(obj.data, resolved["target"]):
            return False
        scene.bname_active_gp_folder_key = ""
    elif kind == "image":
        coll = getattr(scene, "bname_image_layers", None)
        idx = int(resolved.get("index", -1))
        if coll is None or not (0 <= idx < len(coll)):
            return False
        coll.remove(idx)
        scene.bname_active_image_layer_index = min(idx, len(coll) - 1) if len(coll) else -1
    elif kind == "balloon" and page is not None:
        idx = int(resolved.get("index", -1))
        if not (0 <= idx < len(page.balloons)):
            return False
        bid = page.balloons[idx].id
        for text in page.texts:
            if text.parent_balloon_id == bid:
                text.parent_balloon_id = ""
        page.balloons.remove(idx)
        page.active_balloon_index = min(idx, len(page.balloons) - 1) if len(page.balloons) else -1
    elif kind == "text" and page is not None:
        idx = int(resolved.get("index", -1))
        if not (0 <= idx < len(page.texts)):
            return False
        page.texts.remove(idx)
        page.active_text_index = min(idx, len(page.texts) - 1) if len(page.texts) else -1
    elif kind == "effect":
        obj = resolved.get("object")
        try:
            obj.data.layers.remove(resolved["target"])
        except Exception:  # noqa: BLE001
            return False
        scene.bname_active_effect_layer_name = ""
    else:
        return False

    sync_layer_stack(context)
    idx = min(index, len(stack) - 1) if len(stack) else -1
    scene.bname_active_layer_stack_index = idx
    if idx >= 0:
        select_stack_index(context, idx)
    elif hasattr(scene, "bname_active_layer_kind"):
        scene.bname_active_layer_kind = "gp"
        scene.bname_active_gp_folder_key = ""
    tag_view3d_redraw(context)
    return True


def tag_view3d_redraw(context) -> None:
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()
