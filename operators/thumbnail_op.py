"""コマのスクショサムネイル/高品質プレビュー生成 Operator.

計画書 3.4.3 / 8.8 参照。コマ編集モード終了時に panel_NNN_thumb.png を
ビューポートスクショで更新 (レンダリング不使用)。ユーザー手動で
panel_NNN_preview.png を低解像度レンダリング。
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..utils import log, paths

_logger = log.get_logger(__name__)


def _find_view3d_area(context):
    """現在の window で最初に見つかる VIEW_3D area を返す."""
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return None, None, None
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "WINDOW":
                    return window, area, region
    return None, None, None


def take_area_screenshot(context, out_path: Path) -> bool:
    """VIEW_3D area のスクリーンショットを取得して保存.

    ``bpy.ops.screen.screenshot_area()`` を ``temp_override`` で使う。
    失敗時は False を返す (GPUOffScreen フォールバックは未実装)。
    """
    window, area, region = _find_view3d_area(context)
    if window is None or area is None:
        _logger.warning("no VIEW_3D area found for screenshot")
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.screen.screenshot_area(filepath=str(out_path))
        return True
    except Exception as exc:  # noqa: BLE001 - Blender の一部バージョンで属性欠けあり
        _logger.warning("screenshot_area failed: %s", exc)
        return False


class BNAME_OT_panel_update_thumb(Operator):
    """選択中コマのスクショサムネを生成."""

    bl_idname = "bname.panel_update_thumb"
    bl_label = "コマサムネイルを更新"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return page is not None and 0 <= page.active_panel_index < len(page.panels)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        entry = page.panels[page.active_panel_index]
        paths.validate_panel_stem(entry.panel_stem)
        index = int(entry.panel_stem.split("_", 1)[1])
        out = paths.panel_thumb_path(Path(work.work_dir), page.id, index)
        if take_area_screenshot(context, out):
            self.report({"INFO"}, f"サムネイル保存: {out.name}")
            return {"FINISHED"}
        self.report({"WARNING"}, "サムネイル取得に失敗しました")
        return {"CANCELLED"}


class BNAME_OT_panel_generate_preview(Operator):
    """選択中コマを低解像度レンダリングして高品質プレビューを生成."""

    bl_idname = "bname.panel_generate_preview"
    bl_label = "高品質プレビュー生成"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return page is not None and 0 <= page.active_panel_index < len(page.panels)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        entry = page.panels[page.active_panel_index]
        paths.validate_panel_stem(entry.panel_stem)
        index = int(entry.panel_stem.split("_", 1)[1])
        out = paths.panel_preview_path(Path(work.work_dir), page.id, index)
        out.parent.mkdir(parents=True, exist_ok=True)

        scene = context.scene
        prev_filepath = scene.render.filepath
        prev_percent = scene.render.resolution_percentage
        try:
            scene.render.filepath = str(out)
            scene.render.resolution_percentage = 25  # 低解像度
            bpy.ops.render.render(write_still=True)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_generate_preview failed")
            self.report({"ERROR"}, f"プレビュー生成失敗: {exc}")
            return {"CANCELLED"}
        finally:
            scene.render.filepath = prev_filepath
            scene.render.resolution_percentage = prev_percent

        self.report({"INFO"}, f"プレビュー保存: {out.name}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_panel_update_thumb,
    BNAME_OT_panel_generate_preview,
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
