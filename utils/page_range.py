"""Helpers for keeping page-number range and actual pages in sync."""

from __future__ import annotations

from pathlib import Path

from ..io import page_io, work_io
from . import gpencil as gp_utils
from . import layer_stack as layer_stack_utils
from . import log, page_grid

_logger = log.get_logger(__name__)


def desired_page_count(work) -> int:
    info = getattr(work, "work_info", None)
    if info is None:
        return max(0, len(getattr(work, "pages", [])))
    start = int(getattr(info, "page_number_start", 1))
    end = int(getattr(info, "page_number_end", start))
    return max(1, end - start + 1)


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
    sync_end_number_to_existing_pages(work)
    desired = desired_page_count(work)
    current = len(work.pages)
    if current >= desired:
        return 0

    work_dir = Path(work.work_dir)
    created = 0
    previous_active = int(getattr(work, "active_page_index", -1))
    try:
        from ..operators.panel_op import create_basic_frame_panel

        for _ in range(desired - current):
            entry = page_io.register_new_page(work)
            page_io.ensure_page_dir(work_dir, entry.id)
            create_basic_frame_panel(work, entry, work_dir)
            gp_utils.ensure_page_gpencil(context.scene, entry.id)
            created += 1
        if 0 <= previous_active < len(work.pages):
            work.active_page_index = previous_active
        page_grid.apply_page_collection_transforms(context, work)
        page_io.save_pages_json(work_dir, work)
        work_io.save_work_json(work_dir, work)
        layer_stack_utils.sync_layer_stack_after_data_change(
            context,
            align_page_order=True,
            align_panel_order=True,
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
