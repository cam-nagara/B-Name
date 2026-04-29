"""汎用レイヤーフォルダの解決・所属更新ヘルパ."""

from __future__ import annotations

from types import SimpleNamespace

from ..core.work import get_work
from .layer_hierarchy import OUTSIDE_STACK_KEY, outside_child_key, page_stack_key, split_child_key

LAYER_FOLDER_KIND = "layer_folder"
FOLDER_CHILD_KINDS = {"image", "raster", "balloon", "text"}
FOLDER_CONTAINER_CHILD_KINDS = FOLDER_CHILD_KINDS | {LAYER_FOLDER_KIND}


def folder_key(folder) -> str:
    return str(getattr(folder, "id", "") or "")


def folder_parent_key(folder) -> str:
    key = str(getattr(folder, "parent_key", "") or "")
    return key or OUTSIDE_STACK_KEY


def is_folder_child_kind(kind: str) -> bool:
    return str(kind or "") in FOLDER_CHILD_KINDS


def find_folder(work, key: str):
    if work is None or not key:
        return None
    for folder in getattr(work, "layer_folders", []):
        if folder_key(folder) == key:
            return folder
    return None


def folder_exists(work, key: str) -> bool:
    return find_folder(work, key) is not None


def is_folder_key(context, key: str) -> bool:
    return folder_exists(get_work(context), str(key or ""))


def ensure_unique_folder_id(work, prefix: str = "layer_folder") -> str:
    used = {folder_key(folder) for folder in getattr(work, "layer_folders", [])}
    i = 1
    while True:
        candidate = f"{prefix}_{i:04d}"
        if candidate not in used:
            return candidate
        i += 1


def folder_depths(work) -> dict[str, int]:
    folders = {folder_key(folder): folder for folder in getattr(work, "layer_folders", []) if folder_key(folder)}
    memo: dict[str, int] = {}
    visiting: set[str] = set()

    def _depth(key: str) -> int:
        if key in memo:
            return memo[key]
        if key in visiting:
            memo[key] = 1
            return 1
        folder = folders.get(key)
        if folder is None:
            return 0
        visiting.add(key)
        parent_key = folder_parent_key(folder)
        if parent_key in folders:
            depth = _depth(parent_key) + 1
        elif parent_key and parent_key != OUTSIDE_STACK_KEY and ":" in parent_key:
            depth = 2
        else:
            depth = 1
        visiting.discard(key)
        memo[key] = max(1, depth)
        return memo[key]

    for key in folders:
        _depth(key)
    return memo


def folder_has_collapsed_ancestor(work, key: str) -> bool:
    folders = {folder_key(folder): folder for folder in getattr(work, "layer_folders", []) if folder_key(folder)}
    current = str(key or "")
    guard = 0
    while current and guard < 128:
        folder = folders.get(current)
        if folder is None:
            return False
        parent_key = folder_parent_key(folder)
        parent = folders.get(parent_key)
        if parent is not None and not bool(getattr(parent, "expanded", True)):
            return True
        current = parent_key if parent is not None else ""
        guard += 1
    return False


def folder_children_visible(work, key: str) -> bool:
    folder = find_folder(work, key)
    return folder is not None and bool(getattr(folder, "expanded", True)) and not folder_has_collapsed_ancestor(work, key)


def semantic_parent_key_for_folder(work, key: str) -> str:
    folders = {folder_key(folder): folder for folder in getattr(work, "layer_folders", []) if folder_key(folder)}
    current = str(key or "")
    guard = 0
    while current and guard < 128:
        folder = folders.get(current)
        if folder is None:
            return OUTSIDE_STACK_KEY
        parent_key = folder_parent_key(folder)
        if parent_key in folders:
            current = parent_key
            guard += 1
            continue
        return parent_key or OUTSIDE_STACK_KEY
    return OUTSIDE_STACK_KEY


def _find_by_id(coll, key: str):
    for entry in coll or []:
        if str(getattr(entry, "id", "") or "") == key:
            return entry
    return None


def _find_page(work, page_key: str):
    if work is None:
        return None
    for page in getattr(work, "pages", []):
        if page_stack_key(page) == page_key:
            return page
    return None


def _entry_for_kind_key(context, kind: str, key: str):
    scene = getattr(context, "scene", None)
    work = get_work(context)
    kind = str(kind or "")
    key = str(key or "")
    if kind == "image":
        return _find_by_id(getattr(scene, "bname_image_layers", None), key)
    if kind == "raster":
        return _find_by_id(getattr(scene, "bname_raster_layers", None), key)
    if kind in {"balloon", "text"}:
        page_key, child_id = split_child_key(key)
        child_id = child_id or key
        if page_key == OUTSIDE_STACK_KEY:
            coll_name = "shared_balloons" if kind == "balloon" else "shared_texts"
            return _find_by_id(getattr(work, coll_name, []), child_id)
        page = _find_page(work, page_key)
        if page is not None:
            coll_name = "balloons" if kind == "balloon" else "texts"
            return _find_by_id(getattr(page, coll_name, []), child_id)
        for page in getattr(work, "pages", []):
            coll_name = "balloons" if kind == "balloon" else "texts"
            entry = _find_by_id(getattr(page, coll_name, []), child_id)
            if entry is not None:
                return entry
        coll_name = "shared_balloons" if kind == "balloon" else "shared_texts"
        return _find_by_id(getattr(work, coll_name, []), child_id)
    return None


def target_folder_key(context, kind: str, key: str) -> str:
    entry = _entry_for_kind_key(context, kind, key)
    if entry is None:
        return ""
    return str(getattr(entry, "folder_key", "") or "")


def _iter_entries_for_identity(context, kind: str, key: str, preferred_parent_key: str = ""):
    scene = getattr(context, "scene", None)
    work = get_work(context)
    kind = str(kind or "")
    key = str(key or "")
    preferred_parent_key = str(preferred_parent_key or "")
    if kind == "image":
        for entry in getattr(scene, "bname_image_layers", []) or []:
            if str(getattr(entry, "id", "") or "") == key:
                yield entry
        return
    if kind == "raster":
        for entry in getattr(scene, "bname_raster_layers", []) or []:
            if str(getattr(entry, "id", "") or "") == key:
                yield entry
        return
    if kind not in {"balloon", "text"}:
        return
    page_key, child_id = split_child_key(key)
    child_id = child_id or key
    coll_name = "balloons" if kind == "balloon" else "texts"
    shared_name = "shared_balloons" if kind == "balloon" else "shared_texts"
    if page_key and page_key != OUTSIDE_STACK_KEY:
        page = _find_page(work, page_key)
        entry = _find_by_id(getattr(page, coll_name, []) if page is not None else [], child_id)
        if entry is not None:
            yield entry
            return
    if page_key == OUTSIDE_STACK_KEY:
        entry = _find_by_id(getattr(work, shared_name, []), child_id)
        if entry is not None:
            yield entry
            return
    page_candidates = []
    if preferred_parent_key and preferred_parent_key != OUTSIDE_STACK_KEY:
        preferred_page_key, _child = split_child_key(preferred_parent_key)
        page = _find_page(work, preferred_page_key)
        if page is not None:
            page_candidates.append(page)
            entry = _find_by_id(getattr(page, coll_name, []), child_id)
            if entry is not None:
                yield entry
                return
    for page in getattr(work, "pages", []):
        if page not in page_candidates:
            page_candidates.append(page)
    for page in page_candidates:
        entry = _find_by_id(getattr(page, coll_name, []), child_id)
        if entry is not None:
            yield entry
            return
    entry = _find_by_id(getattr(work, shared_name, []), child_id)
    if entry is not None:
        yield entry


def set_item_folder_key(context, item, folder_key_value: str, preferred_parent_key: str = "") -> bool:
    kind = str(getattr(item, "kind", "") or "")
    key = str(getattr(item, "key", "") or "")
    changed = False
    for entry in _iter_entries_for_identity(context, kind, key, preferred_parent_key):
        if not hasattr(entry, "folder_key"):
            continue
        if str(getattr(entry, "folder_key", "") or "") != str(folder_key_value or ""):
            entry.folder_key = str(folder_key_value or "")
            changed = True
    return changed


def assign_item_to_folder(context, item, destination_folder_key: str) -> bool:
    work = get_work(context)
    folder = find_folder(work, destination_folder_key)
    kind = str(getattr(item, "kind", "") or "")
    if folder is None or kind not in FOLDER_CHILD_KINDS:
        return False
    semantic_parent = semantic_parent_key_for_folder(work, destination_folder_key)
    entry = _entry_for_kind_key(context, kind, str(getattr(item, "key", "") or ""))
    current_parent = OUTSIDE_STACK_KEY
    if entry is not None:
        parent_kind = str(getattr(entry, "parent_kind", "") or "")
        parent_key = str(getattr(entry, "parent_key", "") or "")
        if parent_kind != "none" and parent_key:
            current_parent = parent_key
    old_folder_key = target_folder_key(context, kind, str(getattr(item, "key", "") or ""))
    # コピー移送される balloon/text でも所属が引き継がれるよう、先に付与する。
    set_item_folder_key(context, item, destination_folder_key)
    try:
        from . import layer_stack_dnd

        if current_parent != semantic_parent and layer_stack_dnd.is_semantic_parent_key(context, semantic_parent):
            if not layer_stack_dnd.apply_semantic_parent_drop(context, item, semantic_parent):
                set_item_folder_key(context, item, old_folder_key)
                return False
    except Exception:  # noqa: BLE001
        set_item_folder_key(context, item, old_folder_key)
        return False
    return True


def set_folder_parent(context, folder_key_value: str, parent_key: str) -> bool:
    work = get_work(context)
    folder = find_folder(work, folder_key_value)
    if folder is None:
        return False
    parent_key = str(parent_key or "") or OUTSIDE_STACK_KEY
    if parent_key == folder_key_value or folder_is_descendant(work, folder_key_value, parent_key):
        return False
    folder.parent_key = parent_key
    reparent_folder_descendants(context, folder_key_value)
    return True


def folder_is_descendant(work, ancestor_key: str, candidate_key: str) -> bool:
    folders = {folder_key(folder): folder for folder in getattr(work, "layer_folders", []) if folder_key(folder)}
    current = str(candidate_key or "")
    guard = 0
    while current and guard < 128:
        if current == ancestor_key:
            return True
        folder = folders.get(current)
        if folder is None:
            return False
        current = folder_parent_key(folder)
        guard += 1
    return False


def descendant_folder_keys(work, root_key: str) -> set[str]:
    out = {str(root_key or "")}
    changed = True
    while changed:
        changed = False
        for folder in getattr(work, "layer_folders", []):
            key = folder_key(folder)
            if key and key not in out and folder_parent_key(folder) in out:
                out.add(key)
                changed = True
    return {key for key in out if key}


def _stack_key_for_entry(work, kind: str, entry) -> str:
    entry_id = str(getattr(entry, "id", "") or "")
    if kind in {"image", "raster"}:
        return entry_id
    parent_kind = str(getattr(entry, "parent_kind", "") or "")
    parent_key = str(getattr(entry, "parent_key", "") or "")
    if parent_kind == "none" or not parent_key:
        return outside_child_key(entry_id)
    page_key, _child = split_child_key(parent_key)
    if page_key:
        return f"{page_key}:{entry_id}"
    return entry_id


def _iter_folder_member_entries(context, folder_keys: set[str]):
    scene = getattr(context, "scene", None)
    work = get_work(context)
    for entry in getattr(scene, "bname_image_layers", []) or []:
        if str(getattr(entry, "folder_key", "") or "") in folder_keys:
            yield "image", entry
    for entry in getattr(scene, "bname_raster_layers", []) or []:
        if str(getattr(entry, "folder_key", "") or "") in folder_keys:
            yield "raster", entry
    for entry in getattr(work, "shared_balloons", []) or []:
        if str(getattr(entry, "folder_key", "") or "") in folder_keys:
            yield "balloon", entry
    for entry in getattr(work, "shared_texts", []) or []:
        if str(getattr(entry, "folder_key", "") or "") in folder_keys:
            yield "text", entry
    for page in getattr(work, "pages", []):
        for entry in getattr(page, "balloons", []):
            if str(getattr(entry, "folder_key", "") or "") in folder_keys:
                yield "balloon", entry
        for entry in getattr(page, "texts", []):
            if str(getattr(entry, "folder_key", "") or "") in folder_keys:
                yield "text", entry


def reparent_folder_descendants(context, root_folder_key: str) -> int:
    work = get_work(context)
    if work is None:
        return 0
    semantic_parent = semantic_parent_key_for_folder(work, root_folder_key)
    folder_keys = descendant_folder_keys(work, root_folder_key)
    changed = 0
    try:
        from . import layer_stack_dnd
    except Exception:  # noqa: BLE001
        return 0
    if not layer_stack_dnd.is_semantic_parent_key(context, semantic_parent):
        return 0
    for kind, entry in list(_iter_folder_member_entries(context, folder_keys)):
        stack_key = _stack_key_for_entry(work, kind, entry)
        item = SimpleNamespace(kind=kind, key=stack_key, parent_key=semantic_parent)
        if layer_stack_dnd.apply_semantic_parent_drop(context, item, semantic_parent):
            changed += 1
    return changed


def remove_folder_preserve_children(work, key: str) -> bool:
    coll = getattr(work, "layer_folders", None) if work is not None else None
    if coll is None:
        return False
    idx = -1
    target_parent = OUTSIDE_STACK_KEY
    for i, folder in enumerate(coll):
        if folder_key(folder) == key:
            idx = i
            target_parent = folder_parent_key(folder)
            break
    if idx < 0:
        return False
    for folder in coll:
        if folder_parent_key(folder) == key:
            folder.parent_key = target_parent
    for _kind, entry in list(_iter_entries_from_work(work)):
        if str(getattr(entry, "folder_key", "") or "") == key:
            entry.folder_key = ""
    coll.remove(idx)
    return True


def _iter_entries_from_work(work):
    scene = getattr(work, "id_data", None)
    for entry in getattr(scene, "bname_image_layers", []) or []:
        yield "image", entry
    for entry in getattr(scene, "bname_raster_layers", []) or []:
        yield "raster", entry
    for entry in getattr(work, "shared_balloons", []) or []:
        yield "balloon", entry
    for entry in getattr(work, "shared_texts", []) or []:
        yield "text", entry
    for page in getattr(work, "pages", []):
        for entry in getattr(page, "balloons", []):
            yield "balloon", entry
        for entry in getattr(page, "texts", []):
            yield "text", entry
