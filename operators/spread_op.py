"""ページの見開き変更・解除 Operator (計画書 3.3.4)."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty
from bpy.types import Operator

from ..core.work import get_work
from ..io import page_io
from ..utils import log, paths

_logger = log.get_logger(__name__)


class BNAME_OT_pages_merge_spread(Operator):
    """連続 2 ページを見開きに統合 (ダイアログ付き)."""

    bl_idname = "bname.pages_merge_spread"
    bl_label = "見開きに変更"
    bl_options = {"REGISTER"}

    left_index: IntProperty(  # type: ignore[valid-type]
        name="左ページ index",
        default=-1,
        min=-1,
    )
    tombo_aligned: BoolProperty(  # type: ignore[valid-type]
        name="トンボを合わせる",
        default=True,
    )
    tombo_gap_mm: FloatProperty(  # type: ignore[valid-type]
        name="間隔 (mm)",
        description="負値はページを重ねる方向",
        default=-9.60,
    )
    remove_empty_layers: BoolProperty(  # type: ignore[valid-type]
        name="描画されていないレイヤーを削除",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and len(w.pages) >= 2)

    def invoke(self, context, event):
        work = get_work(context)
        if self.left_index < 0:
            self.left_index = work.active_page_index
        return context.window_manager.invoke_props_dialog(self, width=450)

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        left = self.left_index
        if not (0 <= left < len(work.pages) - 1):
            layout.label(text="左ページの選択が不正です", icon="ERROR")
            return
        a = work.pages[left]
        b = work.pages[left + 1]
        col = layout.column()
        col.label(text=f"{a.title} と {b.title} を見開きとして結合します")
        col.separator()
        col.label(
            text="対象ページの編集内容は保存され、この動作は取り消せません。",
            icon="INFO",
        )
        col.separator()
        col.prop(self, "tombo_aligned")
        sub = col.column()
        sub.enabled = self.tombo_aligned
        sub.prop(self, "tombo_gap_mm")
        col.prop(self, "remove_empty_layers")

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        left = self.left_index
        if not (0 <= left < len(work.pages) - 1):
            self.report({"ERROR"}, "左ページの選択が不正です")
            return {"CANCELLED"}
        a = work.pages[left]
        b = work.pages[left + 1]
        if a.spread or b.spread:
            self.report({"ERROR"}, "既に見開きのページは結合できません")
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)

        # 結合先 ID を採番 (元の 2 ページ ID で "0020-0021" 形式)
        try:
            head_a = int(a.id.split("-", 1)[0])
            head_b = int(b.id.split("-", 1)[0])
        except ValueError:
            self.report({"ERROR"}, "ページ ID が不正です")
            return {"CANCELLED"}
        spread_id = paths.format_spread_id(head_a, head_b)

        a_id = a.id
        b_id = b.id
        try:
            # ディレクトリを作成 (空の骨格のみ)。panel は Phase 2 以降でコピー予定。
            page_io.ensure_page_dir(work_dir, spread_id)
            # 元の 2 ページディレクトリを削除 (現状 panel ファイルは未実装のため
            # 空に近い。Phase 2 で panel コピー処理に置き換える想定)。
            page_io.remove_page_dir(work_dir, a_id)
            page_io.remove_page_dir(work_dir, b_id)
            # collection: 右ページを先に削除して index のずれを避ける
            work.pages.remove(left + 1)
            merged = work.pages[left]
            merged.id = spread_id
            merged.title = f"{head_a}-{head_b}"
            merged.dir_rel = f"{paths.PAGES_DIR_NAME}/{spread_id}/"
            merged.spread = True
            merged.tombo_aligned = self.tombo_aligned
            merged.tombo_gap_mm = self.tombo_gap_mm
            merged.original_pages.clear()
            r1 = merged.original_pages.add()
            r1.page_id = paths.format_page_id(head_a)
            r2 = merged.original_pages.add()
            r2.page_id = paths.format_page_id(head_b)
            page_io.save_pages_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("pages_merge_spread failed")
            self.report({"ERROR"}, f"見開き統合失敗: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"見開きに変更: {spread_id}")
        return {"FINISHED"}


class BNAME_OT_pages_split_spread(Operator):
    """見開きを 2 ページに戻す."""

    bl_idname = "bname.pages_split_spread"
    bl_label = "見開きを解除"
    bl_options = {"REGISTER"}

    spread_index: IntProperty(default=-1, min=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        if not (w and w.loaded):
            return False
        idx = w.active_page_index
        return 0 <= idx < len(w.pages) and w.pages[idx].spread

    def invoke(self, context, event):
        work = get_work(context)
        if self.spread_index < 0:
            self.spread_index = work.active_page_index
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        idx = self.spread_index
        if not (0 <= idx < len(work.pages)):
            return {"CANCELLED"}
        entry = work.pages[idx]
        if not entry.spread:
            self.report({"ERROR"}, "見開きページではありません")
            return {"CANCELLED"}
        if len(entry.original_pages) < 2:
            self.report({"ERROR"}, "結合元ページ情報が失われているため解除できません")
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        left_id = entry.original_pages[0].page_id
        right_id = entry.original_pages[1].page_id
        try:
            # 見開きディレクトリのコピーで 2 つに分割
            page_io.copy_page_dir(work_dir, entry.id, left_id)
            # left_id にコピーした後、元の見開きディレクトリは right_id に rename
            page_io.rename_page_dir(work_dir, entry.id, right_id)

            # collection を左右に置換: 左ページを add→idx へ移動→右ページを add→idx+1 へ移動
            work.pages.remove(idx)
            left = work.pages.add()
            left.id = left_id
            left.title = left_id
            left.dir_rel = f"{paths.PAGES_DIR_NAME}/{left_id}/"
            left.spread = False
            work.pages.move(len(work.pages) - 1, idx)
            right = work.pages.add()
            right.id = right_id
            right.title = right_id
            right.dir_rel = f"{paths.PAGES_DIR_NAME}/{right_id}/"
            right.spread = False
            work.pages.move(len(work.pages) - 1, idx + 1)
            work.active_page_index = idx
            page_io.save_pages_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("pages_split_spread failed")
            self.report({"ERROR"}, f"見開き解除失敗: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"見開きを解除: {left_id} / {right_id}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_pages_merge_spread,
    BNAME_OT_pages_split_spread,
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
