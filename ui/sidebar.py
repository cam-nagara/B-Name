"""VIEW_3D サイドバーの B-Name タブ表示補助."""

from __future__ import annotations

import bpy

from ..utils import page_browser

B_NAME_CATEGORY = "B-Name"


def open_bname_sidebar(context=None) -> int:
    """全 VIEW_3D でサイドバーを開き、可能なら B-Name タブを選択する."""
    ctx = context or bpy.context
    wm = getattr(ctx, "window_manager", None)
    if wm is None:
        return 0
    changed = 0
    for window in getattr(wm, "windows", []):
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in getattr(screen, "areas", []):
            if area.type != "VIEW_3D":
                continue
            if page_browser.is_page_browser_area_for_window(window, area):
                page_browser.apply_page_browser_view_settings(area)
                continue
            for space in getattr(area, "spaces", []):
                if space.type != "VIEW_3D":
                    continue
                try:
                    if not bool(getattr(space, "show_region_ui", False)):
                        space.show_region_ui = True
                        changed += 1
                except Exception:  # noqa: BLE001
                    pass
            _select_bname_category(area)
            try:
                area.tag_redraw()
            except Exception:  # noqa: BLE001
                pass
    return changed


def schedule_open_bname_sidebar(retries: int = 8, interval: float = 0.15) -> None:
    """ファイルロード後に UI area が再構築されるまで複数回サイドバーを開く."""
    state = {"left": max(1, int(retries))}

    def _tick():
        try:
            open_bname_sidebar(bpy.context)
        except Exception:  # noqa: BLE001
            pass
        state["left"] -= 1
        return interval if state["left"] > 0 else None

    try:
        bpy.app.timers.register(_tick, first_interval=interval)
    except Exception:  # noqa: BLE001
        pass


def _select_bname_category(area) -> None:
    for region in getattr(area, "regions", []):
        if region.type != "UI":
            continue
        try:
            region.active_panel_category = B_NAME_CATEGORY
        except Exception:  # noqa: BLE001
            pass
