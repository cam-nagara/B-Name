"""Outliner で選択された Collection を B-Name の active page/coma に同期.

Blender の Outliner で Collection をクリックすると ``LayerCollection`` の
``active`` が切替わる (= ``view_layer.active_layer_collection`` が更新)。
その Collection が B-Name 管理 (``bname_kind=="page"|"coma"``) なら、対応
する ``work.active_page_index`` / ``page.active_coma_index`` /
``scene.bname_active_layer_kind`` / ``scene.bname_current_coma_id`` 系を
同期して、3D ビューポート上のオーバーレイ・各種 operator が「いま
ユーザーが Outliner で選んだページ/コマ」を active として扱えるようにする。

主な仕組:
- ``bpy.msgbus.subscribe_rna`` で
  ``LayerCollection`` の ``active`` プロパティを監視し、変更時に同期処理を
  発火させる (poll 不要、選択した瞬間に反映)。
- depsgraph_update_post でも同じ同期処理を呼んでフォールバック (msgbus が
  Blender バージョン差や save/load 跨ぎで失効するケース対策)。

設計:
- 同期処理は **read-only**。Collection 選択を変更したり、書込み系の API
  を呼んだりしない。Blender 側の active を読んで B-Name 側 PropertyGroup の
  index を更新するだけ。
- コマ編集モード (cNN.blend) では同期しない (1 コマ前提のため意味なし)。
- 再帰防止: 同期処理が走った直後に Outliner 側を変えないので無問題。
"""

from __future__ import annotations

from typing import Optional

import bpy
from bpy.app.handlers import persistent

from ..core.mode import MODE_COMA, MODE_PAGE, get_mode
from . import log

_logger = log.get_logger(__name__)

# msgbus subscriber owner (固有 owner で unsubscribe を絞り込める)
_OWNER = object()

# 直近で同期した (page_id, coma_id) をキャッシュして頻繁な書込みを抑制
_LAST_SYNCED: tuple[str, str] = ("", "")

# depsgraph_update_post 内で発火させた書込みが、Blender 側 depsgraph 更新を
# 経由してまた _on_depsgraph_update_post を呼ぶループを防ぐ再入禁止フラグ。
_SYNCING: bool = False


def _resolve_active_collection(context) -> Optional[bpy.types.Collection]:
    """``view_layer.active_layer_collection.collection`` を取得 (None セーフ)."""
    if context is None:
        return None
    view_layer = getattr(context, "view_layer", None)
    if view_layer is None:
        return None
    active_lc = getattr(view_layer, "active_layer_collection", None)
    if active_lc is None:
        return None
    return getattr(active_lc, "collection", None)


def _resolve_page_index(work, page_id: str) -> int:
    if not page_id:
        return -1
    for i, pg in enumerate(work.pages):
        if str(getattr(pg, "id", "") or "") == page_id:
            return i
    return -1


def _resolve_coma_index(page, coma_id: str) -> int:
    if page is None or not coma_id:
        return -1
    comas = getattr(page, "comas", None) or []
    for i, cm in enumerate(comas):
        if str(getattr(cm, "id", "") or "") == coma_id:
            return i
    return -1


def _sync_from_active_collection(context=None) -> None:
    """``view_layer.active_layer_collection`` から page/coma index を同期.

    呼出は冪等で、active が変わっていなければ何もしない (early return)。
    """
    global _LAST_SYNCED, _SYNCING
    if _SYNCING:
        return
    try:
        ctx = context or bpy.context
        if ctx is None:
            return
        # コマ編集モード (cNN.blend) では Outliner 同期不要 (1 コマ前提)
        if get_mode(ctx) == MODE_COMA:
            return
        scene = getattr(ctx, "scene", None)
        if scene is None:
            return
        work = getattr(scene, "bname_work", None)
        if work is None or not getattr(work, "loaded", False):
            return

        coll = _resolve_active_collection(ctx)
        if coll is None:
            return
        # B-Name 管理 Collection でなければ無視 (= ユーザーが他の Collection を
        # 選んだ場合は B-Name 側 active を変更しない)
        if not bool(coll.get("bname_managed", False)):
            return
        kind = str(coll.get("bname_kind", "") or "")
        bname_id = str(coll.get("bname_id", "") or "")
        if not kind or not bname_id:
            return

        new_page_id = ""
        new_coma_id = ""
        if kind == "page":
            new_page_id = bname_id
        elif kind == "coma":
            # coma の bname_id は "<page_id>:<coma_id>" 形式
            if ":" in bname_id:
                new_page_id, new_coma_id = bname_id.split(":", 1)
            else:
                # 旧形式フォールバック (parent_key を見る)
                parent_key = str(coll.get("bname_parent_key", "") or "")
                new_page_id = parent_key
                new_coma_id = bname_id
        else:
            # folder/outside 等は同期対象外
            return

        if (new_page_id, new_coma_id) == _LAST_SYNCED:
            return

        # work.active_page_index 更新
        page_idx = _resolve_page_index(work, new_page_id)
        if page_idx < 0:
            _logger.debug(
                "active collection sync: page %s が work.pages に見つからない",
                new_page_id,
            )
            return

        # ここから書込み開始 → depsgraph 再入禁止
        _SYNCING = True
        try:
            if int(getattr(work, "active_page_index", -1)) != page_idx:
                try:
                    work.active_page_index = page_idx
                except Exception:  # noqa: BLE001
                    _logger.exception("active_page_index 設定失敗")

            page = work.pages[page_idx]

            # page.active_coma_index 更新:
            # - coma 選択時: 該当 index に
            # - page 選択時: -1 (= 「ページ直下選択」を意味する)。-1 にしないと
            #   active_target が前回の active_coma_index を引き継ぎ、ページ
            #   選択したのにコマ配下にレイヤーが作られてしまう。
            if new_coma_id:
                coma_idx = _resolve_coma_index(page, new_coma_id)
                if coma_idx >= 0 and int(getattr(page, "active_coma_index", -1)) != coma_idx:
                    try:
                        page.active_coma_index = coma_idx
                    except Exception:  # noqa: BLE001
                        _logger.exception("active_coma_index 設定失敗")
            else:
                if int(getattr(page, "active_coma_index", -1)) != -1:
                    try:
                        page.active_coma_index = -1
                    except Exception:  # noqa: BLE001
                        _logger.exception("active_coma_index リセット失敗")

            # scene.bname_current_coma_id (active_target が参照する) を反映。
            try:
                if str(getattr(scene, "bname_current_coma_id", "") or "") != new_coma_id:
                    scene.bname_current_coma_id = new_coma_id
            except Exception:  # noqa: BLE001
                pass

            # active layer kind を反映
            if hasattr(scene, "bname_active_layer_kind"):
                desired_kind = "coma" if new_coma_id else "page"
                try:
                    if str(getattr(scene, "bname_active_layer_kind", "") or "") != desired_kind:
                        scene.bname_active_layer_kind = desired_kind
                except Exception:  # noqa: BLE001
                    pass

            _LAST_SYNCED = (new_page_id, new_coma_id)
            _logger.info(
                "active collection sync: kind=%s page=%s coma=%s → "
                "active_page_index=%d active_coma_index=%d",
                kind, new_page_id, new_coma_id,
                page_idx,
                int(getattr(page, "active_coma_index", -1)),
            )
        finally:
            _SYNCING = False
    except Exception:  # noqa: BLE001
        _logger.exception("active collection sync failed")
        _SYNCING = False


def _msgbus_callback() -> None:
    """msgbus.subscribe_rna からの通知 (引数なし)."""
    _sync_from_active_collection()


@persistent
def _on_depsgraph_update_post(scene, depsgraph) -> None:
    """depsgraph_update_post フォールバック.

    msgbus が file load 直後や Blender バージョン差で失効するケースに備える。
    depsgraph_update は Outliner クリックでも発火するため、ここで read-only に
    同期しても余分なコストは小さい (early return が多い)。
    """
    _sync_from_active_collection()


@persistent
def _on_load_post(_filepath: str) -> None:
    """.blend load 後に msgbus 購読を再登録 (load で購読が解除されるため)."""
    global _LAST_SYNCED
    _LAST_SYNCED = ("", "")
    _resubscribe_msgbus()


def _resubscribe_msgbus() -> None:
    """LayerCollection.active を監視する msgbus 購読を (再) 登録."""
    try:
        bpy.msgbus.clear_by_owner(_OWNER)
    except Exception:  # noqa: BLE001
        pass
    try:
        bpy.msgbus.subscribe_rna(
            key=(bpy.types.LayerCollection, "active"),
            owner=_OWNER,
            args=(),
            notify=_msgbus_callback,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("msgbus subscribe failed (LayerCollection.active)")


def register() -> None:
    _resubscribe_msgbus()
    if _on_depsgraph_update_post not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update_post)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister() -> None:
    try:
        bpy.msgbus.clear_by_owner(_OWNER)
    except Exception:  # noqa: BLE001
        pass
    try:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update_post)
    except (ValueError, RuntimeError):
        pass
    try:
        bpy.app.handlers.load_post.remove(_on_load_post)
    except (ValueError, RuntimeError):
        pass
