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


_NAVIGATION_UI_HITBOX_WIDTH_PX = 112
_NAVIGATION_UI_HITBOX_HEIGHT_PX = 232
_NAVIGATION_UI_HITBOX_MARGIN_PX = 8
_MOUSE_EVENT_TYPES = {
    "LEFTMOUSE",
    "MIDDLEMOUSE",
    "RIGHTMOUSE",
    "MOUSEMOVE",
    "WHEELUPMOUSE",
    "WHEELDOWNMOUSE",
    "WHEELINMOUSE",
    "WHEELOUTMOUSE",
}


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


def _navigation_ui_visible(context, area) -> bool:
    prefs_view = getattr(getattr(context, "preferences", None), "view", None)
    if prefs_view is not None and not bool(getattr(prefs_view, "show_navigate_ui", True)):
        return False
    space = getattr(getattr(area, "spaces", None), "active", None)
    if space is None:
        return True
    if not bool(getattr(space, "show_gizmo", True)):
        return False
    return bool(getattr(space, "show_gizmo_navigate", True))


def is_view3d_navigation_ui_event(context, event) -> bool:
    """Return True when a mouse event is over Blender's top-right navigation UI.

    Modal B-Name tools run while the user keeps working in the viewport. Without
    this guard they also consume clicks on Blender's navigation gizmo and the
    zoom/pan buttons because those controls live inside the VIEW_3D WINDOW region.
    """
    event_type = str(getattr(event, "type", "") or "")
    if event_type not in _MOUSE_EVENT_TYPES:
        return False
    view = view3d_window_under_event(context, event)
    if view is None:
        return False
    area, region, _rv3d, mouse_x, mouse_y = view
    if not _navigation_ui_visible(context, area):
        return False
    return (
        int(mouse_x)
        >= int(region.width) - _NAVIGATION_UI_HITBOX_WIDTH_PX - _NAVIGATION_UI_HITBOX_MARGIN_PX
        and int(mouse_y)
        >= int(region.height) - _NAVIGATION_UI_HITBOX_HEIGHT_PX - _NAVIGATION_UI_HITBOX_MARGIN_PX
    )


def modal_navigation_ui_passthrough(modal_operator, context, event) -> bool:
    """Return True while a modal tool should yield to viewport navigation UI.

    Navigation buttons keep handling the drag after the initial press. The mouse
    can leave the top-right hitbox during that drag, so the modal tool must keep
    passing events through until the corresponding left-button release.
    """
    if bool(getattr(modal_operator, "_navigation_drag_passthrough", False)):
        event_type = str(getattr(event, "type", "") or "")
        event_value = str(getattr(event, "value", "") or "")
        if event_type == "LEFTMOUSE" and event_value == "RELEASE":
            setattr(modal_operator, "_navigation_drag_passthrough", False)
        return True
    if not is_view3d_navigation_ui_event(context, event):
        return False
    event_type = str(getattr(event, "type", "") or "")
    event_value = str(getattr(event, "value", "") or "")
    if event_type == "LEFTMOUSE" and event_value == "PRESS":
        setattr(modal_operator, "_navigation_drag_passthrough", True)
    return True
