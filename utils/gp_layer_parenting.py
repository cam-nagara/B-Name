"""B-Name logical parenting for Grease Pencil layers."""

from __future__ import annotations

import json

import bpy

from .geom import mm_to_m
from .layer_hierarchy import page_stack_key, panel_stack_key, split_child_key

PARENT_KEY_PROP = "bname_parent_key"
PARENT_MAP_PROP = "bname_layer_parent_map_json"
_RUNTIME_PARENT_BY_PTR: dict[int, str] = {}


def _runtime_key(node) -> int:
    try:
        return int(node.as_pointer())
    except Exception:  # noqa: BLE001
        return 0


def _layer_name(node) -> str:
    return str(getattr(node, "name", "") or "")


def _load_parent_map(gp_data) -> dict[str, str]:
    if gp_data is None:
        return {}
    try:
        raw = str(gp_data.get(PARENT_MAP_PROP, "") or "")
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
    return {str(name): str(key) for name, key in data.items() if name and key}


def _save_parent_map(gp_data, data: dict[str, str]) -> None:
    if gp_data is None:
        return
    try:
        if data:
            gp_data[PARENT_MAP_PROP] = json.dumps(data, ensure_ascii=False, sort_keys=True)
        elif PARENT_MAP_PROP in gp_data:
            del gp_data[PARENT_MAP_PROP]
    except Exception:  # noqa: BLE001
        pass


def _owner_data_for_layer(layer):
    if layer is None:
        return None
    for obj in getattr(bpy.data, "objects", []):
        if getattr(obj, "type", "") != "GREASEPENCIL":
            continue
        data = getattr(obj, "data", None)
        layers = getattr(data, "layers", None)
        if layers is None:
            continue
        for candidate in layers:
            if candidate == layer:
                return data
    return None


def _idprop_parent_key(node) -> str:
    try:
        return str(node.get(PARENT_KEY_PROP, "") or "")
    except Exception:  # noqa: BLE001
        return ""


def parent_key(node) -> str:
    """Return the B-Name page/panel parent key stored on a GP layer."""
    if node is None:
        return ""
    runtime = _RUNTIME_PARENT_BY_PTR.get(_runtime_key(node), "")
    if runtime:
        return runtime
    direct = _idprop_parent_key(node)
    if direct:
        return direct
    gp_data = _owner_data_for_layer(node)
    return _load_parent_map(gp_data).get(_layer_name(node), "")


def set_parent_key(node, key: str) -> None:
    """Persist or clear a B-Name page/panel parent key on a GP layer."""
    if node is None:
        return
    value = str(key or "")
    ptr = _runtime_key(node)
    if ptr:
        if value:
            _RUNTIME_PARENT_BY_PTR[ptr] = value
        else:
            _RUNTIME_PARENT_BY_PTR.pop(ptr, None)
    try:
        if value:
            node[PARENT_KEY_PROP] = value
        elif PARENT_KEY_PROP in node:
            del node[PARENT_KEY_PROP]
        if value and _idprop_parent_key(node) == value:
            return
    except Exception:  # noqa: BLE001
        pass
    gp_data = _owner_data_for_layer(node)
    data = _load_parent_map(gp_data)
    name = _layer_name(node)
    if not name:
        return
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    _save_parent_map(gp_data, data)


def is_page_or_panel_key(key: str) -> bool:
    key = str(key or "")
    if not key:
        return False
    page_key, child_key = split_child_key(key)
    return bool(page_key) and (not child_key or key.count(":") == 1)


def parent_depth(key: str) -> int:
    page_key, child_key = split_child_key(key)
    if not page_key:
        return 0
    return 2 if child_key else 1


def parent_keys_for_page(page) -> set[str]:
    keys = {page_stack_key(page)}
    keys.update(panel_stack_key(page, panel) for panel in getattr(page, "panels", []))
    return keys


def parent_key_for_panel(page, panel) -> str:
    return panel_stack_key(page, panel)


def parent_key_exists(work, key: str) -> bool:
    page_key, child_key = split_child_key(key)
    if work is None or not page_key:
        return False
    for page in getattr(work, "pages", []):
        if page_stack_key(page) != page_key:
            continue
        if not child_key:
            return True
        return any(panel_stack_key(page, panel) == key for panel in getattr(page, "panels", []))
    return False


def layer_matches_parent(layer, parent_keys: set[str]) -> bool:
    return parent_key(layer) in parent_keys


def iter_layers_with_parent(obj, parent_keys: set[str]):
    layers = getattr(getattr(obj, "data", None), "layers", None)
    if layers is None or not parent_keys:
        return
    for layer in list(layers):
        if layer_matches_parent(layer, parent_keys):
            yield layer


def iter_points(layer):
    for frame in getattr(layer, "frames", []):
        drawing = getattr(frame, "drawing", None)
        strokes = getattr(drawing, "strokes", []) if drawing is not None else []
        for stroke in strokes:
            for point in getattr(stroke, "points", []):
                yield point


def translate_layer(layer, dx_mm: float, dy_mm: float) -> None:
    dx = mm_to_m(dx_mm)
    dy = mm_to_m(dy_mm)
    for point in iter_points(layer):
        pos = getattr(point, "position", None)
        if pos is None:
            continue
        try:
            point.position = (float(pos[0]) + dx, float(pos[1]) + dy, float(pos[2]))
        except Exception:  # noqa: BLE001
            pass


def capture_layers(layers) -> list[tuple[object, list[tuple[object, tuple[float, float, float]]]]]:
    snapshots = []
    for layer in layers:
        points = []
        for point in iter_points(layer):
            pos = getattr(point, "position", None)
            if pos is None:
                continue
            try:
                points.append((point, (float(pos[0]), float(pos[1]), float(pos[2]))))
            except Exception:  # noqa: BLE001
                pass
        snapshots.append((layer, points))
    return snapshots


def restore_layers(snapshot) -> None:
    for _layer, points in snapshot or []:
        for point, pos in points:
            try:
                point.position = pos
            except Exception:  # noqa: BLE001
                pass
