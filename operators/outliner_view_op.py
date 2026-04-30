"""B-Name Outliner 表示切替 operator (Phase 1).

計画書 ``docs/outliner_object_layer_plan_2026-04-30.md`` §4.3 / Phase 0 作業 6
を実装。専用 workspace の作成は行わず、N パネルからユーザーが明示的に
「現在の Outliner エディタ」を B-Name 表示 (VIEW_LAYER + alpha sort) に切替/
復元できるようにする。

切替前の display_mode / use_sort_alpha は ``area["bname_outliner_backup"]`` に
退避し、復元 operator から元へ戻せる。
"""

from __future__ import annotations

import bpy

from ..utils import log

_logger = log.get_logger(__name__)


def _find_outliner_areas(window):
    """ウィンドウ内の Outliner area を列挙."""
    if window is None:
        return []
    return [a for a in window.screen.areas if a.type == "OUTLINER"]


_BACKUP_KEY = "bname_outliner_backup"
_MANAGED_KEY = "bname_outliner_managed"


class BNAME_OT_outliner_apply_view(bpy.types.Operator):
    """現在のウィンドウの Outliner を B-Name 表示 (VIEW_LAYER + alpha sort) に切替."""

    bl_idname = "bname.outliner_apply_view"
    bl_label = "Outliner を B-Name 表示に切替"
    bl_description = (
        "現在のウィンドウの Outliner を VIEW_LAYER 表示 + アルファ昇順ソートへ"
        "切替えます。元の設定はバックアップされ、復元 operator で戻せます。"
    )
    # screen レベル (area / space) の変更は Blender の Undo に乗らないため
    # UNDO オプションは外す (Ctrl+Z で戻らない期待値乖離を防ぐ)。
    bl_options = {"REGISTER"}

    def execute(self, context):
        window = context.window
        areas = _find_outliner_areas(window)
        if not areas:
            self.report({"WARNING"}, "Outliner エディタが見つかりません")
            return {"CANCELLED"}
        applied = 0
        for area in areas:
            # active space のみ対象 (全 spaces ループは backup 多重上書きの元)
            space = area.spaces.active
            if space is None or space.type != "OUTLINER":
                continue
            current_mode = str(getattr(space, "display_mode", ""))
            current_sort = bool(getattr(space, "use_sort_alpha", False))
            # バックアップは「現在が B-Name 表示と一致しないとき」だけ更新。
            # これにより apply 連打で初回値が保持され、apply→手動変更→apply
            # で手動変更後の値が新 backup に入る。
            already_in_bname_view = (
                current_mode == "VIEW_LAYER" and current_sort is True
            )
            if not already_in_bname_view:
                area[_BACKUP_KEY] = {
                    "display_mode": current_mode,
                    "use_sort_alpha": current_sort,
                }
            # B-Name 管理マーカー (restore 時の対象判定に使う)
            area[_MANAGED_KEY] = True
            try:
                space.display_mode = "VIEW_LAYER"
            except Exception:  # noqa: BLE001
                _logger.exception("set display_mode failed")
            try:
                space.use_sort_alpha = True
            except Exception:  # noqa: BLE001
                _logger.exception("set use_sort_alpha failed")
            applied += 1
        self.report({"INFO"}, f"Outliner {applied} 件を B-Name 表示へ切替えました")
        return {"FINISHED"}


class BNAME_OT_outliner_restore_view(bpy.types.Operator):
    """バックアップから Outliner 表示設定を復元."""

    bl_idname = "bname.outliner_restore_view"
    bl_label = "Outliner 表示を元に戻す"
    bl_description = "B-Name 表示への切替前の display_mode / use_sort_alpha に戻します。"
    bl_options = {"REGISTER"}

    def execute(self, context):
        window = context.window
        areas = _find_outliner_areas(window)
        if not areas:
            self.report({"WARNING"}, "Outliner エディタが見つかりません")
            return {"CANCELLED"}
        restored = 0
        for area in areas:
            # B-Name 管理マーカーが立っている area のみ復元
            if not bool(area.get(_MANAGED_KEY, False)):
                continue
            backup = area.get(_BACKUP_KEY)
            space = area.spaces.active
            if space is None or space.type != "OUTLINER":
                continue
            if backup:
                try:
                    if "display_mode" in backup:
                        mode_val = str(backup["display_mode"])
                        if mode_val:
                            space.display_mode = mode_val
                except Exception:  # noqa: BLE001
                    pass
                try:
                    space.use_sort_alpha = bool(backup.get("use_sort_alpha", False))
                except Exception:  # noqa: BLE001
                    pass
            restored += 1
            for key in (_BACKUP_KEY, _MANAGED_KEY):
                try:
                    del area[key]
                except KeyError:
                    pass
        if restored == 0:
            self.report({"INFO"}, "復元対象がありません (B-Name 管理マーカー無し)")
        else:
            self.report({"INFO"}, f"Outliner 表示を {restored} 件復元しました")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_outliner_apply_view,
    BNAME_OT_outliner_restore_view,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
