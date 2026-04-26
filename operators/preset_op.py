"""用紙プリセット適用・保存・削除 Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_work
from ..io import presets, work_io
from ..utils import log

_logger = log.get_logger(__name__)


# Blender の EnumProperty callback は返した文字列への参照を保持しないため
# GC でクラッシュすることがある (公式既知の不具合)。モジュールレベルで
# キャッシュを保持して回避する。
_PRESET_ENUM_CACHE: list[tuple[str, str, str]] = []
_SUPPRESS_SELECTOR_UPDATE = False


def _preset_enum_items(_self, context):
    global _PRESET_ENUM_CACHE
    work = get_work(context)
    work_dir = Path(work.work_dir) if (work and work.loaded and work.work_dir) else None
    cache: list[tuple[str, str, str]] = []
    for p in presets.list_all_presets(work_dir):
        label = p.name if p.source == "global" else f"{p.name} (作品)"
        cache.append((p.name, label, p.description))
    if not cache:
        cache.append(("", "(プリセットなし)", ""))
    _PRESET_ENUM_CACHE = cache
    return _PRESET_ENUM_CACHE


def _on_paper_preset_selector_change(self, context):
    """WindowManager.bname_paper_preset_selector の変更時に用紙プリセットを即時適用."""
    global _SUPPRESS_SELECTOR_UPDATE
    if _SUPPRESS_SELECTOR_UPDATE:
        return
    name = getattr(self, "bname_paper_preset_selector", "")
    if not name:
        return
    work = get_work(context)
    if not (work and work.loaded):
        return
    work_dir = Path(work.work_dir) if work.work_dir else None
    preset = presets.load_preset_by_name(name, work_dir)
    if preset is None:
        return
    presets.apply_preset_to_paper(preset, work.paper)
    _logger.info("paper preset applied via selector: %s", preset.name)


def sync_paper_preset_selector(context) -> None:
    """現在の ``work.paper.preset_name`` に selector を合わせる."""
    global _SUPPRESS_SELECTOR_UPDATE

    work = get_work(context)
    if not (work and work.loaded):
        return
    name = (getattr(work.paper, "preset_name", "") or "").strip()
    if not name:
        return
    work_dir = Path(work.work_dir) if work.work_dir else None
    preset = presets.load_preset_by_name(name, work_dir)
    if preset is None:
        return
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bname_paper_preset_selector"):
        return
    cur = getattr(wm, "bname_paper_preset_selector", "")
    if cur == name:
        return
    _preset_enum_items(None, context)
    _SUPPRESS_SELECTOR_UPDATE = True
    try:
        wm.bname_paper_preset_selector = name
    finally:
        _SUPPRESS_SELECTOR_UPDATE = False


class BNAME_OT_paper_preset_apply(Operator):
    """選択した用紙プリセットを現在の作品に適用."""

    bl_idname = "bname.paper_preset_apply"
    bl_label = "用紙プリセットを適用"
    bl_options = {"REGISTER", "UNDO"}

    preset_name: EnumProperty(  # type: ignore[valid-type]
        name="プリセット",
        items=_preset_enum_items,
    )

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work = get_work(context)
        if not (work and work.loaded):
            return {"CANCELLED"}
        work_dir = Path(work.work_dir) if work.work_dir else None
        preset = presets.load_preset_by_name(self.preset_name, work_dir)
        if preset is None:
            self.report({"ERROR"}, f"プリセットが見つかりません: {self.preset_name}")
            return {"CANCELLED"}
        presets.apply_preset_to_paper(preset, work.paper)
        sync_paper_preset_selector(context)
        self.report({"INFO"}, f"プリセット適用: {preset.name}")
        return {"FINISHED"}


class BNAME_OT_paper_preset_save_local(Operator):
    """現在の用紙設定を作品ローカルプリセットとして保存."""

    bl_idname = "bname.paper_preset_save_local"
    bl_label = "用紙プリセットとして保存"
    bl_options = {"REGISTER"}

    preset_name: StringProperty(  # type: ignore[valid-type]
        name="プリセット名",
        default="",
    )
    description: StringProperty(  # type: ignore[valid-type]
        name="説明",
        default="",
    )

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and w.work_dir)

    def invoke(self, context, event):
        work = get_work(context)
        self.preset_name = work.paper.preset_name or "新規プリセット"
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work = get_work(context)
        if not self.preset_name.strip():
            self.report({"ERROR"}, "プリセット名が空です")
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        try:
            out = presets.save_local_preset(
                work_dir, work.paper, self.preset_name, self.description
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("preset_save_local failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        work.paper.preset_name = self.preset_name
        try:
            sync_paper_preset_selector(context)
            work_io.save_work_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("preset_save_local post-save sync failed")
            self.report({"WARNING"}, f"プリセット保存後の同期に失敗: {exc}")
        self.report({"INFO"}, f"ローカルプリセット保存: {out.name}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_paper_preset_apply,
    BNAME_OT_paper_preset_save_local,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bname_paper_preset_selector = EnumProperty(
        name="プリセット",
        description="用紙プリセットを選択して即時適用",
        items=_preset_enum_items,
        update=_on_paper_preset_selector_change,
    )


def unregister() -> None:
    try:
        del bpy.types.WindowManager.bname_paper_preset_selector
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
