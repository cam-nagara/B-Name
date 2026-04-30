"""Outliner ⇔ B-Name の active page/coma 双方向同期.

3 つの「アクティブ階層」表現の同期を取る:
- Outliner 側: ``view_layer.active_layer_collection``
- B-Name 側: ``work.active_page_index`` / ``page.active_coma_index`` /
  ``scene.bname_current_coma_id``
- (副) ``scene.bname_active_layer_kind`` も連動

仕組:
- ``bpy.msgbus.subscribe_rna`` で ``ViewLayer.active_layer_collection`` を
  監視 → Outliner 変更を即時検出 (poll 不要)。
  (Blender 5.1.1 では ``LayerCollection.active`` プロパティは存在しないため
  このキーを使用する。msgbus 購読は best-effort で、失敗しても下記
  depsgraph フォールバックが同期を担う。)
- ``depsgraph_update_post`` フォールバック (msgbus 失効時 / B-Name 側変化を
  Outliner に反映する逆方向 sync の駆動)。
- ``_LAST_SYNCED`` キャッシュで「直近で揃えた状態」を記憶し、Outliner と
  B-Name のどちらが先に変わったかを判定:
    Outliner != _LAST_SYNCED → Outliner が変わった → B-Name に反映
    上記でなければ B-Name != _LAST_SYNCED → B-Name が変わった → Outliner に反映
- ``_SYNCING`` 再入禁止フラグで depsgraph 再帰を防ぐ。

設計:
- B-Name 管理外 Collection の active 変化は無視 (read-only ガード)。
- コマ編集モード (cNN.blend) では同期しない (1 コマ前提)。
"""

from __future__ import annotations

from typing import Optional

import bpy
from bpy.app.handlers import persistent

from ..core.mode import MODE_COMA, get_mode
from . import log
from . import object_naming as on

_logger = log.get_logger(__name__)

# msgbus subscriber owner (固有 owner で unsubscribe を絞り込める)
_OWNER = object()

# 直近で同期した (page_id, coma_id) をキャッシュ
_LAST_SYNCED: tuple[str, str] = ("", "")

# depsgraph_update_post 内で発火させた書込みが、Blender 側 depsgraph 更新を
# 経由してまた _on_depsgraph_update_post を呼ぶループを防ぐ再入禁止フラグ。
_SYNCING: bool = False


# ---------- ヘルパ ----------

def _resolve_active_collection(context) -> Optional[bpy.types.Collection]:
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


def _outliner_active_state(context) -> tuple[str, str]:
    """Outliner の active Collection から (page_id, coma_id) を抽出.

    B-Name 管理 Collection でなければ ``("", "")`` を返す (= "未確定")。
    その場合は B-Name 側を正と見て逆方向 sync が走る。
    """
    coll = _resolve_active_collection(context)
    if coll is None:
        return "", ""
    if not bool(coll.get("bname_managed", False)):
        return "", ""
    kind = str(coll.get("bname_kind", "") or "")
    bname_id = str(coll.get("bname_id", "") or "")
    if not kind or not bname_id:
        return "", ""
    if kind == "page":
        return bname_id, ""
    if kind == "coma":
        if ":" in bname_id:
            page_id, coma_id = bname_id.split(":", 1)
            return page_id, coma_id
        parent_key = str(coll.get("bname_parent_key", "") or "")
        return parent_key, bname_id
    return "", ""


def _bname_active_state(scene, work) -> tuple[str, str]:
    """B-Name 側プロパティから (page_id, coma_id) を抽出.

    優先順位:
        1. ``scene.bname_current_coma_id`` が空でなく、active page にその
           coma があれば coma_id 採用
        2. ``page.active_coma_index >= 0`` ならそのコマの id
        3. coma なし (ページ直下)
    """
    pages = getattr(work, "pages", None)
    if not pages:
        return "", ""
    idx = int(getattr(work, "active_page_index", -1))
    if not (0 <= idx < len(pages)):
        return "", ""
    page = pages[idx]
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return "", ""
    comas = getattr(page, "comas", None) or []

    current_coma_id = str(getattr(scene, "bname_current_coma_id", "") or "")
    if current_coma_id:
        for cm in comas:
            if str(getattr(cm, "id", "") or "") == current_coma_id:
                return page_id, current_coma_id

    coma_idx = int(getattr(page, "active_coma_index", -1))
    if 0 <= coma_idx < len(comas):
        return page_id, str(getattr(comas[coma_idx], "id", "") or "")

    return page_id, ""


# ---------- forward sync (Outliner → B-Name) ----------

def _apply_to_bname(scene, work, page_id: str, coma_id: str) -> None:
    """B-Name 側プロパティを (page_id, coma_id) に揃える."""
    page_idx = _resolve_page_index(work, page_id)
    if page_idx < 0:
        return
    if int(getattr(work, "active_page_index", -1)) != page_idx:
        try:
            work.active_page_index = page_idx
        except Exception:  # noqa: BLE001
            _logger.exception("active_page_index 設定失敗")
    page = work.pages[page_idx]
    if coma_id:
        coma_idx = _resolve_coma_index(page, coma_id)
        if coma_idx >= 0 and int(getattr(page, "active_coma_index", -1)) != coma_idx:
            try:
                page.active_coma_index = coma_idx
            except Exception:  # noqa: BLE001
                _logger.exception("active_coma_index 設定失敗")
    else:
        # page 選択時は active_coma_index を -1 にして「ページ直下」を明示
        if int(getattr(page, "active_coma_index", -1)) != -1:
            try:
                page.active_coma_index = -1
            except Exception:  # noqa: BLE001
                _logger.exception("active_coma_index リセット失敗")
    try:
        if str(getattr(scene, "bname_current_coma_id", "") or "") != coma_id:
            scene.bname_current_coma_id = coma_id
    except Exception:  # noqa: BLE001
        pass
    if hasattr(scene, "bname_active_layer_kind"):
        desired = "coma" if coma_id else "page"
        try:
            if str(getattr(scene, "bname_active_layer_kind", "") or "") != desired:
                scene.bname_active_layer_kind = desired
        except Exception:  # noqa: BLE001
            pass


# ---------- reverse sync (B-Name → Outliner) ----------

def _find_layer_collection(root_lc, target_coll):
    """LayerCollection ツリーから target Collection を探す."""
    if root_lc is None or target_coll is None:
        return None
    if root_lc.collection is target_coll:
        return root_lc
    for child in root_lc.children:
        found = _find_layer_collection(child, target_coll)
        if found is not None:
            return found
    return None


def _apply_to_outliner(view_layer, page_id: str, coma_id: str) -> None:
    """Outliner の active_layer_collection を (page_id, coma_id) に揃える.

    対応する Collection (page or coma) を探して
    ``view_layer.active_layer_collection`` にセットする。
    """
    if view_layer is None:
        return
    target_coll = None
    if coma_id:
        target_coll = on.find_collection_by_bname_id(
            f"{page_id}:{coma_id}", kind="coma"
        )
    if target_coll is None and page_id:
        target_coll = on.find_collection_by_bname_id(page_id, kind="page")
    if target_coll is None:
        return
    target_lc = _find_layer_collection(view_layer.layer_collection, target_coll)
    if target_lc is None:
        return
    cur = getattr(view_layer, "active_layer_collection", None)
    if cur is target_lc:
        return
    try:
        view_layer.active_layer_collection = target_lc
    except Exception:  # noqa: BLE001
        _logger.exception("active_layer_collection 設定失敗")


# ---------- 双方向同期エントリポイント ----------

def _sync(context=None) -> None:
    """Outliner ⇔ B-Name の双方向同期メイン.

    どちらが新しく変わったかは ``_LAST_SYNCED`` キャッシュとの差分で判定。
    """
    global _LAST_SYNCED, _SYNCING
    if _SYNCING:
        return
    try:
        ctx = context or bpy.context
        if ctx is None:
            return
        if get_mode(ctx) == MODE_COMA:
            return
        scene = getattr(ctx, "scene", None)
        if scene is None:
            return
        work = getattr(scene, "bname_work", None)
        if work is None or not getattr(work, "loaded", False):
            return

        out_state = _outliner_active_state(ctx)  # Outliner が指す状態
        b_state = _bname_active_state(scene, work)  # B-Name 側状態

        # どちらかが _LAST_SYNCED と異なれば、その「異なる側」が新しく変わった
        # 側だと判定して、もう片方を追従させる。
        # ただし Outliner 側が ("", "") (= B-Name 管理外/未選択) の場合、
        # 「ユーザーが他の Collection を選んだ」という解釈で B-Name 側は
        # 触らない (read-only ガード)。
        outliner_changed = bool(out_state[0]) and out_state != _LAST_SYNCED
        bname_changed = bool(b_state[0]) and b_state != _LAST_SYNCED

        if not outliner_changed and not bname_changed:
            return

        _SYNCING = True
        try:
            if outliner_changed:
                # Outliner 側を正として B-Name に反映
                _apply_to_bname(scene, work, out_state[0], out_state[1])
                _LAST_SYNCED = out_state
                _logger.debug(
                    "sync Outliner→B-Name: page=%s coma=%s",
                    out_state[0], out_state[1],
                )
            elif bname_changed:
                # B-Name 側を正として Outliner に反映
                _apply_to_outliner(ctx.view_layer, b_state[0], b_state[1])
                _LAST_SYNCED = b_state
                _logger.debug(
                    "sync B-Name→Outliner: page=%s coma=%s",
                    b_state[0], b_state[1],
                )
        finally:
            _SYNCING = False
    except Exception:  # noqa: BLE001
        _logger.exception("active collection sync failed")
        _SYNCING = False


# 後方互換: 旧 API 名 (operator やテストコードからの呼出に対応)
_sync_from_active_collection = _sync


# ---------- 公開 API: knife cut 等から「アクティブを X コマに固定」したいとき ----------

def request_active_coma(context, page_id: str, coma_id: str) -> None:
    """指定の page/coma を Outliner & B-Name 両方の active にセット.

    ``coma_knife_cut`` 等で新規コマを作った直後に「カット直後の右側コマ
    (active_coma_index で指定したコマ) を Outliner でも選択状態に」したい
    ケース向け。``__masks__`` Collection や ``bname_master_sketch`` が
    自動 active になってしまう副作用を上書きで打ち消す。
    """
    global _LAST_SYNCED, _SYNCING
    if context is None or not page_id:
        return
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    work = getattr(scene, "bname_work", None)
    if work is None or not getattr(work, "loaded", False):
        return
    _SYNCING = True
    try:
        # 先に B-Name 側を確実にする
        _apply_to_bname(scene, work, page_id, coma_id)
        # 続けて Outliner を該当 Collection に
        _apply_to_outliner(context.view_layer, page_id, coma_id)
        # ``bname_master_sketch`` が誤って active になっていたら解除
        try:
            view_layer = context.view_layer
            cur_active = getattr(view_layer.objects, "active", None)
            if cur_active is not None and getattr(cur_active, "name", "") == "bname_master_sketch":
                view_layer.objects.active = None
                cur_active.select_set(False)
        except Exception:  # noqa: BLE001
            pass
        _LAST_SYNCED = (page_id, coma_id)
    finally:
        _SYNCING = False


# ---------- 通知ハンドラ ----------

def _msgbus_callback() -> None:
    _sync()


@persistent
def _on_depsgraph_update_post(scene, depsgraph) -> None:
    _sync()


@persistent
def _on_load_post(_filepath: str) -> None:
    global _LAST_SYNCED
    _LAST_SYNCED = ("", "")
    _resubscribe_msgbus()


def _resubscribe_msgbus() -> None:
    try:
        bpy.msgbus.clear_by_owner(_OWNER)
    except Exception:  # noqa: BLE001
        pass
    # Blender 5.1.1 では LayerCollection.active プロパティは存在しないため、
    # ViewLayer.active_layer_collection を購読対象にする。
    # msgbus は best-effort: 失敗しても depsgraph_update_post フォールバックが
    # 同期を駆動するため、例外は DEBUG ログのみで握りつぶす。
    try:
        bpy.msgbus.subscribe_rna(
            key=(bpy.types.ViewLayer, "active_layer_collection"),
            owner=_OWNER,
            args=(),
            notify=_msgbus_callback,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.debug(
            "msgbus subscribe skipped (ViewLayer.active_layer_collection): %s",
            exc,
        )


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
