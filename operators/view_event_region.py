"""3D View の WINDOW 領域だけを対象にするイベント判定ヘルパ."""

from __future__ import annotations


def _contains(region, mouse_x: int, mouse_y: int) -> bool:
    x = int(getattr(region, "x", 0))
    y = int(getattr(region, "y", 0))
    width = int(getattr(region, "width", 0))
    height = int(getattr(region, "height", 0))
    return (
        x <= mouse_x < x + width
        and y <= mouse_y < y + height
    )


def view3d_window_under_event(context, event):
    """イベント位置にある VIEW_3D の WINDOW region を返す.

    N パネルやツールバーなどの非 WINDOW region が同じ座標を覆っている場合は
    None を返し、モーダルツールが UI 操作を奪わないようにする。
    """
    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    mouse_x = int(getattr(event, "mouse_x", -10_000_000))
    mouse_y = int(getattr(event, "mouse_y", -10_000_000))
    for area in getattr(screen, "areas", []):
        if getattr(area, "type", "") != "VIEW_3D":
            continue
        regions = list(getattr(area, "regions", []) or [])
        for region in regions:
            if (
                getattr(region, "type", "") != "WINDOW"
                and _contains(region, mouse_x, mouse_y)
            ):
                return None
        for region in regions:
            if (
                getattr(region, "type", "") != "WINDOW"
                or not _contains(region, mouse_x, mouse_y)
            ):
                continue
            space = getattr(getattr(area, "spaces", None), "active", None)
            rv3d = getattr(space, "region_3d", None)
            if rv3d is None:
                continue
            return area, region, rv3d, mouse_x - int(region.x), mouse_y - int(region.y)
    return None


def is_view3d_window_event(context, event) -> bool:
    return view3d_window_under_event(context, event) is not None
