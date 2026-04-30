"""アクティブな page / coma の解決ヘルパ.

各種レイヤー作成 op で「ユーザーが今選択している階層」を統一して取得する
ためのユーティリティ。優先順位:

    1. ``scene.bname_current_coma_id`` (cNN.blend 編集中)
    2. ``BNamePageEntry.active_coma_index`` (page browser でコマ選択)
    3. それ以外は page 直下

新規レイヤー作成時に Outliner Collection の親を決定する基準として使う。
"""

from __future__ import annotations

from typing import Optional

import bpy


def resolve_active_target(
    context, *, prefer_page=None
) -> tuple[str, str, Optional[object]]:
    """ユーザーが現在選択している階層 (page or coma) を解決.

    Returns:
        ``(parent_kind, parent_key, page_entry)``:
            - parent_kind: ``"page"`` or ``"coma"``
            - parent_key: ``"<page_id>"`` または ``"<page_id>:<coma_id>"``
            - page_entry: 解決したページの ``BNamePageEntry`` (取得不可なら None)
    """
    scene = getattr(context, "scene", None)
    if scene is None:
        return ("page", "", None)
    work = getattr(scene, "bname_work", None)
    if work is None or not getattr(work, "loaded", False):
        return ("page", "", None)
    pages = getattr(work, "pages", None)
    if not pages:
        return ("page", "", None)

    # アクティブページを解決
    page = prefer_page
    if page is None:
        idx = int(getattr(work, "active_page_index", 0))
        if 0 <= idx < len(pages):
            page = pages[idx]
    if page is None:
        return ("page", "", None)
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return ("page", "", None)

    comas = getattr(page, "comas", None)
    if not comas:
        return ("page", page_id, page)

    # 1. scene.bname_current_coma_id 最優先 (cNN.blend 編集中)
    current_coma_id = str(getattr(scene, "bname_current_coma_id", "") or "")
    if current_coma_id:
        for coma in comas:
            if str(getattr(coma, "id", "") or "") == current_coma_id:
                return ("coma", f"{page_id}:{current_coma_id}", page)

    # 2. page.active_coma_index
    coma_idx = int(getattr(page, "active_coma_index", -1))
    if 0 <= coma_idx < len(comas):
        coma = comas[coma_idx]
        coma_id = str(getattr(coma, "id", "") or "")
        if coma_id:
            return ("coma", f"{page_id}:{coma_id}", page)

    # 3. ページ直下
    return ("page", page_id, page)
