"""レイヤースタック UIList D&D の親変更ヘルパ.

Blender の Python UIList は Outliner のような「行にドロップされた」イベントを
直接公開しないため、D&D 後の平坦な並びから推定された parent_key を受け取り、
実データ側の reparent API へ橋渡しする。
"""

from __future__ import annotations

from .layer_hierarchy import (
    COMA_KIND,
    OUTSIDE_STACK_KEY,
    PAGE_KIND,
    coma_stack_key,
    page_stack_key,
    split_child_key,
)


def _find_page(work, page_key: str):
    if work is None or not page_key:
        return -1, None
    for index, page in enumerate(getattr(work, "pages", [])):
        if page_stack_key(page) == page_key:
            return index, page
    return -1, None


def _find_coma(page, parent_key: str, child_id: str):
    if page is None or not child_id:
        return None
    for panel in getattr(page, "comas", []):
        stem = str(getattr(panel, "coma_id", "") or getattr(panel, "id", "") or "")
        if (
            coma_stack_key(page, panel) == parent_key
            or stem == child_id
            or str(getattr(panel, "id", "") or "") == child_id
        ):
            return panel
    return None


def semantic_parent_target(context, parent_key: str):
    """page / coma / outside の parent_key を ClickTarget へ変換する."""
    from ..core.work import get_work
    from . import layer_reparent

    parent_key = str(parent_key or "")
    if not parent_key or parent_key == OUTSIDE_STACK_KEY:
        return layer_reparent.ClickTarget("outside", None, None, -1, None, None)

    page_key, child_id = split_child_key(parent_key)
    if page_key == OUTSIDE_STACK_KEY:
        return None
    work = get_work(context)
    page_index, page = _find_page(work, page_key)
    if page is None:
        return None
    if child_id:
        panel = _find_coma(page, parent_key, child_id)
        if panel is None:
            return None
        return layer_reparent.ClickTarget("coma", page, panel, page_index, None, None)
    return layer_reparent.ClickTarget("page", page, None, page_index, None, None)


def is_semantic_parent_key(context, parent_key: str) -> bool:
    return semantic_parent_target(context, parent_key) is not None


def apply_semantic_parent_drop(context, item, parent_key: str) -> bool:
    """D&D で page/coma/outside へ入れられた行を実データへ反映する."""
    from . import layer_reparent

    target = semantic_parent_target(context, parent_key)
    if target is None:
        return False
    return bool(layer_reparent.reparent_stack_item(context, item, target=target))


def child_can_use_semantic_parent(child_kind: str) -> bool:
    return child_kind in {
        COMA_KIND,
        "gp",
        "effect",
        "raster",
        "image",
        "balloon",
        "text",
    }
