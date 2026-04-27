"""B-Name page browser workspace/area helpers."""

from __future__ import annotations

from typing import Iterable

import bpy

WORKSPACE_NAME = "B-Name Pages"
WORKSPACE_PROP = "bname_page_browser_workspace"
WORKSPACE_POSITION_PROP = "bname_page_browser_position"

POSITION_ITEMS = (
    ("LEFT", "左", "ページ一覧を左側に表示"),
    ("RIGHT", "右", "ページ一覧を右側に表示"),
    ("TOP", "上", "ページ一覧を上側に表示"),
    ("BOTTOM", "下", "ページ一覧を下側に表示"),
)

_PAGE_BROWSER_AREAS: set[int] = set()


def area_key(area) -> int:
    try:
        return int(area.as_pointer())
    except Exception:  # noqa: BLE001
        return 0


def mark_area(area) -> None:
    key = area_key(area)
    if key:
        _PAGE_BROWSER_AREAS.add(key)


def clear_missing_areas(screen) -> None:
    live = set()
    wm = getattr(bpy.context, "window_manager", None)
    windows = getattr(wm, "windows", ()) if wm is not None else ()
    for window in windows:
        live.update(area_key(area) for area in getattr(getattr(window, "screen", None), "areas", []))
    if not live:
        live = {area_key(area) for area in getattr(screen, "areas", [])}
    _PAGE_BROWSER_AREAS.intersection_update(key for key in live if key)


def clear_screen_marks(screen) -> None:
    keys = {area_key(area) for area in getattr(screen, "areas", [])}
    _PAGE_BROWSER_AREAS.difference_update(key for key in keys if key)


def mark_workspace(workspace, position: str) -> None:
    if workspace is None:
        return
    try:
        workspace[WORKSPACE_PROP] = True
        workspace[WORKSPACE_POSITION_PROP] = normalize_position(position)
    except Exception:  # noqa: BLE001
        pass


def normalize_position(position: str) -> str:
    value = str(position or "LEFT").upper()
    return value if value in {"LEFT", "RIGHT", "TOP", "BOTTOM"} else "LEFT"


def workspace_position(workspace, fallback: str = "LEFT") -> str:
    if workspace is None:
        return normalize_position(fallback)
    try:
        return normalize_position(workspace.get(WORKSPACE_POSITION_PROP, fallback))
    except Exception:  # noqa: BLE001
        return normalize_position(fallback)


def is_page_browser_workspace(workspace) -> bool:
    if workspace is None:
        return False
    try:
        return bool(workspace.get(WORKSPACE_PROP, False))
    except Exception:  # noqa: BLE001
        return False


def view3d_areas(screen) -> list[object]:
    return [area for area in getattr(screen, "areas", []) if getattr(area, "type", "") == "VIEW_3D"]


def marked_view3d_areas(screen) -> list[object]:
    clear_missing_areas(screen)
    marked = []
    for area in view3d_areas(screen):
        if area_key(area) in _PAGE_BROWSER_AREAS:
            marked.append(area)
    return marked


def is_marked_area(area) -> bool:
    return area_key(area) in _PAGE_BROWSER_AREAS


def _edge_value(area, position: str) -> float:
    if position == "LEFT":
        return -float(getattr(area, "x", 0))
    if position == "RIGHT":
        return float(getattr(area, "x", 0)) + float(getattr(area, "width", 0))
    if position == "TOP":
        return float(getattr(area, "y", 0)) + float(getattr(area, "height", 0))
    return -float(getattr(area, "y", 0))


def edge_view3d_area(screen, position: str):
    areas = view3d_areas(screen)
    if not areas:
        return None
    pos = normalize_position(position)
    return max(areas, key=lambda area: (_edge_value(area, pos), getattr(area, "width", 0) * getattr(area, "height", 0)))


def page_browser_area(context=None):
    ctx = context or bpy.context
    screen = getattr(ctx, "screen", None)
    if screen is None:
        return None
    marked = marked_view3d_areas(screen)
    if marked:
        return marked[0]
    workspace = getattr(ctx, "workspace", None)
    if not is_page_browser_workspace(workspace):
        return None
    return edge_view3d_area(screen, workspace_position(workspace))


def is_page_browser_area(context=None) -> bool:
    ctx = context or bpy.context
    area = getattr(ctx, "area", None)
    if area is None or getattr(area, "type", "") != "VIEW_3D":
        return False
    if area_key(area) in _PAGE_BROWSER_AREAS:
        return True
    return page_browser_area(ctx) == area


def iter_page_browser_areas(context=None) -> Iterable[object]:
    ctx = context or bpy.context
    wm = getattr(ctx, "window_manager", None)
    if wm is None:
        return ()
    areas = []
    for window in getattr(wm, "windows", []):
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        marked = marked_view3d_areas(screen)
        areas.extend(marked)
        workspace = getattr(window, "workspace", None)
        if is_page_browser_workspace(workspace):
            inferred = edge_view3d_area(screen, workspace_position(workspace))
            if inferred is not None and inferred not in areas:
                areas.append(inferred)
    return tuple(areas)


def tag_page_browser_redraw(context=None) -> None:
    for area in iter_page_browser_areas(context):
        try:
            area.tag_redraw()
        except Exception:  # noqa: BLE001
            pass
