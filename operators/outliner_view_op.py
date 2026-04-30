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


class BNAME_OT_outliner_apply_view(bpy.types.Operator):
    """現在のウィンドウの Outliner を B-Name 表示 (VIEW_LAYER + alpha sort) に切替."""

    bl_idname = "bname.outliner_apply_view"
    bl_label = "Outliner を B-Name 表示に切替"
    bl_description = (
        "現在のウィンドウの Outliner を VIEW_LAYER 表示 + アルファ昇順ソートへ"
        "切替えます。元の設定はバックアップされ、復元 operator で戻せます。"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        window = context.window
        areas = _find_outliner_areas(window)
        if not areas:
            self.report({"WARNING"}, "Outliner エディタが見つかりません")
            return {"CANCELLED"}
        applied = 0
        for area in areas:
            for space in area.spaces:
                if space.type != "OUTLINER":
                    continue
                # バックアップ (1 度だけ)
                if "bname_outliner_backup" not in area:
                    area["bname_outliner_backup"] = {
                        "display_mode": str(getattr(space, "display_mode", "")),
                        "use_sort_alpha": bool(getattr(space, "use_sort_alpha", False)),
                    }
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
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        window = context.window
        areas = _find_outliner_areas(window)
        if not areas:
            self.report({"WARNING"}, "Outliner エディタが見つかりません")
            return {"CANCELLED"}
        restored = 0
        for area in areas:
            backup = area.get("bname_outliner_backup")
            if not backup:
                continue
            for space in area.spaces:
                if space.type != "OUTLINER":
                    continue
                try:
                    if backup.get("display_mode"):
                        space.display_mode = str(backup["display_mode"])
                except Exception:  # noqa: BLE001
                    pass
                try:
                    space.use_sort_alpha = bool(backup.get("use_sort_alpha", False))
                except Exception:  # noqa: BLE001
                    pass
                restored += 1
            try:
                del area["bname_outliner_backup"]
            except KeyError:
                pass
        if restored == 0:
            self.report({"INFO"}, "復元対象のバックアップがありません")
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
