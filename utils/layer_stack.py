"""統合レイヤースタックの同期・選択・並び替えヘルパ."""

from __future__ import annotations

from dataclasses import dataclass

import bpy

from ..core.work import get_active_page, get_work
from . import gp_layer_parenting as gp_parent
from . import gpencil as gp_utils
from . import log
from .layer_hierarchy import (
    PAGE_KIND,
    PANEL_KIND,
    entry_center,
    page_stack_key,
    panel_containing_point,
    panel_stack_key,
    split_child_key,
)

_logger = log.get_logger(__name__)

EFFECT_GP_OBJECT_NAME = "BName_EffectLines"
_sync_scheduled = False
_sync_should_apply_order = False
_sync_order_moved_uid = ""
_draw_stack_signatures: dict[int, tuple[str, ...]] = {}


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


def set_active_stack_index_silently(context, index: int) -> None:
    """実データ選択を再実行せず、UIList のアクティブ行だけを合わせる。"""
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "bname_active_layer_stack_index"):
        return
    core_layer_stack = None
    try:
        from ..core import layer_stack as core_layer_stack

        core_layer_stack._active_index_update_depth += 1
    except Exception:  # noqa: BLE001
        core_layer_stack = None
    try:
        scene.bname_active_layer_stack_index = int(index)
    finally:
        if core_layer_stack is not None:
            core_layer_stack._active_index_update_depth = max(
                0,
                core_layer_stack._active_index_update_depth - 1,
            )


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
            logical_parent_key = gp_parent.parent_key(layer)
            if logical_parent_key:
                yield LayerTarget(
                    kind,
                    key,
                    layer.name,
                    logical_parent_key,
                    gp_parent.parent_depth(logical_parent_key),
                )
            else:
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
            logical_parent_key = gp_parent.parent_key(node)
            if logical_parent_key:
                yield LayerTarget(
                    kind,
                    key,
                    node.name,
                    logical_parent_key,
                    gp_parent.parent_depth(logical_parent_key),
                )
            else:
                yield LayerTarget(kind, key, node.name, parent_key, depth)


def _collect_page_layer_targets(
    page,
    panels_by_key: dict[str, object],
    *,
    include_page_children: bool = True,
) -> list[LayerTarget]:
    targets: list[LayerTarget] = []
    used_text: set[str] = set()
    used_balloon: set[str] = set()
    page_key = page_stack_key(page)
    panel_children: dict[str, list[LayerTarget]] = {}
    page_children: list[LayerTarget] = []
    balloon_groups: dict[str, LayerTarget] = {}
    balloon_group_children: dict[str, list[LayerTarget]] = {}
    balloon_group_parents: dict[str, set[str]] = {}

    for entry in reversed(list(getattr(page, "balloons", []))):
        bid = _ensure_unique_id(entry, used_balloon, "balloon")
        panel = panel_containing_point(page, *entry_center(entry))
        if panel is not None:
            parent = panel_stack_key(page, panel)
            depth = 2
        else:
            parent = page_key
            depth = 1
        group_id = str(getattr(entry, "merge_group_id", "") or "")
        if group_id:
            group_key = f"{page_key}:{group_id}"
            balloon_group_parents.setdefault(group_key, set()).add(parent)
            if group_key not in balloon_groups:
                label = group_id.replace("balloon_group_", "フキダシ結合 ")
                balloon_groups[group_key] = LayerTarget(
                    "balloon_group", group_key, label, parent, depth
                )
            target = LayerTarget("balloon", f"{page_key}:{bid}", bid, group_key, depth + 1)
            balloon_group_children.setdefault(group_key, []).append(target)
            continue
        target = LayerTarget("balloon", f"{page_key}:{bid}", bid, parent, depth)
        if depth == 2:
            panel_children.setdefault(parent, []).append(target)
        else:
            page_children.append(target)

    for group_key, group_target in balloon_groups.items():
        if len(balloon_group_parents.get(group_key, set())) > 1:
            group_target = LayerTarget(
                "balloon_group",
                group_key,
                group_target.label,
                page_key,
                1,
            )
            children = [
                LayerTarget(child.kind, child.key, child.label, group_key, 2)
                for child in balloon_group_children.get(group_key, [])
            ]
        else:
            children = balloon_group_children.get(group_key, [])
        if group_target.depth == 2:
            panel_children.setdefault(group_target.parent_key, []).append(group_target)
            panel_children[group_target.parent_key].extend(children)
        else:
            page_children.append(group_target)
            page_children.extend(children)

    for entry in reversed(list(getattr(page, "texts", []))):
        tid = _ensure_unique_id(entry, used_text, "text")
        label = getattr(entry, "body", "") or tid
        center = entry_center(entry)
        panel = panel_containing_point(page, *center)
        if panel is not None:
            parent = panel_stack_key(page, panel)
            depth = 2
        else:
            parent = page_key
            depth = 1
        target = LayerTarget("text", f"{page_key}:{tid}", label, parent, depth)
        if depth == 2:
            panel_children.setdefault(parent, []).append(target)
        else:
            page_children.append(target)

    for panel_key in panels_by_key:
        targets.extend(panel_children.get(panel_key, []))
    if include_page_children:
        targets.extend(page_children)
    return targets


def _partition_gp_targets(obj, work) -> tuple[list[LayerTarget], dict[str, list[LayerTarget]]]:
    root_targets: list[LayerTarget] = []
    targets_by_parent: dict[str, list[LayerTarget]] = {}
    if obj is None:
        return root_targets, targets_by_parent
    for target in _iter_gp_targets(obj, kind="gp"):
        if target.kind == "gp" and gp_parent.parent_key_exists(work, target.parent_key):
            targets_by_parent.setdefault(target.parent_key, []).append(target)
        else:
            if target.kind == "gp" and target.parent_key:
                root_targets.append(LayerTarget(target.kind, target.key, target.label))
            else:
                root_targets.append(target)
    return root_targets, targets_by_parent


def collect_targets(context) -> list[LayerTarget]:
    """現在の作品/シーンから、前面→背面の統合レイヤー候補を返す."""
    scene = context.scene
    work = get_work(context)
    targets: list[LayerTarget] = []
    gp_obj = gp_utils.get_master_gpencil()
    gp_root_targets, gp_targets_by_parent = _partition_gp_targets(gp_obj, work)

    if work is not None and getattr(work, "loaded", False):
        from . import page_range

        for page in work.pages:
            if not page_range.page_in_range(page):
                continue
            page_key = page_stack_key(page)
            label = getattr(page, "title", "") or page_key
            targets.append(LayerTarget(PAGE_KIND, page_key, label))
            if not bool(getattr(page, "stack_expanded", True)):
                continue
            panels = sorted(
                list(getattr(page, "panels", [])),
                key=lambda p: int(getattr(p, "z_order", 0)),
                reverse=True,
            )
            panels_by_key: dict[str, object] = {}
            for panel in panels:
                key = panel_stack_key(page, panel)
                panels_by_key[key] = panel
                panel_label = getattr(panel, "title", "") or getattr(panel, "panel_stem", "") or key
                targets.append(LayerTarget(PANEL_KIND, key, panel_label, page_key, 1))
                targets.extend(gp_targets_by_parent.get(key, []))
                targets.extend(
                    _collect_page_layer_targets(
                        page, {key: panel}, include_page_children=False
                    )
                )
            # コマ外のページ直下レイヤーは、すべてのコマ行の後にまとめて表示する。
            all_page_layers = _collect_page_layer_targets(page, panels_by_key)
            visible_parent_keys = {page_key}
            for target in gp_targets_by_parent.get(page_key, []):
                targets.append(target)
                visible_parent_keys.add(target.key)
            for target in all_page_layers:
                if target.parent_key in visible_parent_keys:
                    targets.append(target)
                    visible_parent_keys.add(target.key)

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

    targets.extend(gp_root_targets)

    return targets


def _set_item_from_target(item, target: LayerTarget) -> None:
    item.kind = target.kind
    item.name = target.label
    item.key = target.key
    item.label = target.label
    item.parent_key = target.parent_key
    item.depth = target.depth


def _find_insert_index_for_target(stack, target: LayerTarget) -> int:
    if target.parent_key:
        parent_idx = -1
        last_child_idx = -1
        for i, item in enumerate(stack):
            if item.key == target.parent_key and item.kind in {PAGE_KIND, PANEL_KIND, "gp_folder", "balloon_group"}:
                parent_idx = i
                last_child_idx = max(last_child_idx, i)
            elif getattr(item, "parent_key", "") == target.parent_key:
                last_child_idx = i
        if last_child_idx >= 0:
            return last_child_idx + 1
        if parent_idx >= 0:
            return parent_idx + 1
    if target.kind == PAGE_KIND:
        last_page = -1
        for i, item in enumerate(stack):
            if item.kind == PAGE_KIND:
                last_page = i
        return last_page + 1
    return len(stack)


def _add_target_to_stack(stack, target: LayerTarget) -> None:
    item = stack.add()
    _set_item_from_target(item, target)
    from_index = len(stack) - 1
    to_index = max(0, min(_find_insert_index_for_target(stack, target), from_index))
    if to_index != from_index:
        stack.move(from_index, to_index)


def _ordered_items_by_uid(items, uid_order: list[str] | None):
    if uid_order is None:
        return items
    ordered = []
    used_uids: set[str] = set()
    for uid in uid_order:
        for item in items:
            if stack_item_uid(item) == uid and uid not in used_uids:
                ordered.append(item)
                used_uids.add(uid)
                break
    ordered.extend(item for item in items if stack_item_uid(item) not in used_uids)
    return ordered


def _normalize_tree_order(
    stack,
    page_key_order: list[str] | None = None,
    panel_key_order_by_page: dict[str, list[str]] | None = None,
) -> None:
    """ページ/コマを常にツリー構造へ戻す。

    UIList のD&Dは親子制約を知らないため、ページが子階層へ落ちたり
    コマが別コマ配下へ落ちたように見える並びを、次の描画で正規化する。
    """
    current = [stack_item_uid(item) for item in stack]
    desired: list[str] = []
    used: set[str] = set()

    def _append_uid(item) -> None:
        uid = stack_item_uid(item)
        if uid in used:
            return
        desired.append(uid)
        used.add(uid)

    def _append_gp_subtree(parent_key: str) -> None:
        for child in stack:
            if getattr(child, "parent_key", "") != parent_key:
                continue
            _append_uid(child)
            if child.kind == "gp_folder":
                _append_gp_subtree(child.key)

    page_items = [item for item in stack if item.kind == PAGE_KIND]
    if page_key_order is not None:
        page_uid_order = [target_uid(PAGE_KIND, key) for key in page_key_order]
        page_items = _ordered_items_by_uid(page_items, page_uid_order)

    def _append_page_subtree(page_item) -> None:
        _append_uid(page_item)
        page_key = page_item.key
        panel_items = [
            item
            for item in stack
            if item.kind == PANEL_KIND and split_child_key(item.key)[0] == page_key
        ]
        panel_uid_order = None
        if panel_key_order_by_page is not None:
            panel_uid_order = [
                target_uid(PANEL_KIND, key)
                for key in panel_key_order_by_page.get(page_key, [])
            ]
            panel_items = _ordered_items_by_uid(panel_items, panel_uid_order)
        for panel_item in panel_items:
            _append_uid(panel_item)
            for child in stack:
                if getattr(child, "parent_key", "") == panel_item.key:
                    _append_uid(child)
                    if child.kind == "balloon_group":
                        for group_child in stack:
                            if getattr(group_child, "parent_key", "") == child.key:
                                _append_uid(group_child)
        for child in stack:
            if getattr(child, "parent_key", "") == page_key and child.kind != PANEL_KIND:
                _append_uid(child)
                if child.kind == "balloon_group":
                    for group_child in stack:
                        if getattr(group_child, "parent_key", "") == child.key:
                            _append_uid(group_child)

    if page_key_order is not None:
        for page_item in page_items:
            _append_page_subtree(page_item)

    for item in stack:
        if stack_item_uid(item) in used:
            continue
        if item.kind == PAGE_KIND:
            _append_page_subtree(item)
        elif not getattr(item, "parent_key", ""):
            _append_uid(item)
            if item.kind == "gp_folder":
                _append_gp_subtree(item.key)

    desired.extend(uid for uid in current if uid not in used)
    for target_index, uid in enumerate(desired):
        current_index = next((i for i, item in enumerate(stack) if stack_item_uid(item) == uid), -1)
        if current_index >= 0 and current_index != target_index:
            stack.move(current_index, target_index)


def sync_layer_stack(
    context,
    *,
    preserve_active_index: bool = False,
    align_page_order: bool = False,
    align_panel_order: bool = False,
):
    """統合レイヤーリストを実データに同期する。

    既存行の並びは維持し、消えた実体だけを削除、新規実体だけを前面側へ
    追加する。これにより UIList 側のD&D並び替えを上書きしない。
    """
    scene = context.scene
    stack = getattr(scene, "bname_layer_stack", None)
    if stack is None:
        return None
    old_active_index = int(getattr(scene, "bname_active_layer_stack_index", -1))
    old_active_uid = ""
    if 0 <= old_active_index < len(stack):
        old_active_uid = stack_item_uid(stack[old_active_index])

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
    for target in missing:
        _add_target_to_stack(stack, target)
    page_key_order = None
    if align_page_order:
        work = get_work(context)
        if work is not None and getattr(work, "loaded", False):
            page_key_order = [page_stack_key(page) for page in work.pages]
    panel_key_order_by_page = None
    if align_panel_order:
        panel_key_order_by_page = {}
        for target in targets:
            if target.kind != PANEL_KIND:
                continue
            page_key, _stem = split_child_key(target.key)
            panel_key_order_by_page.setdefault(page_key, []).append(target.key)
    _normalize_tree_order(stack, page_key_order, panel_key_order_by_page)

    if preserve_active_index and old_active_uid:
        for i, item in enumerate(stack):
            if stack_item_uid(item) == old_active_uid:
                set_active_stack_index_silently(context, i)
                break
        else:
            _sync_active_stack_index(context)
    elif preserve_active_index and 0 <= old_active_index < len(stack):
        set_active_stack_index_silently(context, old_active_index)
    else:
        _sync_active_stack_index(context)
    return stack


def _stack_signature(scene) -> tuple[str, ...]:
    stack = getattr(scene, "bname_layer_stack", None)
    if stack is None:
        return ()
    return tuple(stack_item_uid(item) for item in stack)


def _remember_stack_signature(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    try:
        scene_key = int(scene.as_pointer())
    except Exception:  # noqa: BLE001
        scene_key = id(scene)
    _draw_stack_signatures[scene_key] = _stack_signature(scene)


def remember_layer_stack_signature(context) -> None:
    """現在の UIList 並びを既知状態として記録する。Operator からの同期後に使う."""
    _remember_stack_signature(context)


def _find_stack_index_by_uid(stack, uid: str) -> int:
    if stack is None or not uid:
        return -1
    for i, item in enumerate(stack):
        if stack_item_uid(item) == uid:
            return i
    return -1


def _find_stack_item(stack, kind: str, key: str):
    for item in stack or []:
        if getattr(item, "kind", "") == kind and getattr(item, "key", "") == key:
            return item
    return None


def _same_move_scope(a, b) -> bool:
    """右側の順序ボタンで同じ移動単位として扱える行かを返す."""
    a_kind = getattr(a, "kind", "")
    b_kind = getattr(b, "kind", "")
    if a_kind == PAGE_KIND or b_kind == PAGE_KIND:
        return a_kind == b_kind == PAGE_KIND
    if a_kind == PANEL_KIND or b_kind == PANEL_KIND:
        return (
            a_kind == b_kind == PANEL_KIND
            and split_child_key(getattr(a, "key", ""))[0]
            == split_child_key(getattr(b, "key", ""))[0]
        )
    if a_kind in {"gp", "gp_folder"} or b_kind in {"gp", "gp_folder"}:
        return (
            a_kind in {"gp", "gp_folder"}
            and b_kind in {"gp", "gp_folder"}
            and str(getattr(a, "parent_key", "") or "")
            == str(getattr(b, "parent_key", "") or "")
        )
    return (
        a_kind == b_kind
        and str(getattr(a, "parent_key", "") or "")
        == str(getattr(b, "parent_key", "") or "")
    )


def _move_scope_indices(stack, item) -> list[int]:
    return [i for i, candidate in enumerate(stack) if _same_move_scope(item, candidate)]


def _direction_from_target_index(from_index: int, to_index: int, stack_len: int) -> str:
    if to_index <= 0:
        return "FRONT"
    if to_index >= stack_len - 1:
        return "BACK"
    return "UP" if to_index < from_index else "DOWN"


def _target_index_for_stack_move(
    stack,
    from_index: int,
    to_index: int | None = None,
    direction: str = "",
) -> int:
    if stack is None or not (0 <= from_index < len(stack)):
        return -1
    if not direction:
        if to_index is None:
            return -1
        direction = _direction_from_target_index(from_index, int(to_index), len(stack))
    direction = str(direction or "").upper()
    siblings = _move_scope_indices(stack, stack[from_index])
    if from_index not in siblings:
        return -1
    pos = siblings.index(from_index)
    if direction == "FRONT":
        target_pos = 0
    elif direction == "BACK":
        target_pos = len(siblings) - 1
    elif direction == "UP":
        target_pos = pos - 1
    elif direction == "DOWN":
        target_pos = pos + 1
    else:
        return -1
    if not (0 <= target_pos < len(siblings)):
        return -1
    return siblings[target_pos]


def _gp_parent_key_from_flat_drop(stack, moved_index: int) -> str:
    """UIList の平坦D&D位置から GP レイヤーの親を推定する."""
    if stack is None or moved_index <= 0:
        return ""
    previous = stack[moved_index - 1]
    previous_kind = getattr(previous, "kind", "")
    if previous_kind in {PAGE_KIND, PANEL_KIND}:
        return str(getattr(previous, "key", "") or "")
    if previous_kind == "gp_folder":
        return str(getattr(previous, "key", "") or "")
    if previous_kind == "gp":
        return str(getattr(previous, "parent_key", "") or "")
    previous_parent_key = str(getattr(previous, "parent_key", "") or "")
    if previous_parent_key:
        return previous_parent_key
    return ""


def _is_stack_folder_descendant(stack, ancestor_key: str, candidate_key: str) -> bool:
    key = candidate_key
    guard = 0
    while key and guard < 128:
        if key == ancestor_key:
            return True
        folder = _find_stack_item(stack, "gp_folder", key)
        if folder is None:
            return False
        key = str(getattr(folder, "parent_key", "") or "")
        guard += 1
    return False


def _apply_gp_folder_drop_hint(context, moved_uid: str) -> bool:
    """GP レイヤー/フォルダを別階層へ落としたUIList D&Dを親変更へ変換する."""
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bname_layer_stack", None) if scene is not None else None
    moved_index = _find_stack_index_by_uid(stack, moved_uid)
    if moved_index < 0:
        return False
    item = stack[moved_index]
    if getattr(item, "kind", "") not in {"gp", "gp_folder"}:
        return False
    parent_key = _gp_parent_key_from_flat_drop(stack, moved_index)
    old_parent_key = str(getattr(item, "parent_key", "") or "")
    kind = getattr(item, "kind", "")
    work = get_work(context)
    parent_is_page_panel = parent_key and gp_parent.parent_key_exists(work, parent_key)
    parent_is_gp_folder = bool(_find_stack_item(stack, "gp_folder", parent_key)) if parent_key else False
    if parent_key and not parent_is_page_panel and not parent_is_gp_folder:
        return False
    if kind == "gp_folder":
        if parent_is_page_panel:
            return False
        item_key = str(getattr(item, "key", "") or "")
        if parent_key == item_key or _is_stack_folder_descendant(stack, item_key, parent_key):
            return False
    if parent_key == old_parent_key:
        return False
    item.parent_key = parent_key
    parent = None
    if parent_key:
        parent = (
            _find_stack_item(stack, "gp_folder", parent_key)
            or _find_stack_item(stack, PAGE_KIND, parent_key)
            or _find_stack_item(stack, PANEL_KIND, parent_key)
        )
    item.depth = int(getattr(parent, "depth", -1)) + 1 if parent is not None else 0
    obj = gp_utils.get_master_gpencil()
    groups = getattr(getattr(obj, "data", None), "layer_groups", None) if obj is not None else None
    group = _find_gp_group_by_key(groups, parent_key) if parent_key else None
    if group is not None and hasattr(group, "is_expanded"):
        try:
            group.is_expanded = True
        except Exception:  # noqa: BLE001
            pass
    return True


def _infer_moved_uid(previous: tuple[str, ...], current: tuple[str, ...]) -> str:
    if len(previous) != len(current) or previous == current:
        return ""
    first = -1
    last = -1
    for i, (old_uid, new_uid) in enumerate(zip(previous, current)):
        if old_uid != new_uid:
            if first < 0:
                first = i
            last = i
    if first < 0 or last < 0:
        return ""
    if previous[first] == current[last]:
        return previous[first]
    if previous[last] == current[first]:
        return previous[last]
    return ""


def _active_uid_from_signature(scene, signature: tuple[str, ...]) -> str:
    idx = int(getattr(scene, "bname_active_layer_stack_index", -1))
    if 0 <= idx < len(signature):
        return signature[idx]
    return ""


def apply_stack_order_if_ui_changed(context, *, moved_uid: str = "") -> bool:
    """UIList の D&D で変わった Collection 順を、同期で戻る前に実データへ適用する."""
    scene = getattr(context, "scene", None)
    if scene is None or getattr(scene, "bname_layer_stack", None) is None:
        return False
    try:
        scene_key = int(scene.as_pointer())
    except Exception:  # noqa: BLE001
        scene_key = id(scene)
    signature = _stack_signature(scene)
    previous = _draw_stack_signatures.get(scene_key)
    if previous is None:
        _remember_stack_signature(context)
        return False
    if previous == signature:
        return False
    if set(previous) != set(signature):
        _remember_stack_signature(context)
        return False
    if not moved_uid:
        moved_uid = _active_uid_from_signature(scene, signature)
    if not moved_uid:
        moved_uid = _infer_moved_uid(previous, signature)
    _apply_gp_folder_drop_hint(context, moved_uid)
    apply_stack_order(context)
    _remember_stack_signature(context)
    return True


def sync_layer_stack_after_data_change(
    context,
    *,
    align_page_order: bool = False,
    align_panel_order: bool = False,
) -> None:
    """Operator で実データを更新した直後に、UIList と既知シグネチャを揃える."""
    try:
        sync_layer_stack(
            context,
            align_page_order=align_page_order,
            align_panel_order=align_panel_order,
        )
        _remember_stack_signature(context)
        tag_view3d_redraw(context)
    except Exception:  # noqa: BLE001
        _logger.exception("layer stack sync after data change failed")


def schedule_layer_stack_draw_maintenance(context) -> None:
    """Panel.draw 中に Scene を書き換えず、必要な同期だけをタイマー予約する."""
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    stack = getattr(scene, "bname_layer_stack", None)
    if stack is None:
        return
    try:
        scene_key = int(scene.as_pointer())
    except Exception:  # noqa: BLE001
        scene_key = id(scene)
    signature = _stack_signature(scene)
    previous = _draw_stack_signatures.get(scene_key)
    if previous is None:
        _draw_stack_signatures[scene_key] = signature
        if not signature:
            schedule_layer_stack_sync()
        return
    if previous != signature:
        _draw_stack_signatures[scene_key] = signature
        apply_order = set(previous) == set(signature)
        moved_uid = ""
        if apply_order:
            moved_uid = _active_uid_from_signature(scene, signature)
            if not moved_uid:
                moved_uid = _infer_moved_uid(previous, signature)
        schedule_layer_stack_sync(apply_order=apply_order, moved_uid=moved_uid)
    elif not signature:
        schedule_layer_stack_sync()


def _active_key_from_scene(context) -> tuple[str, str] | None:
    scene = context.scene
    kind = getattr(scene, "bname_active_layer_kind", "gp")
    work = get_work(context)
    page = get_active_page(context)

    if kind == PAGE_KIND and work is not None:
        idx = int(getattr(work, "active_page_index", -1))
        if 0 <= idx < len(work.pages):
            return PAGE_KIND, page_stack_key(work.pages[idx])
    if kind == PANEL_KIND and page is not None:
        idx = int(getattr(page, "active_panel_index", -1))
        if 0 <= idx < len(page.panels):
            return PANEL_KIND, panel_stack_key(page, page.panels[idx])
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
            return "balloon", f"{page_stack_key(page)}:{getattr(page.balloons[idx], 'id', '')}"
    if kind == "text" and page is not None:
        idx = int(getattr(page, "active_text_index", -1))
        if 0 <= idx < len(page.texts):
            return "text", f"{page_stack_key(page)}:{getattr(page.texts[idx], 'id', '')}"
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
                set_active_stack_index_silently(context, i)
                return
    idx = int(getattr(scene, "bname_active_layer_stack_index", -1))
    if idx >= len(stack):
        set_active_stack_index_silently(context, len(stack) - 1)
    elif idx < -1:
        set_active_stack_index_silently(context, -1)


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


def gp_parent_keys_for_page(page) -> set[str]:
    return gp_parent.parent_keys_for_page(page)


def gp_parent_key_for_panel(page, panel) -> str:
    return gp_parent.parent_key_for_panel(page, panel)


def gp_layers_for_parent_keys(context, parent_keys: set[str]) -> list[object]:
    _ = context
    obj = gp_utils.get_master_gpencil()
    if obj is None or not parent_keys:
        return []
    return list(gp_parent.iter_layers_with_parent(obj, set(parent_keys)))


def delete_gp_layers_for_parent_keys(context, parent_keys: set[str]) -> int:
    obj = gp_utils.get_master_gpencil()
    layers = getattr(getattr(obj, "data", None), "layers", None) if obj is not None else None
    if layers is None or not parent_keys:
        return 0
    removed = 0
    for layer in list(gp_parent.iter_layers_with_parent(obj, set(parent_keys))):
        try:
            gp_parent.set_parent_key(layer, "")
            layers.remove(layer)
            removed += 1
        except Exception:  # noqa: BLE001
            _logger.exception("delete gp layer for parent failed: %s", getattr(layer, "name", ""))
    if removed:
        tag_view3d_redraw(context)
    return removed


def reparent_gp_layers(context, old_parent_key: str, new_parent_key: str) -> int:
    work = get_work(context)
    if not old_parent_key or not gp_parent.parent_key_exists(work, new_parent_key):
        return 0
    obj = gp_utils.get_master_gpencil()
    if obj is None:
        return 0
    changed = 0
    for layer in list(gp_parent.iter_layers_with_parent(obj, {old_parent_key})):
        gp_parent.set_parent_key(layer, new_parent_key)
        changed += 1
    if changed:
        tag_view3d_redraw(context)
    return changed


def translate_gp_layers_for_parent_keys(context, parent_keys: set[str], dx_mm: float, dy_mm: float) -> int:
    moved = 0
    for layer in gp_layers_for_parent_keys(context, parent_keys):
        gp_parent.translate_layer(layer, dx_mm, dy_mm)
        moved += 1
    if moved:
        tag_view3d_redraw(context)
    return moved


def capture_gp_layers_for_parent_keys(context, parent_keys: set[str]):
    return gp_parent.capture_layers(gp_layers_for_parent_keys(context, parent_keys))


def restore_gp_layer_snapshots(snapshot) -> None:
    gp_parent.restore_layers(snapshot)


def resolve_stack_item(context, item):
    """スタック行が参照する実体を辞書で返す。見つからなければ None."""
    if item is None:
        return None
    kind = getattr(item, "kind", "")
    key = getattr(item, "key", "")
    scene = context.scene
    work = get_work(context)
    page = get_active_page(context)

    if kind == PAGE_KIND:
        if work is None:
            return None
        idx, entry = _find_by_id(work.pages, key)
        return {"kind": kind, "target": entry, "object": None, "index": idx}
    if kind == PANEL_KIND:
        if work is None:
            return None
        page_id, stem = split_child_key(key)
        page_idx, target_page = _find_by_id(work.pages, page_id)
        if target_page is None:
            return None
        for panel_idx, panel in enumerate(target_page.panels):
            if getattr(panel, "panel_stem", "") == stem or getattr(panel, "id", "") == stem:
                return {
                    "kind": kind,
                    "target": panel,
                    "object": None,
                    "index": panel_idx,
                    "page": target_page,
                    "page_index": page_idx,
                }
        return {"kind": kind, "target": None, "object": None, "index": -1,
                "page": target_page, "page_index": page_idx}
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
    if kind == "balloon":
        page_id, child_id = split_child_key(key)
        target_page = page
        page_idx = int(getattr(work, "active_page_index", -1)) if work is not None else -1
        if page_id and work is not None:
            page_idx, target_page = _find_by_id(work.pages, page_id)
        if target_page is None:
            return None
        idx, entry = _find_by_id(target_page.balloons, child_id or key)
        return {
            "kind": kind,
            "target": entry,
            "object": None,
            "index": idx,
            "page": target_page,
            "page_index": page_idx,
        }
    if kind == "balloon_group":
        page_id, group_id = split_child_key(key)
        target_page = page
        page_idx = int(getattr(work, "active_page_index", -1)) if work is not None else -1
        if page_id and work is not None:
            page_idx, target_page = _find_by_id(work.pages, page_id)
        if target_page is None:
            return None
        return {
            "kind": kind,
            "target": target_page,
            "object": None,
            "index": -1,
            "page": target_page,
            "page_index": page_idx,
            "group_id": group_id,
        }
    if kind == "text":
        page_id, child_id = split_child_key(key)
        target_page = page
        page_idx = int(getattr(work, "active_page_index", -1)) if work is not None else -1
        if page_id and work is not None:
            page_idx, target_page = _find_by_id(work.pages, page_id)
        if target_page is None:
            return None
        idx, entry = _find_by_id(target_page.texts, child_id or key)
        return {
            "kind": kind,
            "target": entry,
            "object": None,
            "index": idx,
            "page": target_page,
            "page_index": page_idx,
        }
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
    if kind == PAGE_KIND:
        work = get_work(context)
        idx = int(resolved.get("index", -1))
        if work is None or not (0 <= idx < len(work.pages)):
            return False
        work.active_page_index = idx
        try:
            from ..core.mode import MODE_PAGE, set_mode

            set_mode(MODE_PAGE, context)
            scene.bname_overview_mode = True
            scene.bname_current_panel_stem = ""
            scene.bname_current_panel_page_id = ""
        except Exception:  # noqa: BLE001
            pass
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = PAGE_KIND
    elif kind == PANEL_KIND:
        work = get_work(context)
        page_idx = int(resolved.get("page_index", -1))
        panel_idx = int(resolved.get("index", -1))
        target_page = resolved.get("page")
        if (
            work is None
            or target_page is None
            or not (0 <= page_idx < len(work.pages))
            or not (0 <= panel_idx < len(target_page.panels))
        ):
            return False
        work.active_page_index = page_idx
        target_page.active_panel_index = panel_idx
        try:
            from ..core.mode import MODE_PAGE, set_mode

            set_mode(MODE_PAGE, context)
            scene.bname_overview_mode = True
        except Exception:  # noqa: BLE001
            pass
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = PANEL_KIND
    elif kind == "gp":
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
    elif kind == "balloon_group":
        target_page = resolved.get("page") or page
        if target_page is None:
            return False
        work = get_work(context)
        page_idx = int(resolved.get("page_index", -1))
        if work is not None and 0 <= page_idx < len(work.pages):
            work.active_page_index = page_idx
        group_id = str(resolved.get("group_id", "") or "")
        first_selected = -1
        for i, balloon in enumerate(getattr(target_page, "balloons", [])):
            selected = str(getattr(balloon, "merge_group_id", "") or "") == group_id
            try:
                balloon.selected = selected
            except Exception:  # noqa: BLE001
                pass
            if selected and first_selected < 0:
                first_selected = i
        if first_selected >= 0:
            target_page.active_balloon_index = first_selected
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = "balloon"
    elif kind == "balloon":
        target_page = resolved.get("page") or page
        if target_page is None:
            return False
        work = get_work(context)
        page_idx = int(resolved.get("page_index", -1))
        if work is not None and 0 <= page_idx < len(work.pages):
            work.active_page_index = page_idx
        target_page.active_balloon_index = int(resolved.get("index", -1))
        for i, balloon in enumerate(getattr(target_page, "balloons", [])):
            try:
                balloon.selected = i == target_page.active_balloon_index
            except Exception:  # noqa: BLE001
                pass
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = "balloon"
    elif kind == "text":
        target_page = resolved.get("page") or page
        if target_page is None:
            return False
        work = get_work(context)
        page_idx = int(resolved.get("page_index", -1))
        if work is not None and 0 <= page_idx < len(work.pages):
            work.active_page_index = page_idx
        target_page.active_text_index = int(resolved.get("index", -1))
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


def move_stack_item(
    context,
    from_index: int,
    to_index: int | None = None,
    *,
    direction: str = "",
) -> bool:
    stack = sync_layer_stack(context, preserve_active_index=True)
    if stack is None or len(stack) == 0:
        return False
    if not (0 <= from_index < len(stack)):
        return False
    target_index = _target_index_for_stack_move(stack, from_index, to_index, direction)
    if target_index < 0 or from_index == target_index:
        return False
    moved_uid = stack_item_uid(stack[from_index])
    stack.move(from_index, target_index)
    moved_index = _find_stack_index_by_uid(stack, moved_uid)
    if moved_index >= 0:
        context.scene.bname_active_layer_stack_index = moved_index
    apply_stack_order(context)
    sync_layer_stack(context, preserve_active_index=True)
    for i, item in enumerate(context.scene.bname_layer_stack):
        if stack_item_uid(item) == moved_uid:
            select_stack_index(context, i)
            break
    remember_layer_stack_signature(context)
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


def _restore_active_page_panel(work, active_page_key: str, active_panel_key: str) -> None:
    if work is None:
        return
    work.active_page_index = -1
    for i, page in enumerate(work.pages):
        if page_stack_key(page) == active_page_key:
            work.active_page_index = i
            break
    if work.active_page_index < 0 and len(work.pages) > 0:
        work.active_page_index = 0
    for page in work.pages:
        if int(getattr(page, "active_panel_index", -1)) >= len(page.panels):
            page.active_panel_index = len(page.panels) - 1 if len(page.panels) else -1
        if not active_panel_key:
            continue
        for j, panel in enumerate(page.panels):
            if panel_stack_key(page, panel) == active_panel_key:
                page.active_panel_index = j
                break


def _apply_page_panel_orders(context, stack) -> None:
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return
    try:
        from . import page_grid
    except Exception:  # noqa: BLE001
        page_grid = None
    old_page_offsets = {}
    if page_grid is not None:
        old_page_offsets = {
            page_stack_key(page): page_grid.page_total_offset_mm(work, context.scene, i)
            for i, page in enumerate(work.pages)
        }
    active_page_key = ""
    active_panel_key = ""
    active_idx = int(getattr(work, "active_page_index", -1))
    if 0 <= active_idx < len(work.pages):
        active_page = work.pages[active_idx]
        active_page_key = page_stack_key(active_page)
        panel_idx = int(getattr(active_page, "active_panel_index", -1))
        if 0 <= panel_idx < len(active_page.panels):
            active_panel_key = panel_stack_key(active_page, active_page.panels[panel_idx])

    page_keys = [item.key for item in stack if item.kind == PAGE_KIND]
    _reorder_collection(work.pages, page_keys, page_stack_key)
    try:
        from . import page_range

        page_range.update_page_range_visibility(work)
    except Exception:  # noqa: BLE001
        _logger.exception("page range update after stack order failed")
    if page_grid is not None:
        for i, page in enumerate(work.pages):
            old = old_page_offsets.get(page_stack_key(page))
            if old is None:
                continue
            new = page_grid.page_total_offset_mm(work, context.scene, i)
            dx = new[0] - old[0]
            dy = new[1] - old[1]
            if abs(dx) > 1.0e-6 or abs(dy) > 1.0e-6:
                translate_gp_layers_for_parent_keys(context, gp_parent_keys_for_page(page), dx, dy)

    for page in work.pages:
        page_key = page_stack_key(page)
        panel_keys = [
            item.key
            for item in stack
            if item.kind == PANEL_KIND and split_child_key(item.key)[0] == page_key
        ]
        _reorder_collection(page.panels, panel_keys, lambda panel: panel_stack_key(page, panel))
        count = len(page.panels)
        for i, panel in enumerate(page.panels):
            panel.z_order = count - i - 1
        page.panel_count = count

    _restore_active_page_panel(work, active_page_key, active_panel_key)
    try:
        if page_grid is None:
            return
        page_grid.apply_page_collection_transforms(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("apply page collection transforms after stack order failed")


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

    work = get_work(context)
    if work is None:
        return
    for page in work.pages:
        page_key = page_stack_key(page)
        active_balloon = ""
        if 0 <= page.active_balloon_index < len(page.balloons):
            active_balloon = page.balloons[page.active_balloon_index].id
        front = [
            split_child_key(item.key)[1]
            for item in stack
            if item.kind == "balloon" and split_child_key(item.key)[0] == page_key
        ]
        _reorder_collection(page.balloons, list(reversed(front)), lambda entry: entry.id)
        if active_balloon:
            _restore_active_collection_index(
                page, "active_balloon_index", page.balloons, active_balloon
            )

        active_text = ""
        if 0 <= page.active_text_index < len(page.texts):
            active_text = page.texts[page.active_text_index].id
        front = [
            split_child_key(item.key)[1]
            for item in stack
            if item.kind == "text" and split_child_key(item.key)[0] == page_key
        ]
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


def _gp_group_contains_key(group, key: str) -> bool:
    if group is None or not key:
        return False
    for child in getattr(group, "children", []):
        if gp_utils.is_layer_group(child):
            child_key = _node_stack_key(child)
            if child_key == key or _gp_group_contains_key(child, key):
                return True
    return False


def _apply_gp_parenting(obj, stack, work) -> None:
    gp_data = getattr(obj, "data", None)
    if gp_data is None:
        return
    layers = getattr(gp_data, "layers", None)
    groups = getattr(gp_data, "layer_groups", None)
    if layers is None:
        return
    for item in stack:
        kind = getattr(item, "kind", "")
        if kind not in {"gp", "gp_folder"}:
            continue
        key = str(getattr(item, "key", "") or "")
        node = (
            _find_gp_group_by_key(groups, key)
            if kind == "gp_folder"
            else _find_gp_layer_by_key(layers, key)
        )
        if node is None:
            continue
        desired_parent_key = str(getattr(item, "parent_key", "") or "")
        parent_group = _find_gp_group_by_key(groups, desired_parent_key) if desired_parent_key else None
        logical_parent = kind == "gp" and gp_parent.parent_key_exists(work, desired_parent_key)
        if desired_parent_key and parent_group is None and not logical_parent:
            desired_parent_key = ""
        native_parent_group = None if logical_parent else parent_group
        native_parent_key = "" if native_parent_group is None else _node_stack_key(native_parent_group)
        actual_parent = getattr(node, "parent_group", None)
        actual_parent_key = _node_stack_key(actual_parent) if actual_parent is not None else ""
        if kind == "gp_folder":
            if logical_parent:
                continue
            if desired_parent_key == key or _gp_group_contains_key(node, desired_parent_key):
                continue
            if actual_parent_key == native_parent_key:
                item.parent_key = desired_parent_key
                continue
            if not gp_utils.move_group_to_group(gp_data, node, native_parent_group):
                continue
            gp_parent.set_parent_key(node, "")
        else:
            gp_parent.set_parent_key(node, desired_parent_key if logical_parent else "")
            if actual_parent_key == native_parent_key:
                item.parent_key = desired_parent_key
                continue
            if not gp_utils.move_layer_to_group(gp_data, node, native_parent_group):
                continue
        item.parent_key = desired_parent_key


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
    groups = getattr(gp_data, "layer_groups", None)
    by_parent: dict[str, list[str]] = {}
    for item in stack:
        if effect:
            if item.kind != "effect":
                continue
            native_parent_key = str(getattr(item, "parent_key", "") or "")
        elif item.kind not in {"gp", "gp_folder"}:
            continue
        else:
            parent_key = str(getattr(item, "parent_key", "") or "")
            native_parent_key = parent_key if _find_gp_group_by_key(groups, parent_key) else ""
        by_parent.setdefault(native_parent_key, []).append(stack_item_uid(item))
    for parent_key, uids in by_parent.items():
        _reorder_gp_parent(gp_data, parent_key, uids, effect=effect)


def apply_stack_order(context) -> None:
    stack = getattr(context.scene, "bname_layer_stack", None)
    if stack is None:
        return
    _apply_page_panel_orders(context, stack)
    _apply_simple_collection_orders(context, stack)
    gp_obj = gp_utils.get_master_gpencil()
    if gp_obj is not None:
        _apply_gp_parenting(gp_obj, stack, get_work(context))
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

    if kind == PAGE_KIND:
        if not select_stack_index(context, index):
            return False
        try:
            return "FINISHED" in bpy.ops.bname.page_remove("EXEC_DEFAULT")
        except Exception:  # noqa: BLE001
            _logger.exception("delete page from layer stack failed")
            return False
    if kind == PANEL_KIND:
        if not select_stack_index(context, index):
            return False
        try:
            return "FINISHED" in bpy.ops.bname.panel_remove("EXEC_DEFAULT")
        except Exception:  # noqa: BLE001
            _logger.exception("delete panel from layer stack failed")
            return False
    if kind == "gp":
        obj = resolved.get("object")
        try:
            gp_parent.set_parent_key(resolved["target"], "")
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


def schedule_layer_stack_sync(
    *,
    retries: int = 6,
    interval: float = 0.1,
    apply_order: bool = False,
    moved_uid: str = "",
) -> None:
    """ファイルロード直後の UI 再構築をまたいでレイヤースタックを同期する."""
    global _sync_order_moved_uid, _sync_scheduled, _sync_should_apply_order

    _sync_should_apply_order = _sync_should_apply_order or bool(apply_order)
    if moved_uid:
        _sync_order_moved_uid = moved_uid
    if _sync_scheduled:
        return
    _sync_scheduled = True
    state = {"left": max(1, int(retries))}

    def _tick():
        global _sync_order_moved_uid, _sync_scheduled, _sync_should_apply_order

        try:
            if _sync_should_apply_order:
                if _sync_order_moved_uid:
                    _apply_gp_folder_drop_hint(bpy.context, _sync_order_moved_uid)
                apply_stack_order(bpy.context)
            sync_layer_stack(bpy.context)
            _remember_stack_signature(bpy.context)
            tag_view3d_redraw(bpy.context)
        except Exception:  # noqa: BLE001
            _logger.exception("scheduled layer stack sync failed")
        state["left"] -= 1
        if state["left"] > 0:
            return interval
        _sync_scheduled = False
        _sync_should_apply_order = False
        _sync_order_moved_uid = ""
        return None

    try:
        bpy.app.timers.register(_tick, first_interval=interval)
    except Exception:  # noqa: BLE001
        _logger.exception("schedule layer stack sync failed")
