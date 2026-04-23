"""作品 (.bname) の新規作成・オープン・保存・クローズ Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper, ExportHelper

from ..core.work import get_work
from ..io import page_io, presets, work_io
from ..utils import log, paths

_logger = log.get_logger(__name__)


def _apply_phase1_defaults(work) -> None:
    """新規作品のワンショット既定値セット (計画書 4.6 準拠)."""
    # DisplayItem のうち workName / episode はデフォルト ON
    work.work_info.display_work_name.enabled = True
    work.work_info.display_work_name.position = "bottom-left"
    work.work_info.display_episode.enabled = True
    work.work_info.display_episode.position = "bottom-left"
    work.work_info.display_subtitle.enabled = False
    work.work_info.display_subtitle.position = "bottom-center"
    work.work_info.display_author.enabled = False
    work.work_info.display_author.position = "bottom-right"
    # 既定プリセット適用 (見つからなくても既定値は PropertyGroup に入っている)
    presets.load_default_preset(work.paper)


class BNAME_OT_work_new(Operator, ExportHelper):
    """新規作品を作成 (.bname ディレクトリを生成).

    既存の同名ディレクトリがあれば作成を中止する (安全のため上書き禁止)。
    """

    bl_idname = "bname.work_new"
    bl_label = "新規作品を作成"
    bl_options = {"REGISTER"}

    filename_ext = paths.BNAME_DIR_SUFFIX
    filter_glob: StringProperty(default="*.bname", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        work = get_work(context)
        if work is None:
            self.report({"ERROR"}, "シーンに B-Name データが見つかりません")
            return {"CANCELLED"}

        selected = Path(self.filepath)
        work_dir = paths.ensure_bname_suffix(selected)
        if work_dir.exists():
            self.report({"ERROR"}, f"既に存在します: {work_dir.name}")
            return {"CANCELLED"}

        # 既存の作品データをリセットしてから新規作成
        work.pages.clear()
        work.active_page_index = -1
        work.loaded = False

        try:
            work_io.create_bname_skeleton(work_dir)
            _apply_phase1_defaults(work)
            work.work_dir = str(work_dir.resolve())
            work.loaded = True
            work.work_info.work_name = work_dir.stem
            work_io.save_work_json(work_dir, work)
            page_io.save_pages_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("work_new failed")
            work.loaded = False
            self.report({"ERROR"}, f"作成失敗: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"作品を作成: {work_dir.name}")
        return {"FINISHED"}


class BNAME_OT_work_open(Operator, ImportHelper):
    """既存の .bname 作品フォルダを開く."""

    bl_idname = "bname.work_open"
    bl_label = "作品を開く"
    bl_options = {"REGISTER"}

    filename_ext = paths.BNAME_DIR_SUFFIX
    filter_glob: StringProperty(default="*.bname", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        work = get_work(context)
        if work is None:
            self.report({"ERROR"}, "シーンに B-Name データが見つかりません")
            return {"CANCELLED"}

        selected = Path(self.filepath)
        # ファイルを選ばれても親ディレクトリを作品ルートとして解釈
        work_dir = selected if selected.suffix == paths.BNAME_DIR_SUFFIX else selected.parent
        if not work_dir.is_dir() or work_dir.suffix != paths.BNAME_DIR_SUFFIX:
            self.report({"ERROR"}, f".bname フォルダを指定してください: {work_dir}")
            return {"CANCELLED"}

        try:
            work_io.load_work_json(work_dir, work)
            page_io.load_pages_json(work_dir, work)
        except FileNotFoundError as exc:
            _logger.exception("work_open: missing file")
            work.loaded = False
            self.report({"ERROR"}, f"ファイルが見つかりません: {exc}")
            return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001
            _logger.exception("work_open failed")
            work.loaded = False
            self.report({"ERROR"}, f"読み込み失敗: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"作品を開きました: {work_dir.name}")
        return {"FINISHED"}


class BNAME_OT_work_save(Operator):
    """現在の作品データ (work.json / pages.json) を保存."""

    bl_idname = "bname.work_save"
    bl_label = "作品を保存"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded and work.work_dir)

    def execute(self, context):
        work = get_work(context)
        work_dir = Path(work.work_dir)
        if not work_dir.is_dir():
            self.report({"ERROR"}, f"作品ディレクトリが見つかりません: {work_dir}")
            return {"CANCELLED"}
        try:
            work_io.save_work_json(work_dir, work)
            page_io.save_pages_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("work_save failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, "作品を保存しました")
        return {"FINISHED"}


class BNAME_OT_work_close(Operator):
    """作品を閉じる (データをメモリから解放、ディスクは触らない)."""

    bl_idname = "bname.work_close"
    bl_label = "作品を閉じる"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded)

    def execute(self, context):
        work = get_work(context)
        work.pages.clear()
        work.active_page_index = -1
        work.loaded = False
        work.work_dir = ""
        self.report({"INFO"}, "作品を閉じました")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_work_new,
    BNAME_OT_work_open,
    BNAME_OT_work_save,
    BNAME_OT_work_close,
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
