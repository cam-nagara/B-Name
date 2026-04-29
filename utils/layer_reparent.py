"""ビューポート操作 (Alt+ドラッグ / Alt+クリック / Alt+Shift+クリック) の共通 reparent ロジック.

統合レイヤーリストの D&D (`apply_stack_drop_hint`) は親キー解決ヒントから親変更を
実施するが、ビューポートからの reparent は「カーソル位置のコンテナ → ターゲット親」
を直接指定するスタイルなので、より単純な API を提供する。

フェーズ A/B 範囲:
- 末端レイヤー (gp / gp_folder / effect / balloon / text / image / raster) の親変更
- コマの別ページ移動 (既存 ``BNAME_OT_coma_move_to_page`` を呼び出すか、共通化)
- 「ページ外」(parent_kind="none") への昇格/復帰

公開関数:
- ``find_click_target(context, event)``: カーソル位置の coma/page を解決
- ``find_shallower_target(context, item, event)``: item から見た 1 段浅い親を返す
- ``reparent_stack_item(context, item, *, target_kind, ...)``: 1 件 reparent
- ``reparent_selected(context, target)``: マルチセレクト一括 reparent
- ``move_balloon_with_children(context, page, entry, x_mm, y_mm)``: 子テキスト連動
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import bpy
from bpy_extras.view3d_utils import region_2d_to_location_3d

from . import gp_layer_parenting as gp_parent
from . import layer_stack as layer_stack_utils
from . import log
from .layer_hierarchy import (
    COMA_KIND,
    OUTSIDE_STACK_KEY,
    PAGE_KIND,
    coma_containing_point,
    coma_polygon,
    coma_stack_key,
    outside_child_key,
    page_stack_key,
    point_in_polygon,
    split_child_key,
)

_logger = log.get_logger(__name__)


# ---------- データクラス ----------


@dataclass(frozen=True)
class ClickTarget:
    """カーソル位置から解決した「reparent ターゲット候補」.

    kind: "coma" | "page" | "outside"
        outside はフェーズ B 用 (page も含まない work 直下)
    page: 対象ページ (kind == "outside" のとき None)
    panel: 対象コマ (kind == "coma" のときのみ非 None)
    page_index: ページのインデックス (kind == "outside" のとき -1)
    world_xy_mm: ワールド座標 (mm)
    local_xy_mm: ページローカル座標 (kind が "outside" のとき None)
    """

    kind: str
    page: Optional[object]
    panel: Optional[object]
    page_index: int
    world_xy_mm: Optional[tuple[float, float]]
    local_xy_mm: Optional[tuple[float, float]]


# ---------- 内部ヘルパ ----------


def _world_xy_mm_from_event(context, event) -> Optional[tuple[float, float]]:
    """event から世界座標 mm を返す (View3D 領域外なら None)."""
    from ..operators import view_event_region
    from . import geom

    view = view_event_region.view3d_window_under_event(context, event)
    if view is None:
        return None
    _area, region, rv3d, mx, my = view
    loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
    if loc is None:
        return None
    return (geom.m_to_mm(loc.x), geom.m_to_mm(loc.y))


def _resolve_local_xy_mm(context, world_x_mm: float, world_y_mm: float):
    """world (mm) → (page_index, page, local_x_mm, local_y_mm).

    ヒットしないときは (-1, None, None, None).
    """
    from ..core.work import get_work
    from . import page_grid

    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return -1, None, None, None
    scene = context.scene
    page_idx = page_grid.page_index_at_world_mm(work, scene, world_x_mm, world_y_mm)
    if page_idx is None or not (0 <= page_idx < len(work.pages)):
        return -1, None, None, None
    page = work.pages[page_idx]
    ox, oy = page_grid.page_total_offset_mm(work, scene, page_idx)
    return page_idx, page, world_x_mm - ox, world_y_mm - oy


# ---------- 公開関数: ターゲット解決 ----------


def find_click_target(context, event) -> ClickTarget:
    """event の位置から「最深のコンテナ候補」を返す.

    最深 = まずコマを探し、無ければページ。どのページにも乗っていなければ
    kind="outside" (フェーズ A では未対応扱い)。
    """
    world = _world_xy_mm_from_event(context, event)
    if world is None:
        return ClickTarget("outside", None, None, -1, None, None)
    wx, wy = world
    page_index, page, lx, ly = _resolve_local_xy_mm(context, wx, wy)
    if page is None or lx is None or ly is None:
        return ClickTarget("outside", None, None, -1, world, None)
    panel = coma_containing_point(page, lx, ly)
    if panel is not None:
        return ClickTarget("coma", page, panel, page_index, world, (lx, ly))
    return ClickTarget("page", page, None, page_index, world, (lx, ly))


def find_target_for_drop(context, event) -> ClickTarget:
    """Alt+ドラッグのドロップ位置から、置きたい親候補を返す.

    現状は ``find_click_target`` と同じだが、ドラッグ専用の意味付けを持たせて
    将来 (Phase B) で挙動を変えやすくするためエイリアス化."""
    return find_click_target(context, event)


# ---------- 公開関数: 親キー解決 ----------


def parent_key_for_target(target: ClickTarget) -> str:
    """ClickTarget を ``parent_key`` 文字列に変換する.

    - kind="coma" → ``"<page_id>:<coma_id>"``
    - kind="page" → ``"<page_id>"``
    - kind="outside" → ``""`` (実データでは parent_kind="none")
    """
    if target.kind == "coma" and target.page is not None and target.panel is not None:
        return coma_stack_key(target.page, target.panel)
    if target.kind == "coma" and target.page is None and target.panel is not None:
        stem = str(getattr(target.panel, "coma_id", "") or getattr(target.panel, "id", "") or "")
        return outside_child_key(stem)
    if target.kind == "page" and target.page is not None:
        return page_stack_key(target.page)
    return ""


def current_parent_key(item) -> str:
    """layer_stack item の現在の親キーを返す."""
    return str(getattr(item, "parent_key", "") or "")


def shallower_target_for_item(context, item, click_target: ClickTarget) -> Optional[ClickTarget]:
    """item から見て 1 段浅い親候補を返す.

    - item が coma 配下 → そのページが親 (= page-level に昇格)
    - item が page 直下 → "outside"
    - item の親が "outside" → さらに浅い親なし → None
    """
    parent_key = current_parent_key(item)
    if not parent_key or parent_key == OUTSIDE_STACK_KEY:
        return None
    page_id, child_id = split_child_key(parent_key)
    if child_id:
        # コマ直下 → ページに昇格
        from ..core.work import get_work

        work = get_work(context)
        if work is None:
            return None
        for i, page in enumerate(work.pages):
            if page_stack_key(page) == page_id:
                return ClickTarget("page", page, None, i, click_target.world_xy_mm, click_target.local_xy_mm)
        return None
    # ページ直下 → 上は "outside" (Phase B 範囲)
    return ClickTarget("outside", None, None, -1, click_target.world_xy_mm, click_target.local_xy_mm)


# ---------- 公開関数: reparent 実行 ----------


def reparent_stack_item(
    context,
    item,
    *,
    target: ClickTarget,
    new_world_xy_mm: Optional[tuple[float, float]] = None,
) -> bool:
    """1 つの stack item を ``target`` に reparent する.

    Args:
        item: ``BNameLayerStackItem``
        target: 行先コンテナ
        new_world_xy_mm: ドラッグ落下位置 (世界座標 mm)。
            None のときは「位置を動かさない」。

    Returns:
        変更が発生したら True。
    """
    new_parent_key = parent_key_for_target(target)
    if target.kind != "outside" and not new_parent_key:
        return False

    kind = getattr(item, "kind", "")
    # NOTE: item.parent_key は位置ベースの heuristic で決まるため、エントリ実体の
    # parent_key と乖離していることがある。早期 return は item.parent_key だけで
    # 判定すると "stale" 状態で skip されるので、ここでは行わない (各種別関数の
    # 内部でエントリ側 parent_key と比較する)。
    if kind == "balloon":
        return _reparent_balloon(context, item, target, new_parent_key, new_world_xy_mm)
    if kind == "text":
        return _reparent_text(context, item, target, new_parent_key, new_world_xy_mm)
    if kind == "image":
        return _reparent_image(context, item, target, new_parent_key, new_world_xy_mm)
    if kind == "raster":
        return _reparent_raster(context, item, target, new_parent_key)
    if kind in {"gp", "gp_folder", "effect"}:
        return _reparent_gp_node(context, item, target, new_parent_key)
    if kind == COMA_KIND:
        return _reparent_coma(context, item, target)
    if kind == PAGE_KIND:
        # Phase A: page の reparent は対応しない (page は階層トップに近い)
        return False
    return False


def reparent_selected(
    context,
    target: ClickTarget,
    *,
    new_world_xy_mm: Optional[tuple[float, float]] = None,
) -> int:
    """選択中の全 stack item を ``target`` に reparent する.

    アクティブ行 + ``selected`` フラグが立っている行を対象。
    """
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bname_layer_stack", None) if scene is not None else None
    if stack is None:
        return 0
    selected_uids: list[str] = []
    active_idx = int(getattr(scene, "bname_active_layer_stack_index", -1))
    for i, item in enumerate(stack):
        if i == active_idx or layer_stack_utils.is_item_selected(context, item):
            selected_uids.append(layer_stack_utils.stack_item_uid(item))
    if not selected_uids:
        return 0
    changed = 0
    for uid in selected_uids:
        # stack の参照が並び替えで変わる可能性があるので毎回再解決
        for item in (getattr(scene, "bname_layer_stack", None) or []):
            if layer_stack_utils.stack_item_uid(item) == uid:
                if reparent_stack_item(
                    context,
                    item,
                    target=target,
                    new_world_xy_mm=new_world_xy_mm,
                ):
                    changed += 1
                break
    if changed:
        layer_stack_utils.apply_stack_order(context)
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        layer_stack_utils.tag_view3d_redraw(context)
    return changed


# ---------- 個別 reparent (private) ----------


def _move_entry_position_local(entry, page, target: ClickTarget, new_world_xy_mm) -> None:
    """drop 位置を entry のページローカル座標に変換し、entry.x_mm/y_mm を移動する.

    new_world_xy_mm が None なら何もしない (位置維持)。
    幅/高さは保持。entry の中心を drop 位置に合わせる。
    """
    if new_world_xy_mm is None or page is None:
        return
    from ..core.work import get_work
    from . import page_grid

    scene = bpy.context.scene
    work = get_work(bpy.context)
    if work is None:
        return
    # page index を逆引き
    page_index = -1
    for i, p in enumerate(work.pages):
        if page_stack_key(p) == page_stack_key(page):
            page_index = i
            break
    if page_index < 0:
        return
    ox, oy = page_grid.page_total_offset_mm(work, scene, page_index)
    wx, wy = new_world_xy_mm
    lx = wx - ox
    ly = wy - oy
    w = float(getattr(entry, "width_mm", 0.0))
    h = float(getattr(entry, "height_mm", 0.0))
    try:
        entry.x_mm = lx - w * 0.5
        entry.y_mm = ly - h * 0.5
    except Exception:  # noqa: BLE001
        pass


def _resolve_balloon_in_page(page, balloon_id: str):
    if page is None or not balloon_id:
        return None
    for entry in getattr(page, "balloons", []):
        if str(getattr(entry, "id", "") or "") == balloon_id:
            return entry
    return None


def _balloon_owner_page(work, balloon_id: str):
    """balloon_id を保持しているページを返す."""
    if work is None or not balloon_id:
        return None
    for page in getattr(work, "pages", []):
        if _resolve_balloon_in_page(page, balloon_id) is not None:
            return page
    return None


def _find_entry_index_by_id(coll, key: str, *, id_attrs: tuple[str, ...] = ("id",)):
    key = str(key or "")
    if not key:
        return -1, None
    for i, entry in enumerate(coll or []):
        for attr in id_attrs:
            if str(getattr(entry, attr, "") or "") == key:
                return i, entry
    return -1, None


def _unique_collection_id(coll, preferred: str, prefix: str, *, id_attr: str = "id") -> str:
    used = {str(getattr(entry, id_attr, "") or "") for entry in (coll or [])}
    preferred = str(preferred or "").strip()
    if preferred and preferred not in used:
        return preferred
    i = 1
    while True:
        candidate = f"{prefix}_{i:04d}"
        if candidate not in used:
            return candidate
        i += 1


def _page_index_for_page(work, page) -> int:
    if work is None or page is None:
        return -1
    page_key = page_stack_key(page)
    for i, candidate in enumerate(getattr(work, "pages", [])):
        if page_stack_key(candidate) == page_key:
            return i
    return -1


def _page_offset_for_page(context, work, page) -> tuple[float, float]:
    from . import page_grid

    idx = _page_index_for_page(work, page)
    if idx < 0:
        return (0.0, 0.0)
    return page_grid.page_total_offset_mm(work, context.scene, idx)


def _entry_top_left_world(context, work, page, entry) -> tuple[float, float]:
    x = float(getattr(entry, "x_mm", 0.0))
    y = float(getattr(entry, "y_mm", 0.0))
    if page is None:
        return x, y
    ox, oy = _page_offset_for_page(context, work, page)
    return x + ox, y + oy


def _set_entry_top_left_world(context, work, page, entry, world_xy: tuple[float, float]) -> None:
    wx, wy = world_xy
    if page is None:
        entry.x_mm = wx
        entry.y_mm = wy
        return
    ox, oy = _page_offset_for_page(context, work, page)
    entry.x_mm = wx - ox
    entry.y_mm = wy - oy


def _move_entry_center_world(context, work, page, entry, new_world_xy_mm) -> None:
    if new_world_xy_mm is None:
        return
    wx, wy = new_world_xy_mm
    w = float(getattr(entry, "width_mm", 0.0))
    h = float(getattr(entry, "height_mm", 0.0))
    _set_entry_top_left_world(context, work, page, entry, (wx - w * 0.5, wy - h * 0.5))


def _remove_entry_by_id(coll, key: str, *, id_attrs: tuple[str, ...] = ("id",)) -> bool:
    idx, _entry = _find_entry_index_by_id(coll, key, id_attrs=id_attrs)
    if idx < 0:
        return False
    coll.remove(idx)
    return True


def _source_balloon(context, work, item):
    src_page_id, balloon_id = split_child_key(str(getattr(item, "key", "") or ""))
    if src_page_id == OUTSIDE_STACK_KEY:
        idx, entry = _find_entry_index_by_id(getattr(work, "shared_balloons", []), balloon_id)
        return "shared", None, idx, entry, balloon_id
    src_page = None
    for page in getattr(work, "pages", []):
        if page_stack_key(page) == src_page_id:
            src_page = page
            break
    if src_page is None:
        src_page = _balloon_owner_page(work, balloon_id)
    if src_page is None:
        return "", None, -1, None, balloon_id
    idx, entry = _find_entry_index_by_id(getattr(src_page, "balloons", []), balloon_id)
    return "page", src_page, idx, entry, balloon_id


def _source_text(work, item):
    src_page_id, text_id = split_child_key(str(getattr(item, "key", "") or ""))
    if src_page_id == OUTSIDE_STACK_KEY:
        idx, entry = _find_entry_index_by_id(getattr(work, "shared_texts", []), text_id)
        return "shared", None, idx, entry, text_id
    src_page = None
    for page in getattr(work, "pages", []):
        if page_stack_key(page) == src_page_id:
            src_page = page
            break
    if src_page is None:
        return "", None, -1, None, text_id
    idx, entry = _find_entry_index_by_id(getattr(src_page, "texts", []), text_id)
    return "page", src_page, idx, entry, text_id


def _balloon_id_exists(coll, balloon_id: str) -> bool:
    _idx, entry = _find_entry_index_by_id(coll, balloon_id)
    return entry is not None


def _copy_child_texts_page_to_shared(
    context,
    work,
    src_page,
    src_balloon_id: str,
    dst_balloon_id: str,
    *,
    delta_world: tuple[float, float] = (0.0, 0.0),
) -> None:
    from ..io import schema

    target_ids = [
        str(getattr(entry, "id", "") or "")
        for entry in getattr(src_page, "texts", [])
        if str(getattr(entry, "parent_balloon_id", "") or "") == src_balloon_id
    ]
    for text_id in target_ids:
        idx, src_entry = _find_entry_index_by_id(src_page.texts, text_id)
        if src_entry is None:
            continue
        final_id = _unique_collection_id(work.shared_texts, text_id, "shared_text")
        new_entry = work.shared_texts.add()
        schema.text_entry_from_dict(new_entry, schema.text_entry_to_dict(src_entry))
        new_entry.id = final_id
        new_entry.parent_kind = "none"
        new_entry.parent_key = ""
        new_entry.parent_balloon_id = dst_balloon_id
        old_world = _entry_top_left_world(context, work, src_page, src_entry)
        _set_entry_top_left_world(
            context,
            work,
            None,
            new_entry,
            (old_world[0] + delta_world[0], old_world[1] + delta_world[1]),
        )
        src_page.texts.remove(idx)


def _copy_child_texts_shared_to_page(
    context,
    work,
    dst_page,
    src_balloon_id: str,
    dst_balloon_id: str,
    *,
    delta_world: tuple[float, float] = (0.0, 0.0),
    parent_kind: str = "page",
    parent_key: str = "",
) -> None:
    from ..io import schema
    from ..operators.text_op import _allocate_text_id

    target_ids = [
        str(getattr(entry, "id", "") or "")
        for entry in getattr(work, "shared_texts", [])
        if str(getattr(entry, "parent_balloon_id", "") or "") == src_balloon_id
    ]
    for text_id in target_ids:
        idx, src_entry = _find_entry_index_by_id(work.shared_texts, text_id)
        if src_entry is None:
            continue
        existing_dst_ids = {str(getattr(t, "id", "") or "") for t in dst_page.texts}
        final_id = text_id if text_id and text_id not in existing_dst_ids else _allocate_text_id(dst_page)
        new_entry = dst_page.texts.add()
        schema.text_entry_from_dict(new_entry, schema.text_entry_to_dict(src_entry))
        new_entry.id = final_id
        new_entry.parent_kind = parent_kind
        new_entry.parent_key = parent_key or page_stack_key(dst_page)
        new_entry.parent_balloon_id = dst_balloon_id
        old_world = _entry_top_left_world(context, work, None, src_entry)
        _set_entry_top_left_world(
            context,
            work,
            dst_page,
            new_entry,
            (old_world[0] + delta_world[0], old_world[1] + delta_world[1]),
        )
        work.shared_texts.remove(idx)


def _copy_child_texts_page_to_page(
    context,
    work,
    src_page,
    dst_page,
    src_balloon_id: str,
    dst_balloon_id: str,
    *,
    delta_world: tuple[float, float] = (0.0, 0.0),
    parent_kind: str = "page",
    parent_key: str = "",
) -> None:
    from ..io import schema
    from ..operators.text_op import _allocate_text_id

    target_ids = [
        str(getattr(entry, "id", "") or "")
        for entry in getattr(src_page, "texts", [])
        if str(getattr(entry, "parent_balloon_id", "") or "") == src_balloon_id
    ]
    for text_id in target_ids:
        idx, src_entry = _find_entry_index_by_id(src_page.texts, text_id)
        if src_entry is None:
            continue
        existing_dst_ids = {str(getattr(t, "id", "") or "") for t in dst_page.texts}
        final_id = text_id if text_id and text_id not in existing_dst_ids else _allocate_text_id(dst_page)
        new_entry = dst_page.texts.add()
        schema.text_entry_from_dict(new_entry, schema.text_entry_to_dict(src_entry))
        new_entry.id = final_id
        new_entry.parent_balloon_id = dst_balloon_id
        new_entry.parent_kind = parent_kind
        new_entry.parent_key = parent_key or page_stack_key(dst_page)
        old_world = _entry_top_left_world(context, work, src_page, src_entry)
        _set_entry_top_left_world(
            context,
            work,
            dst_page,
            new_entry,
            (old_world[0] + delta_world[0], old_world[1] + delta_world[1]),
        )
        src_page.texts.remove(idx)


def _reparent_balloon(context, item, target: ClickTarget, new_parent_key: str, new_world_xy_mm) -> bool:
    from ..core.work import get_work
    from ..io import schema

    work = get_work(context)
    if work is None:
        return False
    src_scope, src_page, src_idx, src_entry, balloon_id = _source_balloon(context, work, item)
    if src_entry is None:
        return False
    old_world = _entry_top_left_world(context, work, src_page, src_entry)
    old_parent = str(getattr(src_entry, "parent_key", "") or "")

    if target.kind == "outside":
        if src_scope == "shared":
            src_entry.parent_kind = "none"
            src_entry.parent_key = ""
            _move_entry_center_world(context, work, None, src_entry, new_world_xy_mm)
            try:
                item.parent_key = OUTSIDE_STACK_KEY
            except Exception:  # noqa: BLE001
                pass
            return old_parent or new_world_xy_mm is not None
        if src_page is None:
            return False
        final_id = _unique_collection_id(work.shared_balloons, balloon_id, "shared_balloon")
        new_entry = work.shared_balloons.add()
        schema.balloon_entry_from_dict(new_entry, schema.balloon_entry_to_dict(src_entry))
        new_entry.id = final_id
        new_entry.parent_kind = "none"
        new_entry.parent_key = ""
        _set_entry_top_left_world(context, work, None, new_entry, old_world)
        _move_entry_center_world(context, work, None, new_entry, new_world_xy_mm)
        new_world = _entry_top_left_world(context, work, None, new_entry)
        delta_world = (new_world[0] - old_world[0], new_world[1] - old_world[1])
        _copy_child_texts_page_to_shared(
            context,
            work,
            src_page,
            balloon_id,
            final_id,
            delta_world=delta_world,
        )
        _remove_entry_by_id(src_page.balloons, balloon_id)
        try:
            item.parent_key = OUTSIDE_STACK_KEY
        except Exception:  # noqa: BLE001
            pass
        return True

    if target.page is None:
        return False
    dst_page = target.page

    if src_scope == "shared":
        from ..operators.balloon_op import _allocate_balloon_id

        existing_dst_ids = {str(getattr(b, "id", "") or "") for b in dst_page.balloons}
        final_id = balloon_id if balloon_id and balloon_id not in existing_dst_ids else _allocate_balloon_id(dst_page)
        new_entry = dst_page.balloons.add()
        schema.balloon_entry_from_dict(new_entry, schema.balloon_entry_to_dict(src_entry))
        new_entry.id = final_id
        new_entry.parent_kind = "coma" if target.kind == "coma" else "page"
        new_entry.parent_key = new_parent_key
        _set_entry_top_left_world(context, work, dst_page, new_entry, old_world)
        _move_entry_center_world(context, work, dst_page, new_entry, new_world_xy_mm)
        new_world = _entry_top_left_world(context, work, dst_page, new_entry)
        delta_world = (new_world[0] - old_world[0], new_world[1] - old_world[1])
        _copy_child_texts_shared_to_page(
            context,
            work,
            dst_page,
            balloon_id,
            final_id,
            delta_world=delta_world,
            parent_kind="coma" if target.kind == "coma" else "page",
            parent_key=new_parent_key,
        )
        if src_idx >= 0:
            work.shared_balloons.remove(src_idx)
        return True

    if src_page is None:
        return False
    same_page = page_stack_key(src_page) == page_stack_key(dst_page)
    if same_page:
        src_entry.parent_kind = "coma" if target.kind == "coma" else "page"
        src_entry.parent_key = new_parent_key
        try:
            item.parent_key = new_parent_key
        except Exception:  # noqa: BLE001
            pass
        if new_world_xy_mm is not None:
            _move_balloon_with_children_world(context, src_page, src_entry, new_world_xy_mm)
        if old_parent == new_parent_key and new_world_xy_mm is None:
            return False
        return True

    from ..operators.balloon_op import _allocate_balloon_id

    existing_dst_ids = {str(getattr(b, "id", "") or "") for b in dst_page.balloons}
    final_id = balloon_id if balloon_id and balloon_id not in existing_dst_ids else _allocate_balloon_id(dst_page)
    new_entry = dst_page.balloons.add()
    schema.balloon_entry_from_dict(new_entry, schema.balloon_entry_to_dict(src_entry))
    new_entry.id = final_id
    new_entry.parent_kind = "coma" if target.kind == "coma" else "page"
    new_entry.parent_key = new_parent_key
    _set_entry_top_left_world(context, work, dst_page, new_entry, old_world)
    _move_entry_center_world(context, work, dst_page, new_entry, new_world_xy_mm)
    new_world = _entry_top_left_world(context, work, dst_page, new_entry)
    delta_world = (new_world[0] - old_world[0], new_world[1] - old_world[1])
    _copy_child_texts_page_to_page(
        context,
        work,
        src_page,
        dst_page,
        balloon_id,
        final_id,
        delta_world=delta_world,
        parent_kind="coma" if target.kind == "coma" else "page",
        parent_key=new_parent_key,
    )
    _remove_entry_by_id(src_page.balloons, balloon_id)
    return True


def _reparent_text(context, item, target: ClickTarget, new_parent_key: str, new_world_xy_mm) -> bool:
    from ..core.work import get_work
    from ..io import schema

    work = get_work(context)
    if work is None:
        return False
    src_scope, src_page, src_idx, src_entry, text_id = _source_text(work, item)
    if src_entry is None:
        return False
    old_parent = str(getattr(src_entry, "parent_key", "") or "")
    old_world = _entry_top_left_world(context, work, src_page, src_entry)

    if target.kind == "outside":
        if src_scope == "shared":
            src_entry.parent_kind = "none"
            src_entry.parent_key = ""
            if not _balloon_id_exists(getattr(work, "shared_balloons", []), src_entry.parent_balloon_id):
                src_entry.parent_balloon_id = ""
            _move_entry_center_world(context, work, None, src_entry, new_world_xy_mm)
            try:
                item.parent_key = OUTSIDE_STACK_KEY
            except Exception:  # noqa: BLE001
                pass
            return old_parent or new_world_xy_mm is not None
        if src_page is None:
            return False
        final_id = _unique_collection_id(work.shared_texts, text_id, "shared_text")
        new_entry = work.shared_texts.add()
        schema.text_entry_from_dict(new_entry, schema.text_entry_to_dict(src_entry))
        new_entry.id = final_id
        new_entry.parent_kind = "none"
        new_entry.parent_key = ""
        if not _balloon_id_exists(getattr(work, "shared_balloons", []), new_entry.parent_balloon_id):
            new_entry.parent_balloon_id = ""
        _set_entry_top_left_world(context, work, None, new_entry, old_world)
        _move_entry_center_world(context, work, None, new_entry, new_world_xy_mm)
        _remove_entry_by_id(src_page.texts, text_id)
        try:
            item.parent_key = OUTSIDE_STACK_KEY
        except Exception:  # noqa: BLE001
            pass
        return True

    if target.page is None:
        return False
    dst_page = target.page

    if src_scope == "shared":
        from ..operators.text_op import _allocate_text_id

        existing_dst_ids = {str(getattr(t, "id", "") or "") for t in dst_page.texts}
        final_id = text_id if text_id and text_id not in existing_dst_ids else _allocate_text_id(dst_page)
        new_entry = dst_page.texts.add()
        schema.text_entry_from_dict(new_entry, schema.text_entry_to_dict(src_entry))
        new_entry.id = final_id
        new_entry.parent_kind = "coma" if target.kind == "coma" else "page"
        new_entry.parent_key = new_parent_key
        if not _balloon_id_exists(getattr(dst_page, "balloons", []), new_entry.parent_balloon_id):
            new_entry.parent_balloon_id = ""
        _set_entry_top_left_world(context, work, dst_page, new_entry, old_world)
        _move_entry_center_world(context, work, dst_page, new_entry, new_world_xy_mm)
        if src_idx >= 0:
            work.shared_texts.remove(src_idx)
        return True

    if src_page is None:
        return False
    same_page = page_stack_key(src_page) == page_stack_key(dst_page)
    if same_page:
        src_entry.parent_kind = "coma" if target.kind == "coma" else "page"
        src_entry.parent_key = new_parent_key
        try:
            item.parent_key = new_parent_key
        except Exception:  # noqa: BLE001
            pass
        if new_world_xy_mm is not None:
            _move_entry_position_local(src_entry, src_page, target, new_world_xy_mm)
        if old_parent == new_parent_key and new_world_xy_mm is None:
            return False
        return True

    from ..operators.text_op import _allocate_text_id

    existing_dst_ids = {str(getattr(t, "id", "") or "") for t in dst_page.texts}
    final_id = text_id if text_id and text_id not in existing_dst_ids else _allocate_text_id(dst_page)
    new_entry = dst_page.texts.add()
    schema.text_entry_from_dict(new_entry, schema.text_entry_to_dict(src_entry))
    new_entry.id = final_id
    new_entry.parent_kind = "coma" if target.kind == "coma" else "page"
    new_entry.parent_key = new_parent_key
    if not _balloon_id_exists(getattr(dst_page, "balloons", []), new_entry.parent_balloon_id):
        new_entry.parent_balloon_id = ""
    _set_entry_top_left_world(context, work, dst_page, new_entry, old_world)
    _move_entry_center_world(context, work, dst_page, new_entry, new_world_xy_mm)
    _remove_entry_by_id(src_page.texts, text_id)
    return True


def _reparent_image(context, item, target: ClickTarget, new_parent_key: str, new_world_xy_mm) -> bool:
    """画像レイヤーの parent_kind/parent_key と world 座標を更新する."""
    scene = getattr(context, "scene", None)
    if scene is None:
        return False
    coll = getattr(scene, "bname_image_layers", None)
    if coll is None:
        return False
    image_id = str(getattr(item, "key", "") or "")
    entry = None
    for e in coll:
        if str(getattr(e, "id", "") or "") == image_id:
            entry = e
            break
    if entry is None:
        return False
    if target.kind == "outside":
        entry.parent_kind = "none"
        entry.parent_key = ""
        item.parent_key = OUTSIDE_STACK_KEY
    else:
        entry.parent_kind = "coma" if target.kind == "coma" else "page"
        entry.parent_key = new_parent_key
        item.parent_key = new_parent_key
    if new_world_xy_mm is not None:
        wx, wy = new_world_xy_mm
        w = float(getattr(entry, "width_mm", 0.0))
        h = float(getattr(entry, "height_mm", 0.0))
        try:
            entry.x_mm = wx - w * 0.5
            entry.y_mm = wy - h * 0.5
        except Exception:  # noqa: BLE001
            pass
    return True


def _reparent_raster(context, item, target: ClickTarget, new_parent_key: str) -> bool:
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bname_raster_layers", None) if scene is not None else None
    if coll is None:
        return False
    raster_id = str(getattr(item, "key", "") or "")
    entry = None
    for e in coll:
        if str(getattr(e, "id", "") or "") == raster_id:
            entry = e
            break
    if entry is None:
        return False
    try:
        if target.kind == "outside":
            entry.scope = "master"
            entry.parent_kind = "none"
            entry.parent_key = ""
            item.parent_key = OUTSIDE_STACK_KEY
            return True
        entry.scope = "page"
        entry.parent_kind = "coma" if ":" in new_parent_key else "page"
        entry.parent_key = new_parent_key
    except Exception:  # noqa: BLE001
        return False
    item.parent_key = new_parent_key
    return True


def _reparent_gp_node(context, item, target: ClickTarget, new_parent_key: str) -> bool:
    """GP layer / group / effect レイヤーの親キーを書き換える."""
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    node = resolved.get("target") if resolved is not None else None
    if node is None:
        return False
    actual_parent_key = "" if target.kind == "outside" else new_parent_key
    gp_parent.set_parent_key(node, actual_parent_key)
    item.parent_key = OUTSIDE_STACK_KEY if target.kind == "outside" else new_parent_key
    return True


def _move_page_coma_children_to_outside(context, work, src_page, old_parent_key: str) -> None:
    """ページ内コマを外へ出すとき、直下の主要レイヤーをページ外へ逃がす."""
    from ..io import schema

    for balloon_id in [
        str(getattr(entry, "id", "") or "")
        for entry in getattr(src_page, "balloons", [])
        if str(getattr(entry, "parent_key", "") or "") == old_parent_key
    ]:
        idx, src_entry = _find_entry_index_by_id(src_page.balloons, balloon_id)
        if src_entry is None:
            continue
        final_id = _unique_collection_id(work.shared_balloons, balloon_id, "shared_balloon")
        new_entry = work.shared_balloons.add()
        schema.balloon_entry_from_dict(new_entry, schema.balloon_entry_to_dict(src_entry))
        new_entry.id = final_id
        new_entry.parent_kind = "none"
        new_entry.parent_key = ""
        world_xy = _entry_top_left_world(context, work, src_page, src_entry)
        _set_entry_top_left_world(context, work, None, new_entry, world_xy)
        _copy_child_texts_page_to_shared(context, work, src_page, balloon_id, final_id)
        src_page.balloons.remove(idx)

    for text_id in [
        str(getattr(entry, "id", "") or "")
        for entry in getattr(src_page, "texts", [])
        if str(getattr(entry, "parent_key", "") or "") == old_parent_key
    ]:
        idx, src_entry = _find_entry_index_by_id(src_page.texts, text_id)
        if src_entry is None:
            continue
        final_id = _unique_collection_id(work.shared_texts, text_id, "shared_text")
        new_entry = work.shared_texts.add()
        schema.text_entry_from_dict(new_entry, schema.text_entry_to_dict(src_entry))
        new_entry.id = final_id
        new_entry.parent_kind = "none"
        new_entry.parent_key = ""
        if not _balloon_id_exists(getattr(work, "shared_balloons", []), new_entry.parent_balloon_id):
            new_entry.parent_balloon_id = ""
        world_xy = _entry_top_left_world(context, work, src_page, src_entry)
        _set_entry_top_left_world(context, work, None, new_entry, world_xy)
        src_page.texts.remove(idx)

    scene = getattr(context, "scene", None)
    for entry in getattr(scene, "bname_raster_layers", []) or []:
        if str(getattr(entry, "parent_key", "") or "") == old_parent_key:
            entry.scope = "master"
            entry.parent_kind = "none"
            entry.parent_key = ""
    for entry in getattr(scene, "bname_image_layers", []) or []:
        if str(getattr(entry, "parent_key", "") or "") == old_parent_key:
            entry.parent_kind = "none"
            entry.parent_key = ""
    for layer in layer_stack_utils.gp_layers_for_parent_keys(context, {old_parent_key}):
        gp_parent.set_parent_key(layer, "")
    for layer in layer_stack_utils.effect_layers_for_parent_keys(context, {old_parent_key}):
        gp_parent.set_parent_key(layer, "")


def _reparent_coma(context, item, target: ClickTarget) -> bool:
    """コマを別ページまたはページ外へ送る. 同一ページ内なら no-op."""
    if target.kind not in {"page", "outside"} or (target.kind == "page" and target.page is None):
        return False
    from ..core.work import get_work
    from ..io import schema

    work = get_work(context)
    if work is None:
        return False
    src_page_id, coma_id = split_child_key(str(getattr(item, "key", "") or ""))
    if not coma_id:
        return False
    if src_page_id == OUTSIDE_STACK_KEY:
        if target.kind != "page" or target.page is None:
            return False
        src_idx, src_entry = _find_entry_index_by_id(
            getattr(work, "shared_comas", []),
            coma_id,
            id_attrs=("coma_id", "id"),
        )
        if src_entry is None:
            return False
        final_stem = _unique_collection_id(target.page.comas, coma_id, "c", id_attr="coma_id")
        new_entry = target.page.comas.add()
        schema.coma_entry_from_dict(new_entry, schema.coma_entry_to_dict(src_entry))
        new_entry.coma_id = final_stem
        new_entry.id = final_stem
        ox, oy = _page_offset_for_page(context, work, target.page)
        new_entry.rect_x_mm = float(getattr(new_entry, "rect_x_mm", 0.0)) - ox
        new_entry.rect_y_mm = float(getattr(new_entry, "rect_y_mm", 0.0)) - oy
        for vertex in getattr(new_entry, "vertices", []):
            vertex.x_mm -= ox
            vertex.y_mm -= oy
        new_entry.z_order = max((int(getattr(p, "z_order", 0)) for p in target.page.comas), default=-1) + 1
        if src_idx >= 0:
            work.shared_comas.remove(src_idx)
        target.page.coma_count = len(target.page.comas)
        return True

    src_page_idx = -1
    src_page = None
    for i, p in enumerate(work.pages):
        if page_stack_key(p) == src_page_id:
            src_page_idx = i
            src_page = p
            break
    if src_page is None or page_stack_key(src_page) == page_stack_key(target.page):
        return False
    coma_index = -1
    src_entry = None
    for i, panel in enumerate(src_page.comas):
        if str(getattr(panel, "coma_id", "") or "") == coma_id:
            coma_index = i
            src_entry = panel
            break
    if coma_index < 0:
        return False
    if target.kind == "outside":
        final_stem = _unique_collection_id(work.shared_comas, coma_id, "shared_coma", id_attr="coma_id")
        old_parent_key = coma_stack_key(src_page, src_entry)
        new_entry = work.shared_comas.add()
        schema.coma_entry_from_dict(new_entry, schema.coma_entry_to_dict(src_entry))
        new_entry.coma_id = final_stem
        new_entry.id = final_stem
        ox, oy = _page_offset_for_page(context, work, src_page)
        new_entry.rect_x_mm = float(getattr(new_entry, "rect_x_mm", 0.0)) + ox
        new_entry.rect_y_mm = float(getattr(new_entry, "rect_y_mm", 0.0)) + oy
        for vertex in getattr(new_entry, "vertices", []):
            vertex.x_mm += ox
            vertex.y_mm += oy
        new_entry.z_order = max((int(getattr(p, "z_order", 0)) for p in work.shared_comas), default=-1) + 1
        _move_page_coma_children_to_outside(context, work, src_page, old_parent_key)
        src_page.comas.remove(coma_index)
        src_page.coma_count = len(src_page.comas)
        try:
            item.parent_key = OUTSIDE_STACK_KEY
        except Exception:  # noqa: BLE001
            pass
        return True

    # 既存 BNAME_OT_coma_move_to_page をそのまま活用するため、active_coma を一時設定して invoke
    # ただし direct API がないので、operator を呼ぶ
    work.active_page_index = src_page_idx
    src_page.active_coma_index = coma_index
    try:
        ret = bpy.ops.bname.coma_move_to_page(
            "EXEC_DEFAULT",
            target_page_id=str(getattr(target.page, "id", "") or ""),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("coma_move_to_page failed during reparent")
        return False
    return "FINISHED" in ret


# ---------- 子要素連動 ----------


def _move_balloon_with_children_world(context, page, entry, new_world_xy_mm) -> None:
    """フキダシをドロップ位置 (world mm) に移動。子テキストも同じ delta で動く."""
    from ..core.work import get_work
    from . import page_grid

    work = get_work(context)
    if work is None or page is None:
        return
    page_index = -1
    for i, p in enumerate(work.pages):
        if page_stack_key(p) == page_stack_key(page):
            page_index = i
            break
    if page_index < 0:
        return
    ox, oy = page_grid.page_total_offset_mm(work, context.scene, page_index)
    wx, wy = new_world_xy_mm
    lx = wx - ox
    ly = wy - oy
    w = float(getattr(entry, "width_mm", 0.0))
    h = float(getattr(entry, "height_mm", 0.0))
    new_x = lx - w * 0.5
    new_y = ly - h * 0.5
    dx = new_x - float(getattr(entry, "x_mm", 0.0))
    dy = new_y - float(getattr(entry, "y_mm", 0.0))
    entry.x_mm = new_x
    entry.y_mm = new_y
    if abs(dx) <= 1.0e-9 and abs(dy) <= 1.0e-9:
        return
    bid = str(getattr(entry, "id", "") or "")
    for text in getattr(page, "texts", []):
        if str(getattr(text, "parent_balloon_id", "") or "") == bid:
            text.x_mm += dx
            text.y_mm += dy


def _shift_entry_for_page_change(context, src_page, dst_page, dst_entry) -> None:
    """別ページへ移したときに、視覚位置 (世界座標) を維持するため
    entry.x_mm/y_mm をページオフセットの差で補正する.
    """
    from ..core.work import get_work
    from . import page_grid

    work = get_work(context)
    if work is None or src_page is None or dst_page is None:
        return
    src_idx = next(
        (i for i, p in enumerate(work.pages) if page_stack_key(p) == page_stack_key(src_page)),
        -1,
    )
    dst_idx = next(
        (i for i, p in enumerate(work.pages) if page_stack_key(p) == page_stack_key(dst_page)),
        -1,
    )
    if src_idx < 0 or dst_idx < 0:
        return
    sox, soy = page_grid.page_total_offset_mm(work, context.scene, src_idx)
    dox, doy = page_grid.page_total_offset_mm(work, context.scene, dst_idx)
    delta_x = sox - dox
    delta_y = soy - doy
    try:
        dst_entry.x_mm = float(getattr(dst_entry, "x_mm", 0.0)) + delta_x
        dst_entry.y_mm = float(getattr(dst_entry, "y_mm", 0.0)) + delta_y
    except Exception:  # noqa: BLE001
        pass


def _move_child_texts_across_page(context, src_page, dst_page, balloon_id: str, new_balloon_id: str = "") -> None:
    """別ページへフキダシを送るときに、その子テキスト (parent_balloon_id == balloon_id)
    を一緒に dst_page に移送する.

    new_balloon_id: 移送先ページで balloon が衝突回避のため別 id に採番されたとき、
    子テキストの parent_balloon_id をそちらに付け替える。空なら旧 id を維持.
    """
    from ..io import schema
    from ..operators.text_op import _allocate_text_id

    if src_page is None or dst_page is None or not balloon_id:
        return
    target_balloon_id = str(new_balloon_id or balloon_id)
    # まず移送対象のテキスト id を集める (移送中に collection が変動するため snapshot)
    target_text_ids = [
        str(getattr(entry, "id", "") or "")
        for entry in src_page.texts
        if str(getattr(entry, "parent_balloon_id", "") or "") == balloon_id
    ]
    for text_id in target_text_ids:
        # src_page から取り出す (id でマッチ)
        src_entry = None
        for e in src_page.texts:
            if str(getattr(e, "id", "") or "") == text_id:
                src_entry = e
                break
        if src_entry is None:
            continue
        existing_dst_ids = {str(getattr(t, "id", "") or "") for t in dst_page.texts}
        final_id = text_id if text_id and text_id not in existing_dst_ids else _allocate_text_id(dst_page)
        new_entry = dst_page.texts.add()
        schema.text_entry_from_dict(new_entry, schema.text_entry_to_dict(src_entry))
        new_entry.id = final_id
        new_entry.parent_balloon_id = target_balloon_id
        # 位置補正
        _shift_entry_for_page_change(context, src_page, dst_page, new_entry)
        # 元削除 (id でマッチ)
        for i, e in enumerate(src_page.texts):
            if str(getattr(e, "id", "") or "") == text_id:
                src_page.texts.remove(i)
                break
