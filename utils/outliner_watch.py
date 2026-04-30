"""Outliner D&D の検出と低頻度 sync (Phase 1).

計画書 ``docs/outliner_object_layer_plan_2026-04-30.md`` §5.2 を実装する。

Phase 1 では「**検出と警告ログ**」までを担当し、Outliner で D&D された Object
の親 Collection 変化を 5 秒以上の低頻度 timer scan で拾う。実 entry
(`BNameImageLayer.parent_key` 等) への書戻しは Phase 3 (画像/raster Object
化完了) と同時にこの timer のコールバック内で行う想定。

**再帰抑止**: ``layer_object_sync.suppress_sync()`` ガードと差分キャッシュで
fire 数を最小化する (計画書 §5.3)。
"""

from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

from . import log
from . import layer_object_sync as los
from . import object_naming as on

_logger = log.get_logger(__name__)

# scan 間隔 (秒)。計画書 §5.3 で「1 秒以下にすると Undo 中に再帰する事例が
# あるため 5 秒以上推奨」としている。
SCAN_INTERVAL_SECONDS = 5.0

# scan の世代番号 (アドオン unregister 時に既存タイマーを失効させるため)
_scan_generation = 0

# 現世代の tick 関数参照 (timers.unregister 用)
_active_tick = None


def _writeback_raster_parent(scene, obj, new_kind: str, new_key: str) -> bool:
    """Outliner D&D を ``BNameRasterLayer`` に書き戻す (Phase 3a).

    対応する parent_kind マッピング:
        - "page" → ``parent_kind="page"``
        - "coma" → ``parent_kind="coma"``
        - "outside" / "none" → ``parent_kind="none"`` / ``parent_key=""``
        - "folder" → 未対応 (BNameRasterLayer.parent_kind に folder enum が無い)。
                    警告ログのみ。

    Returns:
        書戻しを実行したら True。
    """
    raster_id = str(obj.get("bname_id", "") or "")
    if not raster_id:
        return False
    coll = getattr(scene, "bname_raster_layers", None)
    if coll is None:
        return False
    entry = None
    for e in coll:
        if str(getattr(e, "id", "") or "") == raster_id:
            entry = e
            break
    if entry is None:
        return False
    if new_kind == "folder":
        _logger.warning(
            "raster %s: folder への移動は Phase 3a では未対応 (skip)", raster_id
        )
        return False
    if new_kind in {"outside", "none"}:
        new_parent_kind = "none"
        new_parent_key = ""
    elif new_kind in {"page", "coma"}:
        new_parent_kind = new_kind
        new_parent_key = new_key
    else:
        return False
    # 既に同値なら no-op (再帰検出を避ける)
    if (
        str(getattr(entry, "parent_kind", "") or "") == new_parent_kind
        and str(getattr(entry, "parent_key", "") or "") == new_parent_key
    ):
        return False
    with los.suppress_sync():
        try:
            entry.parent_kind = new_parent_kind
        except Exception:  # noqa: BLE001
            _logger.exception("raster writeback: parent_kind set failed")
            return False
        try:
            entry.parent_key = new_parent_key
        except Exception:  # noqa: BLE001
            _logger.exception("raster writeback: parent_key set failed")
            return False
        try:
            obj["bname_parent_key"] = new_parent_key
        except Exception:  # noqa: BLE001
            pass
        los.update_snapshot(obj)
    # UIList 再描画 (parent_kind/parent_key には update callback が無いため)
    try:
        for area in bpy.context.screen.areas if bpy.context.screen else ():
            if area.type in {"VIEW_3D", "PROPERTIES"}:
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass
    _logger.info(
        "raster writeback: %s parent → %s/%s",
        raster_id,
        new_parent_kind,
        new_parent_key,
    )
    return True


def _writeback_image_parent(scene, obj, new_kind: str, new_key: str) -> bool:
    """Outliner D&D を ``BNameImageLayer`` に書き戻す (Phase 3b).

    BNameImageLayer.parent_kind は StringProperty (free-form) で、folder も
    扱える。folder への移動は entry.folder_key 経由で表現する。
    """
    image_id = str(obj.get("bname_id", "") or "")
    if not image_id:
        return False
    coll = getattr(scene, "bname_image_layers", None)
    if coll is None:
        return False
    entry = None
    for e in coll:
        if str(getattr(e, "id", "") or "") == image_id:
            entry = e
            break
    if entry is None:
        return False

    # マッピング
    if new_kind in {"outside", "none"}:
        new_parent_kind = "none"
        new_parent_key = ""
        new_folder_key = ""
    elif new_kind == "page":
        new_parent_kind = "page"
        new_parent_key = new_key
        new_folder_key = ""
    elif new_kind == "coma":
        new_parent_kind = "coma"
        new_parent_key = new_key
        new_folder_key = ""
    elif new_kind == "folder":
        # folder へ入れた場合は parent_kind を "folder"、folder_key にフォルダ ID。
        # parent_key は元の page/coma を維持したいが、Outliner からは親は
        # フォルダ Collection しか分からないので、folder の bname_parent_key
        # から親の page/coma を引く。
        new_parent_kind = "folder"
        new_folder_key = new_key
        # fold collection の親の bname_id を取って parent_key にする
        from . import object_naming as on

        folder_coll = on.find_collection_by_bname_id(new_key, kind="folder")
        if folder_coll is not None:
            # フォルダ Collection 自体の親 (page or coma) を逆引き
            for parent_coll in bpy.data.collections:
                if folder_coll.name in parent_coll.children:
                    pkind = on.get_kind(parent_coll)
                    if pkind in {"page", "coma"}:
                        new_parent_kind = pkind  # 上書き: 実体の親種別
                        new_parent_key = on.get_bname_id(parent_coll)
                        break
            else:
                new_parent_key = ""
        else:
            new_parent_key = ""
    else:
        return False

    if (
        str(getattr(entry, "parent_kind", "") or "") == new_parent_kind
        and str(getattr(entry, "parent_key", "") or "") == new_parent_key
        and str(getattr(entry, "folder_key", "") or "") == new_folder_key
    ):
        return False
    with los.suppress_sync():
        try:
            entry.parent_kind = new_parent_kind
        except Exception:  # noqa: BLE001
            _logger.exception("image writeback: parent_kind set failed")
            return False
        try:
            entry.parent_key = new_parent_key
        except Exception:  # noqa: BLE001
            _logger.exception("image writeback: parent_key set failed")
            return False
        try:
            entry.folder_key = new_folder_key
        except Exception:  # noqa: BLE001
            _logger.exception("image writeback: folder_key set failed")
            return False
        try:
            obj["bname_parent_key"] = new_parent_key
            obj["bname_folder_id"] = new_folder_key
        except Exception:  # noqa: BLE001
            pass
        los.update_snapshot(obj)
    try:
        for area in bpy.context.screen.areas if bpy.context.screen else ():
            if area.type in {"VIEW_3D", "PROPERTIES"}:
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass
    _logger.info(
        "image writeback: %s parent → %s/%s folder=%s",
        image_id, new_parent_kind, new_parent_key, new_folder_key,
    )
    return True


def _resolve_parent_kind_key_folder(new_kind: str, new_key: str) -> tuple[str, str, str]:
    """Outliner の new_kind/new_key を ``(parent_kind, parent_key, folder_key)``
    に変換する共通ヘルパ.

    image / balloon / text 系で folder への移動を扱う。folder の場合、
    フォルダ Collection の親 (page or coma) を逆引きして parent_kind に
    反映する。逆引きできなかった場合は ``("", "", "")`` を返し、呼出側で
    writeback を skip させる (entry.parent_kind="folder" は EnumProperty では
    無効値のため代入例外を防ぐ)。
    """
    if new_kind in {"outside", "none"}:
        return "none", "", ""
    if new_kind == "page":
        return "page", new_key, ""
    if new_kind == "coma":
        return "coma", new_key, ""
    if new_kind == "folder":
        folder_coll = on.find_collection_by_bname_id(new_key, kind="folder")
        if folder_coll is not None:
            for parent_coll in bpy.data.collections:
                if any(cc is folder_coll for cc in parent_coll.children):
                    pkind = on.get_kind(parent_coll)
                    if pkind in {"page", "coma"}:
                        return pkind, on.get_bname_id(parent_coll), new_key
        # 親の page/coma が辿れなかった: writeback skip
        return "", "", ""
    return "", "", ""


def _writeback_balloon_parent(scene, obj, new_kind: str, new_key: str) -> bool:
    """``BNameBalloonEntry`` を Outliner D&D に追従させる (Phase 4)."""
    from . import balloon_text_plane as btp

    balloon_id = str(obj.get("bname_id", "") or "")
    if not balloon_id:
        return False
    page, entry = btp.find_balloon_entry(scene, balloon_id)
    if entry is None:
        return False
    new_pk, new_pkey, new_fk = _resolve_parent_kind_key_folder(new_kind, new_key)
    if not new_pk:
        return False
    if (
        str(getattr(entry, "parent_kind", "") or "") == new_pk
        and str(getattr(entry, "parent_key", "") or "") == new_pkey
        and str(getattr(entry, "folder_key", "") or "") == new_fk
    ):
        return False
    with los.suppress_sync():
        try:
            entry.parent_kind = new_pk
            entry.parent_key = new_pkey
            entry.folder_key = new_fk
            obj["bname_parent_key"] = new_pkey
            obj["bname_folder_id"] = new_fk
            los.update_snapshot(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("balloon writeback failed")
            return False
    _logger.info(
        "balloon writeback: %s parent → %s/%s folder=%s",
        balloon_id, new_pk, new_pkey, new_fk,
    )
    return True


def _writeback_effect_parent(scene, obj, new_kind: str, new_key: str) -> bool:
    """効果線 GP Object の Outliner D&D を反映 (Phase 5b).

    効果線は実 entry を持たず、Object custom property
    (``bname_parent_key``) のみが正。watch 検出時に Object 側を最新化する
    だけで write-back 完了。
    """
    new_pk, new_pkey, new_fk = _resolve_parent_kind_key_folder(new_kind, new_key)
    if not new_pk:
        return False
    if (
        str(obj.get("bname_parent_key", "") or "") == new_pkey
        and str(obj.get("bname_folder_id", "") or "") == new_fk
    ):
        return False
    with los.suppress_sync():
        try:
            obj["bname_parent_key"] = new_pkey
            obj["bname_folder_id"] = new_fk
            los.update_snapshot(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("effect writeback failed")
            return False
    _logger.info(
        "effect writeback: %s parent → %s/%s folder=%s",
        obj.get("bname_id", ""), new_pk, new_pkey, new_fk,
    )
    return True


def _writeback_text_parent(scene, obj, new_kind: str, new_key: str) -> bool:
    """``BNameTextEntry`` を Outliner D&D に追従させる (Phase 4)."""
    from . import balloon_text_plane as btp

    text_id = str(obj.get("bname_id", "") or "")
    if not text_id:
        return False
    page, entry = btp.find_text_entry(scene, text_id)
    if entry is None:
        return False
    new_pk, new_pkey, new_fk = _resolve_parent_kind_key_folder(new_kind, new_key)
    if not new_pk:
        return False
    if (
        str(getattr(entry, "parent_kind", "") or "") == new_pk
        and str(getattr(entry, "parent_key", "") or "") == new_pkey
        and str(getattr(entry, "folder_key", "") or "") == new_fk
    ):
        return False
    with los.suppress_sync():
        try:
            entry.parent_kind = new_pk
            entry.parent_key = new_pkey
            entry.folder_key = new_fk
            obj["bname_parent_key"] = new_pkey
            obj["bname_folder_id"] = new_fk
        except Exception:  # noqa: BLE001
            _logger.exception("text writeback failed")
            return False
    _logger.info(
        "text writeback: %s parent → %s/%s folder=%s",
        text_id, new_pk, new_pkey, new_fk,
    )
    return True


def _scan_once() -> float | None:
    """1 回分の scan。差分があれば実 entry へ反映する.

    対応 kind (Phase 4 時点):
        - raster: BNameRasterLayer
        - image: BNameImageLayer
        - balloon: BNameBalloonEntry (page.balloons)
        - text: BNameTextEntry (page.texts)
        - その他 (gp / effect): 警告ログのみ (Phase 5b で対応)
    """
    if los.is_sync_in_progress():
        return SCAN_INTERVAL_SECONDS
    try:
        scene = bpy.context.scene
        if scene is None:
            return SCAN_INTERVAL_SECONDS
        changes = los.detect_outliner_changes(scene)
        if changes:
            for obj, new_kind, new_key in changes:
                kind = str(obj.get("bname_kind", "") or "")
                if kind == "raster":
                    _writeback_raster_parent(scene, obj, new_kind, new_key)
                elif kind == "image":
                    _writeback_image_parent(scene, obj, new_kind, new_key)
                elif kind == "balloon":
                    _writeback_balloon_parent(scene, obj, new_kind, new_key)
                elif kind == "text":
                    _writeback_text_parent(scene, obj, new_kind, new_key)
                elif kind in {"effect", "effect_legacy", "gp"}:
                    _writeback_effect_parent(scene, obj, new_kind, new_key)
                else:
                    _logger.info(
                        "outliner watch: %s (kind=%s) → %s/%s "
                        "(write-back 未対応 kind)",
                        obj.name, kind, new_kind, new_key,
                    )
    except Exception:  # noqa: BLE001
        _logger.exception("outliner watch scan failed")
    return SCAN_INTERVAL_SECONDS


def _make_tick(generation: int):
    def _tick():
        if generation != _scan_generation:
            return None
        return _scan_once()

    return _tick


@persistent
def _on_load_post(_filepath: str) -> None:
    """.blend ロード後に scan timer を再起動 (load_post で世代が変わるため)."""
    schedule_watch_timer()


def schedule_watch_timer() -> None:
    """timer を起動 (既存 timer は世代カウンタ + 明示 unregister で停止)."""
    global _scan_generation, _active_tick
    # 既存 tick を unregister
    if _active_tick is not None:
        try:
            if bpy.app.timers.is_registered(_active_tick):
                bpy.app.timers.unregister(_active_tick)
        except Exception:  # noqa: BLE001
            pass
        _active_tick = None
    _scan_generation += 1
    gen = _scan_generation
    tick = _make_tick(gen)
    _active_tick = tick
    try:
        bpy.app.timers.register(
            tick,
            first_interval=SCAN_INTERVAL_SECONDS,
            persistent=True,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("outliner watch timer register failed")


def cancel_watch_timer() -> None:
    """既存 timer を実 unregister + 世代カウンタで失効させる."""
    global _scan_generation, _active_tick
    _scan_generation += 1
    if _active_tick is not None:
        try:
            if bpy.app.timers.is_registered(_active_tick):
                bpy.app.timers.unregister(_active_tick)
        except Exception:  # noqa: BLE001
            pass
        _active_tick = None
    los.clear_snapshots()


def register() -> None:
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    schedule_watch_timer()


def unregister() -> None:
    cancel_watch_timer()
    if _on_load_post in bpy.app.handlers.load_post:
        try:
            bpy.app.handlers.load_post.remove(_on_load_post)
        except ValueError:
            pass
