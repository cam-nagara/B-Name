"""Viewport overlay visibility predicates.

Blender 標準 Object と整合させるため、entry/panel の visible フラグだけでなく
**Outliner の Collection 表示状態** (LayerCollection.hide_viewport / exclude)
も見る。これによりユーザーが Outliner でページ/コマ Collection の目アイコン
を切ると、オーバーレイ描画も連動して非表示になる。
"""

from __future__ import annotations

import bpy

from ..utils import page_range
from ..utils.layer_hierarchy import entry_center, coma_containing_point


def _walk_layer_collection(layer_coll, bname_id: str):
    """LayerCollection ツリーから ``bname_id`` を持つ LayerCollection を探す."""
    if layer_coll is None or not bname_id:
        return None
    coll = getattr(layer_coll, "collection", None)
    if coll is not None and str(coll.get("bname_id") or "") == bname_id:
        return layer_coll
    for child in layer_coll.children:
        found = _walk_layer_collection(child, bname_id)
        if found is not None:
            return found
    return None


def _layer_collection_visible(bname_id: str) -> bool:
    """``bname_id`` の Collection が現在の view_layer で表示状態にあるか.

    LayerCollection.exclude (チェックボックス) または hide_viewport (目アイコン)
    が立っていたら非表示扱い。Collection が見つからない / scene 取得不可は
    True (表示) で fallback。
    """
    if not bname_id:
        return True
    try:
        scene = bpy.context.scene
        if scene is None:
            return True
        view_layer = bpy.context.view_layer
        if view_layer is None:
            return True
        lc = _walk_layer_collection(view_layer.layer_collection, bname_id)
        if lc is None:
            return True
        if bool(getattr(lc, "exclude", False)):
            return False
        if bool(getattr(lc, "hide_viewport", False)):
            return False
        # Collection 自身の hide_viewport (per-data) も見る
        coll = getattr(lc, "collection", None)
        if coll is not None and bool(getattr(coll, "hide_viewport", False)):
            return False
        return True
    except Exception:  # noqa: BLE001
        return True


def page_visible(page) -> bool:
    if not page_range.page_visible_in_work(page):
        return False
    page_id = str(getattr(page, "id", "") or "")
    if not _layer_collection_visible(page_id):
        return False
    return True


def coma_visible(panel, *, page=None) -> bool:
    if not bool(getattr(panel, "visible", True)):
        return False
    coma_id = str(getattr(panel, "id", "") or "")
    if not coma_id:
        return True
    # コマ Collection の bname_id は "<page_id>:<coma_id>" 形式
    page_id = ""
    if page is not None:
        page_id = str(getattr(page, "id", "") or "")
    else:
        # page 不明: 全 page を走査して panel を含むページを探す
        try:
            scene = bpy.context.scene
            work = getattr(scene, "bname_work", None) if scene is not None else None
            if work is not None:
                for p in getattr(work, "pages", []):
                    for c in getattr(p, "comas", []):
                        if c is panel:
                            page_id = str(getattr(p, "id", "") or "")
                            break
                    if page_id:
                        break
        except Exception:  # noqa: BLE001
            pass
    if page_id:
        bname_id = f"{page_id}:{coma_id}"
        if not _layer_collection_visible(bname_id):
            return False
    return True


def entry_in_visible_coma(page, entry) -> bool:
    # エントリ自身が「表示=False」なら描画しない (balloon / text で使用)
    if not bool(getattr(entry, "visible", True)):
        return False
    try:
        panel = coma_containing_point(page, *entry_center(entry))
    except Exception:  # noqa: BLE001
        panel = None
    return panel is None or coma_visible(panel, page=page)
