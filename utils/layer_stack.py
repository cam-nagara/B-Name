"""統合レイヤースタックの同期・選択・並び替えヘルパ."""

from __future__ import annotations

from dataclasses import dataclass
from array import array

import bpy

from ..core.work import get_active_page, get_work
from . import gp_layer_parenting as gp_parent
from . import edge_selection
from . import gpencil as gp_utils
from . import log
from .layer_hierarchy import (
    PAGE_KIND,
    COMA_KIND,
    OUTSIDE_KIND,
    OUTSIDE_STACK_KEY,
    entry_center,
    page_stack_key,
    coma_containing_point,
    coma_stack_key,
    outside_child_key,
    split_child_key,
)

_logger = log.get_logger(__name__)

EFFECT_GP_OBJECT_NAME = "BName_EffectLines"
PAGE_COMA_CHILD_KINDS = {"gp", "effect", "raster", "image", "balloon", "text"}
_sync_scheduled = False
_sync_should_apply_order = False
_sync_order_moved_uid = ""
_draw_stack_signatures: dict[int, tuple[str, ...]] = {}


def _place_effect_gp_object(obj) -> None:
    if obj is None:
        return
    try:
        from .page_grid import GP_Z_LIFT_M

        # 既に正しい位置なら何もしない (draw_handler や UIList draw 等の制限
        # コンテキストから呼ばれた場合に "Writing to ID classes ... not allowed"
        # を回避するため)
        current = obj.location
        if (
            abs(float(current[0])) < 1.0e-9
            and abs(float(current[1])) < 1.0e-9
            and abs(float(current[2]) - GP_Z_LIFT_M) < 1.0e-9
        ):
            return
        obj.location = (0.0, 0.0, GP_Z_LIFT_M)
    except AttributeError:
        # draw context での書き込み禁止エラーは無視 (次の通常コンテキスト時に
        # ensure_effect_gp_object が再実行されて lift される)
        pass
    except Exception:  # noqa: BLE001
        _logger.exception("effect GP location lift failed")


def ensure_effect_gp_object(scene=None):
    scene = scene or bpy.context.scene
    root = gp_utils.ensure_root_collection(scene)
    obj = gp_utils.ensure_gpencil_object(EFFECT_GP_OBJECT_NAME, link_to_collection=False)
    try:
        gp_utils._relink_object_to_collection_only(scene, obj, root)
    except Exception:  # noqa: BLE001
        _logger.exception("effect GP relink failed")
    _place_effect_gp_object(obj)
    return obj


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
        _place_effect_gp_object(obj)
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


def _coma_parent_key_matches(entry, page, coma_key: str, panel) -> bool:
    parent = str(getattr(entry, "parent_key", "") or "")
    if parent == coma_key:
        return True
    if parent == getattr(panel, "id", ""):
        return True
    if parent == getattr(panel, "coma_id", ""):
        return True
    return parent == f"{getattr(page, 'id', '')}:{getattr(panel, 'coma_id', '')}"


def _explicit_entry_parent(entry, page, panels_by_key: dict[str, object]) -> tuple[str, int] | None:
    """エントリ (balloon/text) の永続化された親キーを解決する.

    coma 親は ``panels_by_key`` ではなく ``page.comas`` 全件で解決する。これにより、
    per-panel 呼び出し (``panels_by_key`` が部分集合) でも、別コマがオーソリティ
    親であるエントリを「該当無し」と誤判定して空間フォールバックで重複生成する
    バグを避ける。呼び出し側で ``parent not in panels_by_key`` のときに skip する
    こと。
    """
    _ = panels_by_key  # 互換のため引数は残すが、解決は page.comas で行う
    parent = str(getattr(entry, "parent_key", "") or "")
    if not parent:
        return None
    parent_kind = str(getattr(entry, "parent_kind", "") or "")
    page_key = page_stack_key(page)
    if parent_kind == "page" or (not parent_kind and ":" not in parent):
        if parent in {getattr(page, "id", ""), page_key}:
            return page_key, 1
        return None
    if parent_kind == "coma" or ":" in parent:
        for panel in getattr(page, "comas", []):
            coma_key = coma_stack_key(page, panel)
            if _coma_parent_key_matches(entry, page, coma_key, panel):
                return coma_key, 2
    return None


def _collect_raster_targets_for_page(page, panels_by_key: dict[str, object]):
    scene = getattr(bpy.context, "scene", None)
    coll = getattr(scene, "bname_raster_layers", None) if scene is not None else None
    if coll is None:
        return [], {}
    page_key = page_stack_key(page)
    page_children: list[LayerTarget] = []
    panel_children: dict[str, list[LayerTarget]] = {}
    for entry in reversed(list(coll)):
        if str(getattr(entry, "scope", "") or "page") != "page":
            continue
        parent_kind = str(getattr(entry, "parent_kind", "") or "page")
        parent_key = str(getattr(entry, "parent_key", "") or "")
        label = getattr(entry, "title", "") or getattr(entry, "id", "") or "ラスター"
        if parent_kind == "page" and parent_key in {getattr(page, "id", ""), page_key}:
            page_children.append(LayerTarget("raster", entry.id, label, page_key, 1))
            continue
        if parent_kind == "coma":
            for coma_key, panel in panels_by_key.items():
                if _coma_parent_key_matches(entry, page, coma_key, panel):
                    panel_children.setdefault(coma_key, []).append(
                        LayerTarget("raster", entry.id, label, coma_key, 2)
                    )
                    break
    return page_children, panel_children


def _collect_image_targets_for_page(page, panels_by_key: dict[str, object]):
    scene = getattr(bpy.context, "scene", None)
    coll = getattr(scene, "bname_image_layers", None) if scene is not None else None
    if coll is None:
        return [], {}
    page_key = page_stack_key(page)
    page_children: list[LayerTarget] = []
    panel_children: dict[str, list[LayerTarget]] = {}
    for entry in reversed(list(coll)):
        parent_kind = str(getattr(entry, "parent_kind", "") or "none")
        parent_key = str(getattr(entry, "parent_key", "") or "")
        label = getattr(entry, "title", "") or getattr(entry, "id", "") or "画像"
        if parent_kind == "page" and parent_key in {getattr(page, "id", ""), page_key}:
            page_children.append(LayerTarget("image", entry.id, label, page_key, 1))
            continue
        if parent_kind == "coma":
            for coma_key, panel in panels_by_key.items():
                if _coma_parent_key_matches(entry, page, coma_key, panel):
                    panel_children.setdefault(coma_key, []).append(
                        LayerTarget("image", entry.id, label, coma_key, 2)
                    )
                    break
    return page_children, panel_children


def _retarget_root_subtree_to_outside(targets: list[LayerTarget]) -> list[LayerTarget]:
    """GP/Effect の root 階層を UI 上の「ページ外」配下へ載せ替える."""
    folder_keys = {target.key for target in targets if target.kind == "gp_folder"}
    out: list[LayerTarget] = []
    for target in targets:
        parent_key = target.parent_key if target.parent_key in folder_keys else OUTSIDE_STACK_KEY
        out.append(
            LayerTarget(
                target.kind,
                target.key,
                target.label,
                parent_key,
                int(target.depth) + 1,
            )
        )
    return out


def _collect_outside_layer_targets(
    work,
    scene,
    gp_root_targets: list[LayerTarget],
    effect_root_targets: list[LayerTarget],
) -> list[LayerTarget]:
    targets = [LayerTarget(OUTSIDE_KIND, OUTSIDE_STACK_KEY, "(ページ外)")]
    if work is None:
        return targets

    for panel in sorted(
        list(getattr(work, "shared_comas", [])),
        key=lambda entry: int(getattr(entry, "z_order", 0)),
        reverse=True,
    ):
        stem = str(getattr(panel, "coma_id", "") or getattr(panel, "id", "") or "")
        if not stem:
            continue
        label = str(getattr(panel, "title", "") or stem)
        targets.append(LayerTarget(COMA_KIND, outside_child_key(stem), label, OUTSIDE_STACK_KEY, 1))

    raster_layers = getattr(scene, "bname_raster_layers", None) if scene is not None else None
    if raster_layers is not None:
        used_raster: set[str] = set()
        for entry in reversed(list(raster_layers)):
            scope = str(getattr(entry, "scope", "") or "")
            parent_kind = str(getattr(entry, "parent_kind", "") or "")
            parent_key = str(getattr(entry, "parent_key", "") or "")
            if scope != "master" and parent_kind != "none" and parent_key:
                continue
            key = _ensure_unique_id(entry, used_raster, "raster")
            label = getattr(entry, "title", "") or key
            targets.append(LayerTarget("raster", key, label, OUTSIDE_STACK_KEY, 1))

    image_layers = getattr(scene, "bname_image_layers", None) if scene is not None else None
    if image_layers is not None:
        used_image: set[str] = set()
        for entry in reversed(list(image_layers)):
            parent_kind = str(getattr(entry, "parent_kind", "") or "none")
            parent_key = str(getattr(entry, "parent_key", "") or "")
            if parent_kind != "none" and parent_key:
                continue
            key = _ensure_unique_id(entry, used_image, "image")
            label = getattr(entry, "title", "") or key
            targets.append(LayerTarget("image", key, label, OUTSIDE_STACK_KEY, 1))

    used_balloon: set[str] = set()
    for entry in reversed(list(getattr(work, "shared_balloons", []))):
        bid = _ensure_unique_id(entry, used_balloon, "shared_balloon")
        label = getattr(entry, "id", "") or bid
        targets.append(LayerTarget("balloon", outside_child_key(bid), label, OUTSIDE_STACK_KEY, 1))

    used_text: set[str] = set()
    for entry in reversed(list(getattr(work, "shared_texts", []))):
        tid = _ensure_unique_id(entry, used_text, "shared_text")
        label = getattr(entry, "body", "") or tid
        targets.append(LayerTarget("text", outside_child_key(tid), label, OUTSIDE_STACK_KEY, 1))

    targets.extend(_retarget_root_subtree_to_outside(effect_root_targets))
    targets.extend(_retarget_root_subtree_to_outside(gp_root_targets))
    return targets


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
    raster_page_children, raster_panel_children = _collect_raster_targets_for_page(
        page,
        panels_by_key,
    )
    page_children.extend(raster_page_children)
    for coma_key, children in raster_panel_children.items():
        panel_children.setdefault(coma_key, []).extend(children)
    image_page_children, image_panel_children = _collect_image_targets_for_page(
        page,
        panels_by_key,
    )
    page_children.extend(image_page_children)
    for coma_key, children in image_panel_children.items():
        panel_children.setdefault(coma_key, []).extend(children)

    for entry in reversed(list(getattr(page, "balloons", []))):
        bid = _ensure_unique_id(entry, used_balloon, "balloon")
        explicit_parent = _explicit_entry_parent(entry, page, panels_by_key)
        if explicit_parent is not None:
            parent, depth = explicit_parent
            # オーソリティ親が今回の panels_by_key 範囲外なら skip。
            # (all-panels 呼び出しでは全コマが含まれるため skip されない)
            if depth == 2 and parent not in panels_by_key:
                continue
        else:
            panel = coma_containing_point(page, *entry_center(entry))
            if panel is not None:
                parent = coma_stack_key(page, panel)
                depth = 2
                if parent not in panels_by_key:
                    continue
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
        explicit_parent = _explicit_entry_parent(entry, page, panels_by_key)
        if explicit_parent is not None:
            parent, depth = explicit_parent
            if depth == 2 and parent not in panels_by_key:
                continue
        else:
            center = entry_center(entry)
            panel = coma_containing_point(page, *center)
            if panel is not None:
                parent = coma_stack_key(page, panel)
                depth = 2
                if parent not in panels_by_key:
                    continue
            else:
                parent = page_key
                depth = 1
        target = LayerTarget("text", f"{page_key}:{tid}", label, parent, depth)
        if depth == 2:
            panel_children.setdefault(parent, []).append(target)
        else:
            page_children.append(target)

    for coma_key in panels_by_key:
        targets.extend(panel_children.get(coma_key, []))
    if include_page_children:
        targets.extend(page_children)
    return targets


def _partition_gp_targets(
    obj,
    work,
    *,
    kind: str = "gp",
) -> tuple[list[LayerTarget], dict[str, list[LayerTarget]]]:
    """``_iter_gp_targets`` を ``gp_targets_by_parent`` (page/coma 親) と
    ``root_targets`` (それ以外: gp_folder, root レイヤー, gp_folder 配下レイヤー
    含む) に分配する.

    旧実装は gp_folder 配下のレイヤーから parent_key を stripping していたため、
    sync_layer_stack 後に「folder 内にあるはずのレイヤーが root に parent_key=''
    で並ぶ」状態になり、apply_stack_order が actual_parent_group との不一致を
    検知してフォルダ外へ移動させる破壊的バグを誘発していた。folder 配下は
    target.parent_key=folder_key を保持したまま root_targets へ入れ、後段の
    ``_normalize_tree_order`` がフォルダ配下に正しくネストする。
    """
    root_targets: list[LayerTarget] = []
    targets_by_parent: dict[str, list[LayerTarget]] = {}
    if obj is None:
        return root_targets, targets_by_parent
    for target in _iter_gp_targets(obj, kind=kind):
        if target.kind == kind and gp_parent.parent_key_exists(work, target.parent_key):
            targets_by_parent.setdefault(target.parent_key, []).append(target)
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
    effect_obj = get_effect_gp_object()
    effect_root_targets, effect_targets_by_parent = _partition_gp_targets(
        effect_obj,
        work,
        kind="effect",
    )

    if work is not None and getattr(work, "loaded", False):
        from . import page_range

        targets.extend(
            _collect_outside_layer_targets(
                work,
                scene,
                gp_root_targets,
                effect_root_targets,
            )
        )
        for page in work.pages:
            if not page_range.page_in_range(page):
                continue
            page_key = page_stack_key(page)
            label = getattr(page, "title", "") or page_key
            targets.append(LayerTarget(PAGE_KIND, page_key, label))
            if not bool(getattr(page, "stack_expanded", True)):
                continue
            panels = sorted(
                list(getattr(page, "comas", [])),
                key=lambda p: int(getattr(p, "z_order", 0)),
                reverse=True,
            )
            panels_by_key: dict[str, object] = {}
            for panel in panels:
                key = coma_stack_key(page, panel)
                panels_by_key[key] = panel
                panel_label = getattr(panel, "title", "") or getattr(panel, "coma_id", "") or key
                targets.append(LayerTarget(COMA_KIND, key, panel_label, page_key, 1))
                targets.extend(gp_targets_by_parent.get(key, []))
                targets.extend(effect_targets_by_parent.get(key, []))
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
            for target in effect_targets_by_parent.get(page_key, []):
                targets.append(target)
                visible_parent_keys.add(target.key)
            for target in all_page_layers:
                if target.parent_key in visible_parent_keys:
                    targets.append(target)
                    visible_parent_keys.add(target.key)

    elif gp_root_targets or effect_root_targets:
        targets.extend(effect_root_targets)
        targets.extend(gp_root_targets)

    # 防御: 万一 UID 重複が混入してもスタックには 1 行しか出さない。
    # (per-panel 呼び出しと spatial fallback の組合せで重複が紛れ込むケースの保険)
    seen: set[str] = set()
    deduped: list[LayerTarget] = []
    for t in targets:
        if t.uid in seen:
            continue
        seen.add(t.uid)
        deduped.append(t)
    return deduped


def _set_item_from_target(item, target: LayerTarget) -> None:
    item.kind = target.kind
    item.name = target.label
    item.key = target.key
    item.label = target.label
    item.parent_key = target.parent_key
    item.depth = target.depth


def _find_insert_index_for_target(stack, target: LayerTarget) -> int:
    if target.kind == OUTSIDE_KIND:
        return 0
    if target.parent_key:
        parent_idx = -1
        last_child_idx = -1
        for i, item in enumerate(stack):
            if item.key == target.parent_key and item.kind in {OUTSIDE_KIND, PAGE_KIND, COMA_KIND, "gp_folder", "balloon_group"}:
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
    coma_key_order_by_page: dict[str, list[str]] | None = None,
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

    def _append_subtree_in_stack_order(parent_key: str) -> None:
        """``parent_key`` 直下の子をスタック順で append し、コンテナなら再帰."""
        for child in stack:
            uid = stack_item_uid(child)
            if uid in used:
                continue
            if str(getattr(child, "parent_key", "") or "") != parent_key:
                continue
            _append_uid(child)
            kind = getattr(child, "kind", "")
            if kind in {OUTSIDE_KIND, COMA_KIND, "balloon_group", "gp_folder"}:
                _append_subtree_in_stack_order(getattr(child, "key", ""))

    def _append_page_subtree(page_item) -> None:
        _append_uid(page_item)
        page_key = page_item.key
        if coma_key_order_by_page is not None:
            # align モード: コマを実データ順に並べ、その後にページ直下子を出す.
            panel_items = [
                item
                for item in stack
                if item.kind == COMA_KIND and split_child_key(item.key)[0] == page_key
            ]
            panel_uid_order = [
                target_uid(COMA_KIND, key)
                for key in coma_key_order_by_page.get(page_key, [])
            ]
            panel_items = _ordered_items_by_uid(panel_items, panel_uid_order)
            for panel_item in panel_items:
                _append_uid(panel_item)
                _append_subtree_in_stack_order(panel_item.key)
            for child in stack:
                if (
                    str(getattr(child, "parent_key", "") or "") == page_key
                    and child.kind != COMA_KIND
                ):
                    _append_uid(child)
                    kind = getattr(child, "kind", "")
                    if kind in {"balloon_group", "gp_folder"}:
                        _append_subtree_in_stack_order(child.key)
        else:
            # デフォルト: スタック順を尊重。ページ直下の子(ページ直下 GP, コマ等)
            # を出現順に append。これにより「ページとその第1コマの間に GP を
            # 入れる」など、ユーザーが選んだ任意位置を保てる.
            _append_subtree_in_stack_order(page_key)

    outside_item = _find_stack_item(stack, OUTSIDE_KIND, OUTSIDE_STACK_KEY)
    if outside_item is not None:
        _append_uid(outside_item)
        _append_subtree_in_stack_order(OUTSIDE_STACK_KEY)

    if page_key_order is not None:
        for page_item in page_items:
            _append_page_subtree(page_item)

    for item in stack:
        if stack_item_uid(item) in used:
            continue
        if item.kind == OUTSIDE_KIND:
            _append_uid(item)
            _append_subtree_in_stack_order(item.key)
        elif item.kind == PAGE_KIND:
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
    align_coma_order: bool = False,
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
    coma_key_order_by_page = None
    if align_coma_order:
        coma_key_order_by_page = {}
        for target in targets:
            if target.kind != COMA_KIND:
                continue
            page_key, _stem = split_child_key(target.key)
            coma_key_order_by_page.setdefault(page_key, []).append(target.key)
    _normalize_tree_order(stack, page_key_order, coma_key_order_by_page)

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
    if a_kind == COMA_KIND or b_kind == COMA_KIND:
        return (
            a_kind == b_kind == COMA_KIND
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


def _parent_item_allows_child(parent, child_kind: str) -> bool:
    parent_kind = getattr(parent, "kind", "")
    if parent_kind == OUTSIDE_KIND:
        return child_kind in PAGE_COMA_CHILD_KINDS or child_kind in {COMA_KIND, "gp_folder"}
    if parent_kind == PAGE_KIND:
        return child_kind in PAGE_COMA_CHILD_KINDS or child_kind == COMA_KIND
    if parent_kind == COMA_KIND:
        return child_kind in PAGE_COMA_CHILD_KINDS
    if parent_kind == "gp_folder":
        return child_kind in {"gp", "gp_folder"}
    return False


def _parent_key_exists_for_child(context, child_kind: str, parent_key: str) -> bool:
    parent_key = str(parent_key or "")
    if not parent_key:
        return True
    if parent_key == OUTSIDE_STACK_KEY:
        return child_kind in PAGE_COMA_CHILD_KINDS or child_kind in {COMA_KIND, "gp_folder"}
    if child_kind == COMA_KIND:
        return ":" not in parent_key and gp_parent.parent_key_exists(get_work(context), parent_key)
    work = get_work(context)
    if child_kind in PAGE_COMA_CHILD_KINDS and gp_parent.parent_key_exists(work, parent_key):
        return True
    if child_kind in {"gp", "gp_folder"}:
        stack = getattr(getattr(context, "scene", None), "bname_layer_stack", None)
        return bool(_find_stack_item(stack, "gp_folder", parent_key))
    return False


def _parent_key_from_flat_drop(stack, moved_index: int, child_kind: str) -> str:
    """UIList の平坦D&D位置から、移動した行の親キーを推定する."""
    if stack is None or moved_index <= 0:
        return ""
    previous = stack[moved_index - 1]
    if _parent_item_allows_child(previous, child_kind):
        return str(getattr(previous, "key", "") or "")
    if child_kind in PAGE_COMA_CHILD_KINDS and getattr(previous, "kind", "") in PAGE_COMA_CHILD_KINDS:
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


def _parent_key_one_level_up(stack, parent_key: str) -> str:
    parent = (
        _find_stack_item(stack, OUTSIDE_KIND, parent_key)
        or
        _find_stack_item(stack, "gp_folder", parent_key)
        or _find_stack_item(stack, COMA_KIND, parent_key)
        or _find_stack_item(stack, PAGE_KIND, parent_key)
    )
    if parent is None:
        page_key, _child_key = split_child_key(parent_key)
        return page_key if page_key != parent_key else ""
    if getattr(parent, "kind", "") == COMA_KIND:
        page_key, _child_key = split_child_key(parent_key)
        return page_key
    return str(getattr(parent, "parent_key", "") or "")


def _drop_parent_from_nesting_delta(stack, item, moved_index: int, nesting_delta: int) -> str:
    old_parent_key = str(getattr(item, "parent_key", "") or "")
    if nesting_delta < 0:
        return _parent_key_one_level_up(stack, old_parent_key)
    if nesting_delta > 0:
        return _parent_key_from_flat_drop(stack, moved_index, getattr(item, "kind", ""))
    return _parent_key_from_flat_drop(stack, moved_index, getattr(item, "kind", ""))


def _stack_item_page_key(item) -> str:
    """スタック行が属するページキーを返す。ページ非依存なら "" を返す.

    - balloon / text / balloon_group: 行 key が ``page_id:child_id`` 形式
    - raster: 永続化された ``parent_key`` のページプレフィックス
    - gp / effect / gp_folder: ``parent_key`` のページプレフィックス
    """
    kind = getattr(item, "kind", "")
    key = str(getattr(item, "key", "") or "")
    parent_key = str(getattr(item, "parent_key", "") or "")
    if key == OUTSIDE_STACK_KEY or parent_key == OUTSIDE_STACK_KEY:
        return ""
    if kind in {"balloon", "balloon_group", "text"}:
        page_key, _ = split_child_key(key)
        if page_key == OUTSIDE_STACK_KEY:
            return ""
        return page_key
    if kind in {"raster", "gp", "gp_folder", "effect"}:
        page_key, _ = split_child_key(parent_key)
        if page_key == OUTSIDE_STACK_KEY:
            return ""
        return page_key
    return ""


def _parent_key_page(parent_key: str) -> str:
    if not parent_key:
        return ""
    if parent_key == OUTSIDE_STACK_KEY:
        return ""
    page_key, _ = split_child_key(parent_key)
    if page_key == OUTSIDE_STACK_KEY:
        return ""
    return page_key


def _apply_stack_drop_hint(context, moved_uid: str, *, nesting_delta: int = 0) -> bool:
    """UIList D&Dの位置/横方向ヒントを、保存可能な親変更へ変換する."""
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bname_layer_stack", None) if scene is not None else None
    moved_index = _find_stack_index_by_uid(stack, moved_uid)
    if moved_index < 0:
        return False
    item = stack[moved_index]
    kind = getattr(item, "kind", "")
    if kind not in {COMA_KIND, "gp", "gp_folder", "effect", "raster", "image", "balloon", "text"}:
        return False
    parent_key = _drop_parent_from_nesting_delta(stack, item, moved_index, nesting_delta)
    old_parent_key = str(getattr(item, "parent_key", "") or "")
    if parent_key and not _parent_key_exists_for_child(context, kind, parent_key):
        return False
    if kind != "gp_folder":
        try:
            from . import layer_stack_dnd

            if (
                layer_stack_dnd.child_can_use_semantic_parent(kind)
                and layer_stack_dnd.is_semantic_parent_key(context, parent_key)
            ):
                return layer_stack_dnd.apply_semantic_parent_drop(context, item, parent_key)
        except Exception:  # noqa: BLE001
            _logger.exception("semantic layer stack D&D parent drop failed")
            return False
    # ここから下は gp_folder など、実コレクション移送を伴わない従来の親キー更新。
    # page/coma/outside への意味的な D&D は上で layer_reparent に委譲済み。
    # フォールバック経路ではページをまたぐ単純 parent_key 書き換えを拒否する。
    entry_page = _stack_item_page_key(item)
    target_page = _parent_key_page(parent_key)
    if entry_page and target_page and entry_page != target_page:
        return False
    if kind == "gp_folder":
        item_key = str(getattr(item, "key", "") or "")
        if parent_key == item_key or _is_stack_folder_descendant(stack, item_key, parent_key):
            return False
    if kind in {"effect", "raster", "balloon", "text"} and _find_stack_item(stack, "gp_folder", parent_key):
        return False
    # 旧バージョンでは Y-only ドラッグでの「深く入れる」(depth 増加) を抑止していたが、
    # CSP / Photoshop のレイヤーパネルでは Y-drag だけでフォルダ/コマに直接入れられるのが
    # 標準。ここで block すると D&D が「入れたいのに入らない」状態になるため撤廃。
    if parent_key == old_parent_key:
        return False
    item.parent_key = parent_key
    parent = None
    if parent_key:
        parent = (
            _find_stack_item(stack, "gp_folder", parent_key)
            or _find_stack_item(stack, OUTSIDE_KIND, parent_key)
            or _find_stack_item(stack, PAGE_KIND, parent_key)
            or _find_stack_item(stack, COMA_KIND, parent_key)
        )
    item.depth = int(getattr(parent, "depth", -1)) + 1 if parent is not None else 0
    if kind in {"gp", "gp_folder"}:
        obj = gp_utils.get_master_gpencil()
        groups = getattr(getattr(obj, "data", None), "layer_groups", None) if obj is not None else None
        group = _find_gp_group_by_key(groups, parent_key) if parent_key else None
        if group is not None and hasattr(group, "is_expanded"):
            try:
                group.is_expanded = True
            except Exception:  # noqa: BLE001
                pass
    return True


def apply_stack_drop_hint(context, moved_uid: str, *, nesting_delta: int = 0) -> bool:
    """D&D中の同一行ドロップや横方向ドラッグを親変更として適用する。"""
    changed = _apply_stack_drop_hint(context, moved_uid, nesting_delta=nesting_delta)
    if changed:
        apply_stack_order(context)
        _remember_stack_signature(context)
    return changed


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
    _apply_stack_drop_hint(context, moved_uid)
    apply_stack_order(context)
    _remember_stack_signature(context)
    return True


def sync_layer_stack_after_data_change(
    context,
    *,
    align_page_order: bool = False,
    align_coma_order: bool = False,
) -> None:
    """Operator で実データを更新した直後に、UIList と既知シグネチャを揃える."""
    try:
        sync_layer_stack(
            context,
            align_page_order=align_page_order,
            align_coma_order=align_coma_order,
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
    if kind == COMA_KIND and page is not None:
        idx = int(getattr(page, "active_coma_index", -1))
        if 0 <= idx < len(page.comas):
            return COMA_KIND, coma_stack_key(page, page.comas[idx])
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
    if kind == "raster":
        coll = getattr(scene, "bname_raster_layers", None)
        idx = int(getattr(scene, "bname_active_raster_layer_index", -1))
        if coll is not None and 0 <= idx < len(coll):
            return "raster", getattr(coll[idx], "id", "")
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


def gp_parent_key_for_coma(page, panel) -> str:
    return gp_parent.parent_key_for_coma(page, panel)


def gp_layers_for_parent_keys(context, parent_keys: set[str]) -> list[object]:
    _ = context
    obj = gp_utils.get_master_gpencil()
    if obj is None or not parent_keys:
        return []
    return list(gp_parent.iter_layers_with_parent(obj, set(parent_keys)))


def effect_layers_for_parent_keys(context, parent_keys: set[str]) -> list[object]:
    _ = context
    obj = get_effect_gp_object()
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


def delete_effect_layers_for_parent_keys(context, parent_keys: set[str]) -> int:
    obj = get_effect_gp_object()
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
            _logger.exception("delete effect layer for parent failed: %s", getattr(layer, "name", ""))
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


def reparent_effect_layers(context, old_parent_key: str, new_parent_key: str) -> int:
    work = get_work(context)
    if not old_parent_key or not gp_parent.parent_key_exists(work, new_parent_key):
        return 0
    obj = get_effect_gp_object()
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


def translate_effect_layers_for_parent_keys(context, parent_keys: set[str], dx_mm: float, dy_mm: float) -> int:
    moved = 0
    for layer in effect_layers_for_parent_keys(context, parent_keys):
        gp_parent.translate_layer(layer, dx_mm, dy_mm)
        moved += 1
    if moved:
        tag_view3d_redraw(context)
    return moved


def capture_gp_layers_for_parent_keys(context, parent_keys: set[str]):
    return gp_parent.capture_layers(gp_layers_for_parent_keys(context, parent_keys))


def capture_effect_layers_for_parent_keys(context, parent_keys: set[str]):
    return gp_parent.capture_layers(effect_layers_for_parent_keys(context, parent_keys))


def restore_gp_layer_snapshots(snapshot) -> None:
    gp_parent.restore_layers(snapshot)


def raster_entries_for_parent_keys(context, parent_keys: set[str]) -> list[object]:
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bname_raster_layers", None) if scene is not None else None
    if coll is None or not parent_keys:
        return []
    keys = {str(key or "") for key in parent_keys if str(key or "")}
    return [
        entry for entry in coll
        if str(getattr(entry, "parent_key", "") or "") in keys
    ]


def translate_raster_layers_for_parent_keys(context, parent_keys: set[str], dx_mm: float, dy_mm: float) -> int:
    try:
        from ..operators import raster_layer_op
    except Exception:  # noqa: BLE001
        return 0
    moved = 0
    for entry in raster_entries_for_parent_keys(context, parent_keys):
        try:
            if raster_layer_op.translate_raster_layer_pixels(context, entry, dx_mm, dy_mm):
                moved += 1
        except Exception:  # noqa: BLE001
            _logger.exception("translate raster pixels failed: %s", getattr(entry, "id", ""))
    if moved:
        tag_view3d_redraw(context)
    return moved


def capture_raster_layers_for_parent_keys(context, parent_keys: set[str]):
    try:
        from ..operators import raster_layer_op
    except Exception:  # noqa: BLE001
        return []
    snapshots = []
    for entry in raster_entries_for_parent_keys(context, parent_keys):
        image = raster_layer_op.ensure_raster_image(context, entry, create_missing=False)
        if image is None:
            continue
        try:
            total = int(image.size[0]) * int(image.size[1]) * 4
            data = array("f", image.pixels[:])
            if len(data) != total:
                continue
            snapshots.append((str(getattr(entry, "id", "") or ""), str(image.name), data))
        except Exception:  # noqa: BLE001
            _logger.exception("capture raster pixels failed: %s", getattr(entry, "id", ""))
    return snapshots


def restore_raster_layer_snapshots(context, snapshot) -> None:
    try:
        from ..operators import raster_layer_op
    except Exception:  # noqa: BLE001
        raster_layer_op = None
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bname_raster_layers", None) if scene is not None else None
    entry_by_id = {
        str(getattr(entry, "id", "") or ""): entry
        for entry in (coll or [])
    }
    for entry_id, image_name, data in snapshot or []:
        image = bpy.data.images.get(str(image_name))
        if image is None:
            continue
        try:
            image.pixels.foreach_set(data)
            image.update()
            entry = entry_by_id.get(str(entry_id))
            if entry is not None and raster_layer_op is not None:
                raster_layer_op.mark_raster_dirty(entry)
        except Exception:  # noqa: BLE001
            _logger.exception("restore raster pixels failed: %s", image_name)


def resolve_stack_item(context, item):
    """スタック行が参照する実体を辞書で返す。見つからなければ None."""
    if item is None:
        return None
    kind = getattr(item, "kind", "")
    key = getattr(item, "key", "")
    scene = context.scene
    work = get_work(context)
    page = get_active_page(context)

    if kind == OUTSIDE_KIND:
        if work is None:
            return None
        return {"kind": kind, "target": work, "object": None, "index": -1}
    if kind == PAGE_KIND:
        if work is None:
            return None
        idx, entry = _find_by_id(work.pages, key)
        return {"kind": kind, "target": entry, "object": None, "index": idx}
    if kind == COMA_KIND:
        if work is None:
            return None
        page_id, stem = split_child_key(key)
        if page_id == OUTSIDE_STACK_KEY:
            for coma_idx, panel in enumerate(getattr(work, "shared_comas", [])):
                if getattr(panel, "coma_id", "") == stem or getattr(panel, "id", "") == stem:
                    return {
                        "kind": kind,
                        "target": panel,
                        "object": None,
                        "index": coma_idx,
                        "page": None,
                        "page_index": -1,
                    }
            return {
                "kind": kind,
                "target": None,
                "object": None,
                "index": -1,
                "page": None,
                "page_index": -1,
            }
        page_idx, target_page = _find_by_id(work.pages, page_id)
        if target_page is None:
            return None
        for coma_idx, panel in enumerate(target_page.comas):
            if getattr(panel, "coma_id", "") == stem or getattr(panel, "id", "") == stem:
                return {
                    "kind": kind,
                    "target": panel,
                    "object": None,
                    "index": coma_idx,
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
    if kind == "raster":
        coll = getattr(scene, "bname_raster_layers", None)
        if coll is None:
            return None
        idx, entry = _find_by_id(coll, key)
        target_page = None
        page_idx = -1
        if work is not None and entry is not None:
            parent_key = str(getattr(entry, "parent_key", "") or "")
            for i, candidate in enumerate(getattr(work, "pages", [])):
                if parent_key in {
                    getattr(candidate, "id", ""),
                    page_stack_key(candidate),
                }:
                    target_page = candidate
                    page_idx = i
                    break
        return {
            "kind": kind,
            "target": entry,
            "object": None,
            "index": idx,
            "page": target_page,
            "page_index": page_idx,
        }
    if kind == "balloon":
        page_id, child_id = split_child_key(key)
        if page_id == OUTSIDE_STACK_KEY and work is not None:
            idx, entry = _find_by_id(getattr(work, "shared_balloons", []), child_id or key)
            return {
                "kind": kind,
                "target": entry,
                "object": None,
                "index": idx,
                "page": None,
                "page_index": -1,
            }
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
        if page_id == OUTSIDE_STACK_KEY and work is not None:
            idx, entry = _find_by_id(getattr(work, "shared_texts", []), child_id or key)
            return {
                "kind": kind,
                "target": entry,
                "object": None,
                "index": idx,
                "page": None,
                "page_index": -1,
            }
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


def _selection_attr_name(target) -> str:
    """エントリの選択フラグ属性名を返す。GP layer は native ``select`` を使う."""
    if target is None:
        return ""
    if hasattr(target, "selected"):
        return "selected"
    if hasattr(target, "select"):
        return "select"
    return ""


def is_item_selected(context, item) -> bool:
    """``item`` がマルチセレクト集合に含まれるかを返す。

    アクティブ行 (``bname_active_layer_stack_index``) も「選択中」として扱う。
    """
    scene = getattr(context, "scene", None)
    if scene is None or item is None:
        return False
    stack = getattr(scene, "bname_layer_stack", None)
    if stack is not None:
        idx = int(getattr(scene, "bname_active_layer_stack_index", -1))
        if 0 <= idx < len(stack):
            if stack_item_uid(stack[idx]) == stack_item_uid(item):
                return True
    resolved = resolve_stack_item(context, item)
    target = resolved.get("target") if resolved is not None else None
    attr = _selection_attr_name(target)
    if not attr:
        return False
    try:
        return bool(getattr(target, attr))
    except Exception:  # noqa: BLE001
        return False


def set_item_selected(context, item, value: bool) -> bool:
    """``item`` 配下の実エントリにマルチセレクトフラグを書き込む.

    GP layer は native ``select`` を使い、その他のエントリは独自 ``selected``
    プロパティを使う。balloon_group のような仮想行は対象外で False を返す.
    """
    if item is None:
        return False
    resolved = resolve_stack_item(context, item)
    target = resolved.get("target") if resolved is not None else None
    attr = _selection_attr_name(target)
    if not attr:
        return False
    try:
        setattr(target, attr, bool(value))
        return True
    except Exception:  # noqa: BLE001
        return False


def clear_all_selection(context) -> int:
    """スタック全行のマルチセレクトフラグをクリアする (アクティブ行は影響なし)."""
    scene = getattr(context, "scene", None)
    if scene is None:
        return 0
    stack = getattr(scene, "bname_layer_stack", None)
    if stack is None:
        return 0
    cleared = 0
    for item in stack:
        if set_item_selected(context, item, False):
            cleared += 1
    return cleared


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


def _leave_grease_pencil_draw_modes(context) -> None:
    view_layer = getattr(context, "view_layer", None)
    obj = getattr(view_layer, "objects", None)
    active = getattr(obj, "active", None) if obj is not None else None
    if active is None or getattr(active, "type", "") != "GREASEPENCIL":
        return
    if getattr(active, "mode", "") not in {"PAINT_GREASE_PENCIL", "EDIT"}:
        return
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
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
    if kind != "gp":
        _leave_grease_pencil_draw_modes(context)
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
            scene.bname_current_coma_id = ""
            scene.bname_current_coma_page_id = ""
        except Exception:  # noqa: BLE001
            pass
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = PAGE_KIND
        edge_selection.clear_selection(context)
    elif kind == COMA_KIND:
        work = get_work(context)
        page_idx = int(resolved.get("page_index", -1))
        coma_idx = int(resolved.get("index", -1))
        target_page = resolved.get("page")
        if target_page is None:
            scene.bname_active_gp_folder_key = ""
            scene.bname_active_layer_kind = COMA_KIND
            edge_selection.clear_selection(context)
            target = resolved.get("target")
            if target is not None and hasattr(target, "selected"):
                try:
                    target.selected = True
                except Exception:  # noqa: BLE001
                    pass
            tag_view3d_redraw(context)
            return True
        if (
            work is None
            or target_page is None
            or not (0 <= page_idx < len(work.pages))
            or not (0 <= coma_idx < len(target_page.comas))
        ):
            return False
        work.active_page_index = page_idx
        target_page.active_coma_index = coma_idx
        try:
            from ..core.mode import MODE_PAGE, set_mode

            set_mode(MODE_PAGE, context)
            scene.bname_overview_mode = True
        except Exception:  # noqa: BLE001
            pass
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = COMA_KIND
        edge_selection.set_selection(
            context,
            "border",
            page_index=page_idx,
            coma_index=coma_idx,
        )
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
        edge_selection.clear_selection(context)
    elif kind == "gp_folder":
        _set_active_object(context, resolved.get("object"))
        scene.bname_active_gp_folder_key = item.key
        scene.bname_active_layer_kind = "gp_folder"
        edge_selection.clear_selection(context)
    elif kind == "image":
        scene.bname_active_image_layer_index = int(resolved.get("index", -1))
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = "image"
        edge_selection.clear_selection(context)
    elif kind == "raster":
        page_idx = int(resolved.get("page_index", -1))
        work = get_work(context)
        if work is not None and 0 <= page_idx < len(work.pages):
            work.active_page_index = page_idx
        scene.bname_active_raster_layer_index = int(resolved.get("index", -1))
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = "raster"
        edge_selection.clear_selection(context)
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
        edge_selection.clear_selection(context)
    elif kind == "balloon":
        target_page = resolved.get("page") or page
        if target_page is None:
            target = resolved.get("target")
            if target is None:
                return False
            try:
                target.selected = True
            except Exception:  # noqa: BLE001
                pass
            scene.bname_active_gp_folder_key = ""
            scene.bname_active_layer_kind = "balloon"
            edge_selection.clear_selection(context)
            tag_view3d_redraw(context)
            return True
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
        edge_selection.clear_selection(context)
    elif kind == "text":
        target_page = resolved.get("page") or page
        if target_page is None:
            target = resolved.get("target")
            if target is None:
                return False
            try:
                target.selected = True
            except Exception:  # noqa: BLE001
                pass
            scene.bname_active_gp_folder_key = ""
            scene.bname_active_layer_kind = "text"
            edge_selection.clear_selection(context)
            tag_view3d_redraw(context)
            return True
        work = get_work(context)
        page_idx = int(resolved.get("page_index", -1))
        if work is not None and 0 <= page_idx < len(work.pages):
            work.active_page_index = page_idx
        target_page.active_text_index = int(resolved.get("index", -1))
        scene.bname_active_gp_folder_key = ""
        scene.bname_active_layer_kind = "text"
        edge_selection.clear_selection(context)
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
        try:
            from ..operators import effect_line_op

            effect_line_op._load_layer_params_to_scene(context, obj, layer)
        except Exception:  # noqa: BLE001
            _logger.exception("effect layer params restore failed")
        edge_selection.clear_selection(context)
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


def _restore_active_page_coma(work, active_page_key: str, active_coma_key: str) -> None:
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
        if int(getattr(page, "active_coma_index", -1)) >= len(page.comas):
            page.active_coma_index = len(page.comas) - 1 if len(page.comas) else -1
        if not active_coma_key:
            continue
        for j, panel in enumerate(page.comas):
            if coma_stack_key(page, panel) == active_coma_key:
                page.active_coma_index = j
                break


def _apply_page_coma_orders(context, stack) -> None:
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
    active_coma_key = ""
    active_idx = int(getattr(work, "active_page_index", -1))
    if 0 <= active_idx < len(work.pages):
        active_page = work.pages[active_idx]
        active_page_key = page_stack_key(active_page)
        coma_idx = int(getattr(active_page, "active_coma_index", -1))
        if 0 <= coma_idx < len(active_page.comas):
            active_coma_key = coma_stack_key(active_page, active_page.comas[coma_idx])

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
        coma_keys = [
            item.key
            for item in stack
            if item.kind == COMA_KIND and split_child_key(item.key)[0] == page_key
        ]
        _reorder_collection(page.comas, coma_keys, lambda panel: coma_stack_key(page, panel))
        count = len(page.comas)
        for i, panel in enumerate(page.comas):
            panel.z_order = count - i - 1
        page.coma_count = count

    _restore_active_page_coma(work, active_page_key, active_coma_key)
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

    raster_layers = getattr(scene, "bname_raster_layers", None)
    if raster_layers is not None:
        active_key = ""
        idx = int(getattr(scene, "bname_active_raster_layer_index", -1))
        if 0 <= idx < len(raster_layers):
            active_key = getattr(raster_layers[idx], "id", "")
        front = [item.key for item in stack if item.kind == "raster"]
        _reorder_collection(raster_layers, list(reversed(front)), lambda entry: entry.id)
        if active_key:
            _restore_active_collection_index(
                scene, "bname_active_raster_layer_index", raster_layers, active_key
            )

    work = get_work(context)
    if work is None:
        return

    shared_balloons = getattr(work, "shared_balloons", None)
    if shared_balloons is not None:
        front = [
            split_child_key(item.key)[1]
            for item in stack
            if item.kind == "balloon"
            and split_child_key(item.key)[0] == OUTSIDE_STACK_KEY
        ]
        _reorder_collection(shared_balloons, list(reversed(front)), lambda entry: entry.id)

    shared_texts = getattr(work, "shared_texts", None)
    if shared_texts is not None:
        front = [
            split_child_key(item.key)[1]
            for item in stack
            if item.kind == "text"
            and split_child_key(item.key)[0] == OUTSIDE_STACK_KEY
        ]
        _reorder_collection(shared_texts, list(reversed(front)), lambda entry: entry.id)

    shared_comas = getattr(work, "shared_comas", None)
    if shared_comas is not None:
        front = [
            split_child_key(item.key)[1]
            for item in stack
            if item.kind == COMA_KIND
            and split_child_key(item.key)[0] == OUTSIDE_STACK_KEY
        ]
        _reorder_collection(
            shared_comas,
            list(reversed(front)),
            lambda entry: str(getattr(entry, "coma_id", "") or getattr(entry, "id", "")),
        )
        count = len(shared_comas)
        for i, panel in enumerate(shared_comas):
            panel.z_order = count - i - 1

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
        ui_parent_key = desired_parent_key
        if desired_parent_key == OUTSIDE_STACK_KEY:
            desired_parent_key = ""
        parent_group = _find_gp_group_by_key(groups, desired_parent_key) if desired_parent_key else None
        logical_parent = kind == "gp" and gp_parent.parent_key_exists(work, desired_parent_key)
        if desired_parent_key and parent_group is None and not logical_parent:
            desired_parent_key = ""
            if ui_parent_key != OUTSIDE_STACK_KEY:
                ui_parent_key = ""
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
                item.parent_key = ui_parent_key
                continue
            if not gp_utils.move_group_to_group(gp_data, node, native_parent_group):
                continue
            gp_parent.set_parent_key(node, "")
        else:
            gp_parent.set_parent_key(node, desired_parent_key if logical_parent else "")
            if actual_parent_key == native_parent_key:
                item.parent_key = ui_parent_key
                continue
            if not gp_utils.move_layer_to_group(gp_data, node, native_parent_group):
                continue
        item.parent_key = ui_parent_key


def _apply_effect_parenting(obj, stack, work) -> None:
    gp_data = getattr(obj, "data", None)
    layers = getattr(gp_data, "layers", None) if gp_data is not None else None
    if layers is None:
        return
    for item in stack:
        if getattr(item, "kind", "") != "effect":
            continue
        node = _find_gp_layer_by_key(layers, str(getattr(item, "key", "") or ""))
        if node is None:
            continue
        desired_parent_key = str(getattr(item, "parent_key", "") or "")
        ui_parent_key = desired_parent_key
        if desired_parent_key == OUTSIDE_STACK_KEY:
            desired_parent_key = ""
        if desired_parent_key and not gp_parent.parent_key_exists(work, desired_parent_key):
            desired_parent_key = ""
            if ui_parent_key != OUTSIDE_STACK_KEY:
                ui_parent_key = ""
        gp_parent.set_parent_key(node, desired_parent_key)
        item.parent_key = ui_parent_key if ui_parent_key == OUTSIDE_STACK_KEY else desired_parent_key


def _apply_image_parenting(context, stack) -> None:
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bname_image_layers", None) if scene is not None else None
    if coll is None:
        return
    work = get_work(context)
    by_key = {
        str(getattr(item, "key", "") or ""): str(getattr(item, "parent_key", "") or "")
        for item in stack
        if getattr(item, "kind", "") == "image"
    }
    for entry in coll:
        key = str(getattr(entry, "id", "") or "")
        if key not in by_key:
            continue
        parent_key = by_key[key]
        try:
            if parent_key == OUTSIDE_STACK_KEY or not parent_key:
                entry.parent_kind = "none"
                entry.parent_key = ""
            elif gp_parent.parent_key_exists(work, parent_key):
                entry.parent_kind = "coma" if ":" in parent_key else "page"
                entry.parent_key = parent_key
        except Exception:  # noqa: BLE001
            pass


def _apply_raster_parenting(context, stack) -> None:
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bname_raster_layers", None) if scene is not None else None
    if coll is None:
        return
    work = get_work(context)
    by_key = {
        str(getattr(item, "key", "") or ""): str(getattr(item, "parent_key", "") or "")
        for item in stack
        if getattr(item, "kind", "") == "raster"
    }
    for entry in coll:
        key = str(getattr(entry, "id", "") or "")
        if key not in by_key:
            continue
        parent_key = by_key[key]
        try:
            if parent_key == OUTSIDE_STACK_KEY or not parent_key:
                entry.scope = "master"
                entry.parent_kind = "none"
                entry.parent_key = ""
            elif gp_parent.parent_key_exists(work, parent_key):
                entry.scope = "page"
                entry.parent_kind = "coma" if ":" in parent_key else "page"
                entry.parent_key = parent_key
        except Exception:  # noqa: BLE001
            pass


def _apply_balloon_parenting(context, stack) -> None:
    work = get_work(context)
    if work is None:
        return
    for page in getattr(work, "pages", []):
        page_key = page_stack_key(page)
        by_key = {
            split_child_key(str(getattr(item, "key", "") or ""))[1]: str(getattr(item, "parent_key", "") or "")
            for item in stack
            if getattr(item, "kind", "") == "balloon"
            and split_child_key(str(getattr(item, "key", "") or ""))[0] == page_key
        }
        for entry in getattr(page, "balloons", []):
            key = str(getattr(entry, "id", "") or "")
            if key not in by_key:
                continue
            parent_key = by_key[key]
            existing_parent = str(getattr(entry, "parent_key", "") or "")
            fallback_panel = coma_containing_point(page, *entry_center(entry))
            fallback_parent = coma_stack_key(page, fallback_panel) if fallback_panel is not None else page_key
            if not existing_parent and parent_key == fallback_parent:
                continue
            if not parent_key or not gp_parent.parent_key_exists(work, parent_key):
                continue
            entry.parent_kind = "coma" if ":" in parent_key else "page"
            entry.parent_key = parent_key


def _apply_text_parenting(context, stack) -> None:
    work = get_work(context)
    if work is None:
        return
    for page in getattr(work, "pages", []):
        page_key = page_stack_key(page)
        by_key = {
            split_child_key(str(getattr(item, "key", "") or ""))[1]: str(getattr(item, "parent_key", "") or "")
            for item in stack
            if getattr(item, "kind", "") == "text"
            and split_child_key(str(getattr(item, "key", "") or ""))[0] == page_key
        }
        for entry in getattr(page, "texts", []):
            key = str(getattr(entry, "id", "") or "")
            if key not in by_key:
                continue
            parent_key = by_key[key]
            existing_parent = str(getattr(entry, "parent_key", "") or "")
            fallback_panel = coma_containing_point(page, *entry_center(entry))
            fallback_parent = coma_stack_key(page, fallback_panel) if fallback_panel is not None else page_key
            if not existing_parent and parent_key == fallback_parent:
                continue
            if not parent_key or not gp_parent.parent_key_exists(work, parent_key):
                continue
            entry.parent_kind = "coma" if ":" in parent_key else "page"
            entry.parent_key = parent_key


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
            parent_key = str(getattr(item, "parent_key", "") or "")
            native_parent_key = parent_key if _find_gp_group_by_key(groups, parent_key) else ""
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
    _apply_page_coma_orders(context, stack)
    _apply_simple_collection_orders(context, stack)
    gp_obj = gp_utils.get_master_gpencil()
    if gp_obj is not None:
        _apply_gp_parenting(gp_obj, stack, get_work(context))
        _apply_gp_order(gp_obj, stack, effect=False)
    effect_obj = get_effect_gp_object()
    if effect_obj is not None:
        _apply_effect_parenting(effect_obj, stack, get_work(context))
        _apply_gp_order(effect_obj, stack, effect=True)
    _apply_image_parenting(context, stack)
    _apply_raster_parenting(context, stack)
    _apply_balloon_parenting(context, stack)
    _apply_text_parenting(context, stack)
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

    if kind == OUTSIDE_KIND:
        return False
    if kind == PAGE_KIND:
        if not select_stack_index(context, index):
            return False
        try:
            return "FINISHED" in bpy.ops.bname.page_remove("EXEC_DEFAULT")
        except Exception:  # noqa: BLE001
            _logger.exception("delete page from layer stack failed")
            return False
    if kind == COMA_KIND:
        if resolved.get("page") is None:
            work = get_work(context)
            coll = getattr(work, "shared_comas", None) if work is not None else None
            idx = int(resolved.get("index", -1))
            if coll is None or not (0 <= idx < len(coll)):
                return False
            coll.remove(idx)
            sync_layer_stack(context)
            tag_view3d_redraw(context)
            return True
        if not select_stack_index(context, index):
            return False
        try:
            return "FINISHED" in bpy.ops.bname.coma_remove("EXEC_DEFAULT")
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
    elif kind == "raster":
        idx = int(resolved.get("index", -1))
        try:
            from ..operators import raster_layer_op

            if not raster_layer_op.remove_raster_by_index(context, idx):
                return False
        except Exception:  # noqa: BLE001
            _logger.exception("delete raster from layer stack failed")
            return False
    elif kind == "balloon":
        idx = int(resolved.get("index", -1))
        target_page = resolved.get("page") or page
        if target_page is None:
            work = get_work(context)
            coll = getattr(work, "shared_balloons", None) if work is not None else None
            if coll is None or not (0 <= idx < len(coll)):
                return False
            bid = coll[idx].id
            for text in getattr(work, "shared_texts", []):
                if text.parent_balloon_id == bid:
                    text.parent_balloon_id = ""
            coll.remove(idx)
        else:
            if not (0 <= idx < len(target_page.balloons)):
                return False
            bid = target_page.balloons[idx].id
            for text in target_page.texts:
                if text.parent_balloon_id == bid:
                    text.parent_balloon_id = ""
            target_page.balloons.remove(idx)
            target_page.active_balloon_index = min(idx, len(target_page.balloons) - 1) if len(target_page.balloons) else -1
    elif kind == "text":
        idx = int(resolved.get("index", -1))
        target_page = resolved.get("page") or page
        if target_page is None:
            work = get_work(context)
            coll = getattr(work, "shared_texts", None) if work is not None else None
            if coll is None or not (0 <= idx < len(coll)):
                return False
            coll.remove(idx)
        else:
            if not (0 <= idx < len(target_page.texts)):
                return False
            target_page.texts.remove(idx)
            target_page.active_text_index = min(idx, len(target_page.texts) - 1) if len(target_page.texts) else -1
    elif kind == "effect":
        obj = resolved.get("object")
        target = resolved["target"]
        try:
            from ..operators import effect_line_op

            effect_line_op._remove_layer_bounds(obj, target)
        except Exception:  # noqa: BLE001
            _logger.exception("delete effect metadata from layer stack failed")
        try:
            obj.data.layers.remove(target)
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
