"""Outliner Object/Collection ミラーと差分検出 sync.

計画書 ``docs/outliner_object_layer_plan_2026-04-30.md`` Phase 0/1。

責務:
    1. ``BNameWorkData`` (page / coma / layer_folder / image / raster / GP) を
       読み、対応する Collection / Object を ``utils/outliner_model.py`` 経由で
       生成・整合させる (mirror)。
    2. depsgraph_update_post / msgbus / timer scan で Outliner D&D を検出し、
       Object の現所属 Collection から ``parent_kind`` / ``parent_key`` を
       逆方向に反映する (Phase 1 で実装、ここではフックの土台のみ)。
    3. 計画書 §5.3 の再帰抑止 guard を提供する。

Phase 0 では (1) と (3) を実装。(2) のフルパス detection は Phase 1 で拡張。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

import bpy

from . import log
from . import object_naming as on
from . import outliner_model as om

_logger = log.get_logger(__name__)


# ---------- 再帰抑止 guard (計画書 §5.3) ----------

_SYNC_IN_PROGRESS = False


@contextmanager
def suppress_sync():
    """B-Name operator 実行中の depsgraph 再帰を抑止するコンテキスト.

    使用例:
        with suppress_sync():
            obj.location.z = z

    ネストしても外側のフラグが立っている限り内側は no-op。
    """
    global _SYNC_IN_PROGRESS
    if _SYNC_IN_PROGRESS:
        yield
        return
    _SYNC_IN_PROGRESS = True
    try:
        yield
    finally:
        _SYNC_IN_PROGRESS = False


def is_sync_in_progress() -> bool:
    return _SYNC_IN_PROGRESS


# ---------- 差分キャッシュ (再 fire 抑止) ----------

# 前回 scan 時の (parent_collection_name, location_z, parent_key, folder_id) を
# **bname_id** キーで保持する。obj.name キーだとリネームでリーク + 同名再生成で
# 偶発継承する事故が起きるため安定 ID を採用。
_LAST_SNAPSHOT: dict[str, tuple] = {}


def _snapshot_key(obj: bpy.types.Object) -> str:
    """snapshot のキー。bname_id 優先、無ければ obj.name fallback."""
    bid = str(obj.get(on.PROP_ID, "") or "")
    return bid if bid else f"@name:{obj.name}"


def _snapshot_for(obj: bpy.types.Object) -> tuple:
    parent_coll = om.find_managed_parent_collection(obj)
    parent_name = parent_coll.name if parent_coll is not None else ""
    z = round(float(obj.location.z), 6)
    parent_key = obj.get(on.PROP_PARENT_KEY, "")
    folder_id = obj.get(on.PROP_FOLDER_ID, "")
    return (parent_name, z, str(parent_key), str(folder_id))


def has_changed(obj: bpy.types.Object) -> bool:
    snap = _snapshot_for(obj)
    return _LAST_SNAPSHOT.get(_snapshot_key(obj)) != snap


def update_snapshot(obj: bpy.types.Object) -> None:
    _LAST_SNAPSHOT[_snapshot_key(obj)] = _snapshot_for(obj)


def clear_snapshots() -> None:
    _LAST_SNAPSHOT.clear()


def prune_snapshots(valid_bname_ids: set[str]) -> int:
    """指定された有効 bname_id 以外の snapshot を削除. orphan 解消用."""
    stale = [k for k in _LAST_SNAPSHOT if not k.startswith("@name:") and k not in valid_bname_ids]
    for k in stale:
        del _LAST_SNAPSHOT[k]
    return len(stale)


# ---------- Z 座標と prefix (計画書 §4.2) ----------

# 1 z_index あたりの Z 座標オフセット (m)。0.1mm。
BNAME_Z_STEP_M = 0.0001


def z_for_index(z_index: int) -> float:
    return float(z_index) * BNAME_Z_STEP_M


def apply_z_index(obj: bpy.types.Object, z_index: int) -> None:
    """Object の ``location.z`` と name prefix を ``z_index`` から再生成."""
    obj[on.PROP_Z_INDEX] = int(z_index)
    try:
        loc = obj.location
        obj.location = (loc.x, loc.y, z_for_index(z_index))
    except Exception:  # noqa: BLE001
        pass
    kind = on.get_kind(obj)
    bname_id = on.get_bname_id(obj)
    title = str(obj.get(on.PROP_TITLE, "") or "")
    if kind and bname_id:
        on.assign_canonical_name(
            obj, kind=kind, z_index=int(z_index), sub_id=kind, title=title
        )


# ---------- 作品全体の mirror 同期 (Phase 0 の中核) ----------


def _mirror_image_text_empties(scene, work) -> None:
    """全 BNameImageLayer / BNameTextEntry に対応する Empty Object を ensure."""
    try:
        from . import empty_layer_object as elo

        # 旧 Plane 方式の Object/Mesh/Material/Image を掃除 (Empty 化移行)
        try:
            elo.cleanup_legacy_plane_objects()
        except Exception:  # noqa: BLE001
            _logger.exception("legacy plane cleanup failed")

        # image_layers (scene 直下)
        coll = getattr(scene, "bname_image_layers", None) if scene is not None else None
        if coll is not None:
            for entry in coll:
                parent_key = str(getattr(entry, "parent_key", "") or "")
                page_id = parent_key.split(":", 1)[0] if parent_key else ""
                page = None
                for p in getattr(work, "pages", []):
                    if str(getattr(p, "id", "") or "") == page_id:
                        page = p
                        break
                if page is None and len(work.pages) > 0:
                    page = work.pages[0]
                if page is None:
                    continue
                elo.ensure_image_empty_object(scene=scene, entry=entry, page=page)

        # texts (page.texts)
        for page in getattr(work, "pages", []):
            for entry in getattr(page, "texts", []):
                elo.ensure_text_empty_object(scene=scene, entry=entry, page=page)
    except Exception:  # noqa: BLE001
        _logger.exception("mirror image/text empties failed")


def mirror_work_to_outliner(scene: bpy.types.Scene, work) -> None:
    """``work`` の page/coma/folder 配列から Collection 階層を生成・整合.

    既存 Collection は ``bname_id`` で逆引きして再利用する。
    work が未ロード (``loaded`` False) の場合は何もしない (Outliner に
    意味のない空の B-Name 階層を作らない)。
    """
    if scene is None or work is None:
        return
    if not bool(getattr(work, "loaded", False)):
        return
    with suppress_sync():
        om.ensure_root_collection(scene)
        om.ensure_outside_collection(scene)
        for page in getattr(work, "pages", []):
            page_id = str(getattr(page, "id", "") or "")
            if not page_id:
                continue
            title = str(getattr(page, "title", "") or page_id)
            om.ensure_page_collection(scene, page_id, title)
            for coma in getattr(page, "comas", []):
                coma_id = str(getattr(coma, "id", "") or "")
                if not coma_id:
                    continue
                coma_title = str(getattr(coma, "title", "") or coma_id)
                om.ensure_coma_collection(scene, page_id, coma_id, coma_title)
        # 画像 / テキストの Empty Object を ensure (オーバーレイ描画と並列)
        _mirror_image_text_empties(scene, work)

        # 用紙背景 (opaque Mesh) を全ページ分 ensure。BLENDED ラスター
        # 材質の depth 不在を補い、ラスター paint の上に被さらないように
        # する。GPU overlay 用紙塗りの代替。
        try:
            from . import paper_bg_object as _pbg

            _pbg.regenerate_all_paper_bgs(scene, work)
        except Exception:  # noqa: BLE001
            _logger.exception("mirror paper backgrounds failed")

        for folder in getattr(work, "layer_folders", []):
            folder_id = str(getattr(folder, "id", "") or "")
            if not folder_id:
                continue
            title = str(getattr(folder, "title", "") or folder_id)
            parent_key_raw = str(getattr(folder, "parent_key", "") or "")
            parent_kind, parent_key = _split_folder_parent(parent_key_raw)
            # NOTE: 現状の BNameLayerFolder には z_order フィールドが無い。
            # Phase 1 で z_order を実フィールド化するか、layer_stack の
            # 順序から導出する。それまでは 0 fallback で複数フォルダが
            # F0000 prefix に潰れるが、bname_id で識別できるので機能上の
            # 問題はない (alpha sort では `.001` 自動付加で揺れる)。
            z_index = int(getattr(folder, "z_order", 0) or 0)
            om.ensure_folder_collection(
                scene,
                folder_id=folder_id,
                title=title,
                parent_kind=parent_kind,
                parent_key=parent_key,
                z_index=z_index,
            )


def _split_folder_parent(parent_key_raw: str) -> tuple[str, str]:
    """フォルダの ``parent_key`` 文字列を ``(parent_kind, parent_key)`` に分解.

    既存 ``utils/layer_reparent.py`` と同じ規約に揃える:
        - ``""`` -> outside
        - ``pNNNN`` -> page
        - ``pNNNN:cNN`` -> coma
        - その他 (folder_xxx) -> folder
    """
    if not parent_key_raw:
        return ("none", "")
    if ":" in parent_key_raw:
        return ("coma", parent_key_raw)
    if parent_key_raw.startswith("p") or parent_key_raw.startswith("P"):
        return ("page", parent_key_raw)
    return ("folder", parent_key_raw)


# ---------- Object 側ミラー (画像 / raster / GP の 3 種を Phase 0 で対応) ----------


def _resolve_page_world_offset_mm(scene, parent_key: str) -> tuple[float, float]:
    """parent_key の page 部分から page_grid の world オフセット (mm) を取得."""
    if scene is None or not parent_key:
        return (0.0, 0.0)
    page_id = parent_key.split(":", 1)[0] if parent_key else ""
    if not page_id:
        return (0.0, 0.0)
    work = getattr(scene, "bname_work", None)
    if work is None:
        return (0.0, 0.0)
    pages = list(getattr(work, "pages", []))
    page_idx = -1
    for i, p in enumerate(pages):
        if str(getattr(p, "id", "") or "") == page_id:
            page_idx = i
            break
    if page_idx < 0:
        return (0.0, 0.0)
    try:
        from . import page_grid as _pg

        return _pg.page_total_offset_mm(work, scene, page_idx)
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)


def stamp_layer_object(
    obj: bpy.types.Object,
    *,
    kind: str,
    bname_id: str,
    title: str,
    z_index: int,
    parent_kind: str,
    parent_key: str,
    folder_id: str = "",
    scene: Optional[bpy.types.Scene] = None,
    apply_page_offset: bool = True,
) -> None:
    """既存の Object に B-Name メタデータを書き込み、所属 Collection を整合.

    呼出側は既に ``bpy.data.objects.new()`` 等で Object を生成済みであることを
    前提とする。ここでは custom property 設定と link 整合を行う。

    ``apply_page_offset=True`` (既定) の場合、parent_key から所属ページを引いて
    page_grid 経由の world X/Y オフセットを Object.location.x/y に設定する。
    Mesh / Curve 頂点をページローカル座標で持っているレイヤーが、Page Browser
    モードの page グリッド上で正しい位置に重なるようにするため。
    オーバーレイ描画系で entry.x_mm/y_mm を独自管理する Empty レイヤーは
    apply_page_offset=False を渡して Object.location を別途制御する。
    """
    on.stamp_identity(
        obj,
        kind=kind,
        bname_id=bname_id,
        title=title,
        z_index=z_index,
        parent_key=parent_key,
        folder_id=folder_id,
    )
    on.assign_canonical_name(
        obj, kind=kind, z_index=z_index, sub_id=kind, title=title
    )
    apply_z_index(obj, z_index)
    # page world オフセットを X/Y に反映 (apply_z_index は Z のみ触る)
    if apply_page_offset and scene is not None:
        try:
            from .geom import mm_to_m as _mm_to_m

            ox_mm, oy_mm = _resolve_page_world_offset_mm(scene, parent_key)
            loc = obj.location
            obj.location = (
                _mm_to_m(ox_mm), _mm_to_m(oy_mm), loc.z
            )
        except Exception:  # noqa: BLE001
            _logger.exception("stamp_layer_object: page offset 設定失敗")
    if scene is not None:
        om.link_object_to_parent(
            scene, obj, parent_kind=parent_kind, parent_key=parent_key, folder_id=folder_id
        )
    update_snapshot(obj)


def detect_outliner_changes(scene: bpy.types.Scene) -> list[tuple[bpy.types.Object, str, str]]:
    """B-Name 管理 Object のうち、現所属 Collection が ``parent_key`` と
    乖離しているものを返す.

    Phase 0 では呼出側 (timer scan) でこの戻り値を見て警告ログを出すだけ。
    Phase 1 で実反映 (entries の parent_key 書換え) を加える。

    Returns:
        ``[(obj, new_parent_kind, new_parent_key), ...]``。
    """
    if _SYNC_IN_PROGRESS:
        return []
    changes: list[tuple[bpy.types.Object, str, str]] = []
    for obj in on.iter_managed_objects():
        if not has_changed(obj):
            continue
        parent_coll = om.find_managed_parent_collection(obj)
        if parent_coll is None:
            update_snapshot(obj)
            continue
        new_kind, new_key = om.parent_key_from_collection(parent_coll)
        old_key = str(obj.get(on.PROP_PARENT_KEY, "") or "")
        if new_key != old_key:
            changes.append((obj, new_kind, new_key))
        update_snapshot(obj)
    return changes
