"""3D アセット連携 Operator (Phase 4).

計画書 3.4.4 / 3.4.5 / 8.13 参照。アセットブラウザからのリンク追加は
Blender 標準 UI を使うため、ここでは:
- リンク元 .blend を subprocess で開く
- 現在選択中のオブジェクトのリンク情報を cNN.json に記録
のみを提供する。
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import Operator

from ..core.mode import MODE_COMA, get_mode
from ..core.work import find_page_by_id, get_work
from ..io import coma_io
from ..utils import bpy_link, log, paths

_logger = log.get_logger(__name__)


class BNAME_OT_open_link_source(Operator):
    """選択中オブジェクトのリンク元 .blend を新しい Blender で開く."""

    bl_idname = "bname.open_link_source"
    bl_label = "リンク元ファイルを開く"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None:
            return False
        return bool(
            (getattr(obj, "library", None) and obj.library.filepath)
            or (getattr(obj, "data", None) and getattr(obj.data, "library", None) and obj.data.library.filepath)
        )

    def execute(self, context):
        obj = context.active_object
        candidates = list(bpy_link.find_linked_filepaths(obj))
        if not candidates:
            self.report({"ERROR"}, "リンク元ファイルが見つかりません")
            return {"CANCELLED"}
        target = candidates[0]
        proc = bpy_link.open_in_new_blender(target)
        if proc is None:
            self.report({"ERROR"}, f"Blender 起動に失敗: {target}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"別 Blender で開きました: {target.name}")
        return {"FINISHED"}


class BNAME_OT_record_asset_link(Operator):
    """コマ編集モード中、選択中オブジェクトのリンク参照を cNN.json に記録."""

    bl_idname = "bname.record_asset_link"
    bl_label = "このリンクを記録"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        if get_mode(context) != MODE_COMA:
            return False
        obj = context.active_object
        return obj is not None

    def execute(self, context):
        work = get_work(context)
        stem = getattr(context.scene, "bname_current_coma_id", "")
        page_id = getattr(context.scene, "bname_current_coma_page_id", "")
        page = find_page_by_id(work, page_id)
        if work is None or page is None or not stem:
            self.report({"ERROR"}, "コマ編集モード + アクティブコマが必要です")
            return {"CANCELLED"}
        if not paths.is_valid_coma_id(stem):
            self.report({"ERROR"}, f"不正なコマ stem: {stem}")
            return {"CANCELLED"}
        entry = _find_coma_by_stem(page, stem)
        if entry is None:
            self.report({"ERROR"}, f"コマエントリが見つかりません: {stem}")
            return {"CANCELLED"}
        obj = context.active_object
        link_id = _make_link_id(obj)
        if not link_id:
            self.report({"ERROR"}, "リンク情報が取得できません")
            return {"CANCELLED"}
        # 既存の layer_refs に同じ ID が無ければ追加
        for existing in entry.layer_refs:
            if existing.layer_id == link_id:
                self.report({"INFO"}, "既に記録済みです")
                return {"CANCELLED"}
        ref = entry.layer_refs.add()
        ref.layer_id = link_id
        try:
            coma_io.save_coma_meta(Path(work.work_dir), page.id, entry)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("record_asset_link failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"リンク記録: {link_id}")
        return {"FINISHED"}


def _find_coma_by_stem(page, stem: str):
    for entry in page.comas:
        if entry.coma_id == stem:
            return entry
    return None


def _make_link_id(obj: bpy.types.Object) -> str:
    """Object 名 + ライブラリパスから識別用 ID 文字列を合成."""
    lib = getattr(obj, "library", None)
    lib_path = lib.filepath if lib else ""
    return f"link:{obj.name}|{lib_path}"


_CLASSES = (
    BNAME_OT_open_link_source,
    BNAME_OT_record_asset_link,
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
