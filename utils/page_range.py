"""Helpers for keeping page-number range and actual pages in sync."""

from __future__ import annotations

import json
from pathlib import Path

from ..io import page_io, work_io
from . import gp_layer_parenting as gp_parent
from . import gpencil as gp_utils
from . import layer_stack as layer_stack_utils
from . import log, page_grid
from .layer_hierarchy import page_stack_key, split_child_key

_logger = log.get_logger(__name__)
_RANGE_HIDE_MAP_PROP = "bname_range_hide_original_map_json"


def desired_page_count(work) -> int:
    info = getattr(work, "work_info", None)
    if info is None:
        return max(0, len(getattr(work, "pages", [])))
    start = int(getattr(info, "page_number_start", 1))
    end = int(getattr(info, "page_number_end", start))
    return max(1, end - start + 1)


def page_in_range(page) -> bool:
    """Return whether a page is inside the work-info start/end page range."""
    return bool(getattr(page, "in_page_range", True))


def page_visible_in_work(page) -> bool:
    """Return effective page visibility, including user eye state and range state."""
    return bool(getattr(page, "visible", True)) and page_in_range(page)


def iter_in_range_pages(work):
    """Yield ``(index, page)`` for pages currently included in the page range."""
    if work is None:
        return
    for index, page in enumerate(getattr(work, "pages", [])):
        if page_in_range(page):
            yield index, page


def in_range_page_count(work) -> int:
    return sum(1 for _index, _page in iter_in_range_pages(work))


def _clamp_active_page_to_range(work) -> bool:
    pages = getattr(work, "pages", [])
    if len(pages) == 0:
        changed = int(getattr(work, "active_page_index", -1)) != -1
        work.active_page_index = -1
        return changed
    active = int(getattr(work, "active_page_index", -1))
    if 0 <= active < len(pages) and page_in_range(pages[active]):
        return False
    for index, page in iter_in_range_pages(work):
        work.active_page_index = index
        return active != index
    work.active_page_index = -1
    return active != -1


def update_page_range_visibility(work) -> bool:
    """Sync page range flags to work-info count without deleting existing pages."""
    if work is None:
        return False
    desired = desired_page_count(work)
    changed = False
    for index, page in enumerate(getattr(work, "pages", [])):
        in_range = index < desired
        if hasattr(page, "in_page_range") and bool(page.in_page_range) != in_range:
            page.in_page_range = in_range
            changed = True
    if _clamp_active_page_to_range(work):
        changed = True
    if _apply_range_visibility_to_gp_layers(work):
        changed = True
    return changed


def _load_range_hide_map(gp_data) -> dict[str, bool]:
    try:
        raw = str(gp_data.get(_RANGE_HIDE_MAP_PROP, "") or "")
    except Exception:  # noqa: BLE001
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): bool(value) for key, value in data.items() if key}


def _save_range_hide_map(gp_data, data: dict[str, bool]) -> None:
    try:
        if data:
            gp_data[_RANGE_HIDE_MAP_PROP] = json.dumps(data, ensure_ascii=False, sort_keys=True)
        elif _RANGE_HIDE_MAP_PROP in gp_data:
            del gp_data[_RANGE_HIDE_MAP_PROP]
    except Exception:  # noqa: BLE001
        pass


def _layer_hide_key(layer) -> str:
    return str(getattr(layer, "name", "") or "")


def _restore_range_hidden_state(layer, state: dict[str, bool]) -> bool:
    key = _layer_hide_key(layer)
    if not key or key not in state:
        return False
    try:
        layer.hide = bool(state.pop(key))
    except Exception:  # noqa: BLE001
        state.pop(key, None)
    return True


def _range_hide_layer(layer, state: dict[str, bool]) -> bool:
    key = _layer_hide_key(layer)
    if not key:
        return False
    changed = False
    if key not in state:
        state[key] = bool(getattr(layer, "hide", False))
        changed = True
    if not bool(getattr(layer, "hide", False)):
        try:
            layer.hide = True
            changed = True
        except Exception:  # noqa: BLE001
            pass
    return changed


def _apply_range_visibility_to_gp_layers(work) -> bool:
    """Hide GP layers parented to out-of-range pages without losing user hide state."""
    obj = gp_utils.get_master_gpencil()
    gp_data = getattr(obj, "data", None)
    layers = getattr(gp_data, "layers", None)
    if layers is None:
        return False
    state = _load_range_hide_map(gp_data)
    changed = False
    visible_page_keys = {
        page_stack_key(page)
        for page in getattr(work, "pages", [])
        if page_in_range(page)
    }
    known_layer_keys = set()
    for layer in list(layers):
        key = _layer_hide_key(layer)
        if key:
            known_layer_keys.add(key)
        parent_key = gp_parent.parent_key(layer)
        if not parent_key:
            changed = _restore_range_hidden_state(layer, state) or changed
            continue
        page_key, _child_key = split_child_key(parent_key)
        if page_key and page_key not in visible_page_keys:
            changed = _range_hide_layer(layer, state) or changed
        else:
            changed = _restore_range_hidden_state(layer, state) or changed
    stale_keys = set(state) - known_layer_keys
    if stale_keys:
        for key in stale_keys:
            state.pop(key, None)
        changed = True
    if changed:
        _save_range_hide_map(gp_data, state)
    return changed


def sync_end_number_to_existing_pages(work) -> None:
    """Make end number cover all existing pages without deleting anything."""
    info = getattr(work, "work_info", None)
    if info is None:
        return
    start = max(0, int(getattr(info, "page_number_start", 1)))
    count = max(1, len(getattr(work, "pages", [])))
    min_end = start + count - 1
    if int(getattr(info, "page_number_end", start)) < min_end:
        info.page_number_end = min_end


def sync_end_number_to_page_count(work) -> None:
    """Set end number so the current start/end range matches existing pages."""
    info = getattr(work, "work_info", None)
    if info is None:
        return
    count = len(getattr(work, "pages", []))
    if count <= 0:
        return
    start = max(0, int(getattr(info, "page_number_start", 1)))
    end = start + count - 1
    if int(getattr(info, "page_number_end", start)) != end:
        info.page_number_end = end
    update_page_range_visibility(work)


def ensure_pages_for_number_range(context) -> int:
    """Create missing pages for the current start/end range. Never removes pages."""
    from ..core.work import get_work

    work = get_work(context)
    if not (work and getattr(work, "loaded", False) and getattr(work, "work_dir", "")):
        return 0
    try:
        from ..core.mode import MODE_PAGE, get_mode

        if get_mode(context) != MODE_PAGE:
            return 0
    except Exception:  # noqa: BLE001
        return 0
    desired = desired_page_count(work)
    current = len(work.pages)
    range_changed = update_page_range_visibility(work)

    work_dir = Path(work.work_dir)
    created = 0
    previous_active = int(getattr(work, "active_page_index", -1))
    try:
        from ..operators.coma_op import create_basic_frame_coma

        for _ in range(max(0, desired - current)):
            entry = page_io.register_new_page(work)
            if hasattr(entry, "in_page_range"):
                entry.in_page_range = True
            page_io.ensure_page_dir(work_dir, entry.id)
            create_basic_frame_coma(work, entry, work_dir)
            gp_utils.ensure_page_gpencil(context.scene, entry.id)
            created += 1
        range_changed = update_page_range_visibility(work) or range_changed
        if (
            0 <= previous_active < len(work.pages)
            and page_in_range(work.pages[previous_active])
        ):
            work.active_page_index = previous_active
        else:
            _clamp_active_page_to_range(work)
        if created == 0 and not range_changed:
            return 0
        page_grid.apply_page_collection_transforms(context, work)
        page_io.save_pages_json(work_dir, work)
        work_io.save_work_json(work_dir, work)
        layer_stack_utils.sync_layer_stack_after_data_change(
            context,
            align_page_order=True,
            align_coma_order=True,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("ensure_pages_for_number_range failed")
    try:
        for area in getattr(context, "screen", None).areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass
    return created
