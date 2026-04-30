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

_logger = log.get_logger(__name__)

# scan 間隔 (秒)。計画書 §5.3 で「1 秒以下にすると Undo 中に再帰する事例が
# あるため 5 秒以上推奨」としている。
SCAN_INTERVAL_SECONDS = 5.0

# scan の世代番号 (アドオン unregister 時に既存タイマーを失効させるため)
_scan_generation = 0


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
    _logger.info(
        "raster writeback: %s parent → %s/%s",
        raster_id,
        new_parent_kind,
        new_parent_key,
    )
    return True


def _scan_once() -> float | None:
    """1 回分の scan。差分があれば実 entry へ反映する (raster は書戻し対応).

    Returns:
        次回までの秒数。停止する場合は None。
    """
    if los.is_sync_in_progress():
        # B-Name operator 実行中は次のスロットで再試行
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
                else:
                    # image / gp / balloon / text などは Phase 3b 以降で対応
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
    """timer を起動 (既存 timer は世代カウンタで失効させる)."""
    global _scan_generation
    _scan_generation += 1
    gen = _scan_generation
    try:
        bpy.app.timers.register(
            _make_tick(gen),
            first_interval=SCAN_INTERVAL_SECONDS,
            persistent=True,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("outliner watch timer register failed")


def cancel_watch_timer() -> None:
    """既存 timer を世代カウンタで失効させる (登録解除と同等の効果)."""
    global _scan_generation
    _scan_generation += 1
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
