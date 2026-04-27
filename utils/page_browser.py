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
    from . import page_grid

    paper = getattr(work, "paper", None)
    start_side = getattr(paper, "start_side", "right")
    read_direction = getattr(paper, "read_direction", "left")
    slots = [
        int(page_grid._logical_slot_index(i, start_side, read_direction))
        for i, _page in enumerate(work.pages)
    ]
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
    from . import page_grid

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
        ox, oy = page_offset_mm(work, scene, area, i)
        min_x = ox if min_x is None else min(min_x, ox)
        min_y = oy if min_y is None else min(min_y, oy)
        max_x = ox + cw if max_x is None else max(max_x, ox + cw)
        max_y = oy + ch if max_y is None else max(max_y, oy + ch)

    if min_x is None or min_y is None or max_x is None or max_y is None:
        return None
    return min_x, min_y, max_x - min_x, max_y - min_y
