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
_SPACE_VIEW_STATES: dict[int, dict[str, object]] = {}
_SPACE_BOOL_PROPS = (
    "show_region_toolbar",
    "show_region_ui",
    "show_region_tool_header",
    "show_region_asset_shelf",
    "show_region_hud",
    "show_region_header",
    "show_gizmo",
    "show_gizmo_navigate",
)


def area_key(area) -> int:
    try:
        return int(area.as_pointer())
    except Exception:  # noqa: BLE001
        return 0


def _space_key(space) -> int:
    try:
        return int(space.as_pointer())
    except Exception:  # noqa: BLE001
        return 0


def _remember_space_state(space) -> dict[str, object]:
    key = _space_key(space)
    if key and key in _SPACE_VIEW_STATES:
        return _SPACE_VIEW_STATES[key]
    state: dict[str, object] = {}
    for prop in _SPACE_BOOL_PROPS:
        if hasattr(space, prop):
            try:
                state[prop] = bool(getattr(space, prop))
            except Exception:  # noqa: BLE001
                pass
    overlay = getattr(space, "overlay", None)
    if overlay is not None and hasattr(overlay, "show_overlays"):
        try:
            state["overlay.show_overlays"] = bool(overlay.show_overlays)
        except Exception:  # noqa: BLE001
            pass
    rv3d = getattr(space, "region_3d", None)
    if rv3d is not None and hasattr(rv3d, "view_perspective"):
        try:
            state["region_3d.view_perspective"] = str(rv3d.view_perspective)
        except Exception:  # noqa: BLE001
            pass
    if hasattr(space, "lock_camera"):
        try:
            state["lock_camera"] = bool(space.lock_camera)
        except Exception:  # noqa: BLE001
            pass
    shading = getattr(space, "shading", None)
    if shading is not None:
        for prop in ("type", "light", "background_type"):
            if not hasattr(shading, prop):
                continue
            try:
                state[f"shading.{prop}"] = str(getattr(shading, prop))
            except Exception:  # noqa: BLE001
                pass
    if key:
        _SPACE_VIEW_STATES[key] = state
    return state


def apply_page_browser_view_settings(area) -> None:
    """ページ一覧ビュー専用の3Dビュー表示設定を適用する."""
    if area is None or getattr(area, "type", "") != "VIEW_3D":
        return
    for space in getattr(area, "spaces", []):
        if getattr(space, "type", "") != "VIEW_3D":
            continue
        _remember_space_state(space)
        for prop in _SPACE_BOOL_PROPS:
            if not hasattr(space, prop):
                continue
            try:
                setattr(space, prop, False)
            except Exception:  # noqa: BLE001
                pass
        overlay = getattr(space, "overlay", None)
        if overlay is not None and hasattr(overlay, "show_overlays"):
            try:
                overlay.show_overlays = False
            except Exception:  # noqa: BLE001
                pass
        if hasattr(space, "lock_camera"):
            try:
                space.lock_camera = False
            except Exception:  # noqa: BLE001
                pass
        rv3d = getattr(space, "region_3d", None)
        if rv3d is not None:
            try:
                rv3d.view_perspective = "ORTHO"
            except Exception:  # noqa: BLE001
                pass
        shading = getattr(space, "shading", None)
        if shading is not None:
            try:
                shading.type = "SOLID"
            except Exception:  # noqa: BLE001
                pass
            try:
                shading.light = "FLAT"
            except Exception:  # noqa: BLE001
                pass
            try:
                shading.background_type = "THEME"
            except Exception:  # noqa: BLE001
                pass
    try:
        area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass


def restore_page_browser_view_settings(area) -> None:
    if area is None or getattr(area, "type", "") != "VIEW_3D":
        return
    for space in getattr(area, "spaces", []):
        if getattr(space, "type", "") != "VIEW_3D":
            continue
        key = _space_key(space)
        state = _SPACE_VIEW_STATES.pop(key, None) if key else None
        if not state:
            continue
        for prop in _SPACE_BOOL_PROPS:
            if prop not in state or not hasattr(space, prop):
                continue
            try:
                setattr(space, prop, bool(state[prop]))
            except Exception:  # noqa: BLE001
                pass
        overlay = getattr(space, "overlay", None)
        if overlay is not None and "overlay.show_overlays" in state:
            try:
                overlay.show_overlays = bool(state["overlay.show_overlays"])
            except Exception:  # noqa: BLE001
                pass
        rv3d = getattr(space, "region_3d", None)
        if rv3d is not None and "region_3d.view_perspective" in state:
            try:
                rv3d.view_perspective = str(state["region_3d.view_perspective"])
            except Exception:  # noqa: BLE001
                pass
        if hasattr(space, "lock_camera") and "lock_camera" in state:
            try:
                space.lock_camera = bool(state["lock_camera"])
            except Exception:  # noqa: BLE001
                pass
        shading = getattr(space, "shading", None)
        if shading is not None:
            for prop in ("type", "light", "background_type"):
                key_name = f"shading.{prop}"
                if key_name not in state or not hasattr(shading, prop):
                    continue
                try:
                    setattr(shading, prop, str(state[key_name]))
                except Exception:  # noqa: BLE001
                    pass
    try:
        area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass


def _area_has_remembered_space(area) -> bool:
    for space in getattr(area, "spaces", []):
        key = _space_key(space)
        if key and key in _SPACE_VIEW_STATES:
            return True
    return False


def restore_all_view_settings() -> None:
    wm = getattr(bpy.context, "window_manager", None)
    windows = getattr(wm, "windows", ()) if wm is not None else ()
    for window in windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in getattr(screen, "areas", []):
            if _area_has_remembered_space(area):
                restore_page_browser_view_settings(area)
    _SPACE_VIEW_STATES.clear()
    _PAGE_BROWSER_AREAS.clear()


def mark_area(area) -> None:
    key = area_key(area)
    if key:
        _PAGE_BROWSER_AREAS.add(key)
        apply_page_browser_view_settings(area)


def clear_missing_areas(screen) -> None:
    live = set()
    wm = getattr(bpy.context, "window_manager", None)
    windows = getattr(wm, "windows", ()) if wm is not None else ()
    for window in windows:
        live.update(area_key(area) for area in getattr(getattr(window, "screen", None), "areas", []))
    if not live:
        live = {area_key(area) for area in getattr(screen, "areas", [])}
    _PAGE_BROWSER_AREAS.intersection_update(key for key in live if key)
    live_spaces = {
        key
        for window in windows
        for area in getattr(getattr(window, "screen", None), "areas", [])
        for space in getattr(area, "spaces", [])
        if (key := _space_key(space))
    }
    if not live_spaces:
        live_spaces = {
            key
            for area in getattr(screen, "areas", [])
            for space in getattr(area, "spaces", [])
            if (key := _space_key(space))
        }
    for key in tuple(_SPACE_VIEW_STATES):
        if key not in live_spaces:
            _SPACE_VIEW_STATES.pop(key, None)


def clear_screen_marks(screen) -> None:
    areas = tuple(getattr(screen, "areas", []))
    keys = {area_key(area) for area in areas}
    for area in areas:
        if area_key(area) in _PAGE_BROWSER_AREAS or _area_has_remembered_space(area):
            restore_page_browser_view_settings(area)
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


def is_page_browser_area_for_window(window, area) -> bool:
    if area is None or getattr(area, "type", "") != "VIEW_3D":
        return False
    if is_marked_area(area):
        return True
    workspace = getattr(window, "workspace", None)
    screen = getattr(window, "screen", None)
    if not is_page_browser_workspace(workspace) or screen is None:
        return False
    inferred = edge_view3d_area(screen, workspace_position(workspace))
    return inferred == area


def tag_page_browser_redraw(context=None) -> None:
    for area in iter_page_browser_areas(context):
        try:
            area.tag_redraw()
        except Exception:  # noqa: BLE001
            pass


def fit_enabled(scene=None) -> bool:
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return True
    return bool(getattr(scene, "bname_page_browser_fit", True))


def is_vertical_area(area) -> bool:
    if area is None:
        return True
    width = max(1, int(getattr(area, "width", 1)))
    height = max(1, int(getattr(area, "height", 1)))
    return height >= width


def _page_slots(work) -> tuple[int, int]:
    if work is None or len(getattr(work, "pages", [])) == 0:
        return 0, 0
    from . import page_grid, page_range

    paper = getattr(work, "paper", None)
    start_side = getattr(paper, "start_side", "right")
    read_direction = getattr(paper, "read_direction", "left")
    slots = [
        int(page_grid._logical_slot_index(i, start_side, read_direction))
        for i, _page in enumerate(work.pages)
        if page_range.page_in_range(_page)
    ]
    if not slots:
        return 0, 0
    return min(slots), max(slots)


def layout_cols_for_area(work, area, scene=None) -> int:
    """ページ一覧ビュー用の列数を返す.

    フィット OFF なら通常の全ページ一覧列数、フィット ON かつ縦長なら
    見開き 1 組ごとに改行する 2 列、横長なら全ページを 1 行に並べる。
    """
    scene = scene or getattr(bpy.context, "scene", None)
    if not fit_enabled(scene):
        return max(1, int(getattr(scene, "bname_overview_cols", 4)))
    paper = getattr(work, "paper", None)
    if getattr(paper, "read_direction", "left") == "down":
        return 1
    if is_vertical_area(area):
        return 2
    _min_slot, max_slot = _page_slots(work)
    return max(2, max_slot + 1)


def page_offset_mm(work, scene, area, page_index: int) -> tuple[float, float]:
    """ページ一覧ビュー上の page_index の offset (mm) を返す."""
    from . import page_grid

    if work is None or scene is None or not (0 <= page_index < len(work.pages)):
        return 0.0, 0.0
    paper = work.paper
    cols = layout_cols_for_area(work, area, scene)
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    start_side = getattr(paper, "start_side", "right")
    read_direction = getattr(paper, "read_direction", "left")
    ox, oy = page_grid.page_grid_offset_mm(
        page_index,
        cols,
        gap,
        float(paper.canvas_width_mm),
        float(paper.canvas_height_mm),
        start_side,
        read_direction,
    )
    add_x, add_y = page_grid.page_manual_offset_mm(work.pages[page_index])
    return ox + add_x, oy + add_y


def layout_bbox_mm(work, scene, area) -> tuple[float, float, float, float] | None:
    """ページ一覧ビューの表示対象 bbox (x, y, w, h) を mm で返す."""
    if work is None or scene is None or len(getattr(work, "pages", [])) == 0:
        return None
    from . import page_grid, page_range

    paper = work.paper
    cw = float(paper.canvas_width_mm)
    ch = float(paper.canvas_height_mm)
    gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
    start_side = getattr(paper, "start_side", "right")
    read_direction = getattr(paper, "read_direction", "left")
    cols = layout_cols_for_area(work, area, scene)
    min_x = min_y = max_x = max_y = None

    if fit_enabled(scene):
        _min_slot, max_slot = _page_slots(work)
        slot_count = max(1, max_slot + 1)
        if read_direction != "down" and is_vertical_area(area):
            slot_count = max(2, ((slot_count + 1) // 2) * 2)
        for slot in range(slot_count):
            ox, oy = page_grid.slot_grid_offset_mm(slot, cols, gap, cw, ch, read_direction)
            min_x = ox if min_x is None else min(min_x, ox)
            min_y = oy if min_y is None else min(min_y, oy)
            max_x = ox + cw if max_x is None else max(max_x, ox + cw)
            max_y = oy + ch if max_y is None else max(max_y, oy + ch)

    for i, _page in enumerate(work.pages):
        if not page_range.page_in_range(_page):
            continue
        ox, oy = page_offset_mm(work, scene, area, i)
        min_x = ox if min_x is None else min(min_x, ox)
        min_y = oy if min_y is None else min(min_y, oy)
        max_x = ox + cw if max_x is None else max(max_x, ox + cw)
        max_y = oy + ch if max_y is None else max(max_y, oy + ch)

    if min_x is None or min_y is None or max_x is None or max_y is None:
        return None
    return min_x, min_y, max_x - min_x, max_y - min_y
