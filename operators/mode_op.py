"""overview 編集モード / コマ編集モードの切替 Operator.

モード切替時の .blend 入出力:
- **enter_panel_mode**: 現在の work.blend を save → panel_NNN.blend を open
  (panel_NNN.blend が未作成なら、現在の mainfile をそのまま save_as で新規化)
- **exit_panel_mode**: 現在の panel_NNN.blend を save → work.blend を open
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import Operator

from ..core.mode import MODE_PAGE, MODE_PANEL, get_mode, set_mode
from ..core.work import get_active_page, get_work
from ..io import blend_io
from ..utils import geom, log, paths

_logger = log.get_logger(__name__)


def _resolve_panel_at_event(context, event) -> tuple[int, int] | None:
    """``event.mouse_x/y`` の位置から (page_index, panel_index) を逆引き.

    VIEW_3D エリアに乗っていない場合は None。overview モードなら全ページを
    走査、OFF なら active ページのみ。Z 順最大 (最前面) のヒットを返す。
    """
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    # panel_picker ヘルパを遅延 import (operators→utils の循環依存回避)
    from . import panel_picker

    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if not (
                region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height
            ):
                continue
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if rv3d is None:
                continue
            mx = event.mouse_x - region.x
            my = event.mouse_y - region.y
            loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
            if loc is None:
                continue
            x_mm = geom.m_to_mm(loc.x)
            y_mm = geom.m_to_mm(loc.y)
            return panel_picker.find_panel_at_world_mm(work, x_mm, y_mm)
    return None


class BNAME_OT_enter_panel_mode(Operator):
    """選択中 or マウス直下のコマの 3D シーンに入る (コマ編集モード).

    work.blend を保存し、panel_NNN.blend を開く。未作成なら現シーンを
    そのまま save_as_mainfile で新規化する。

    invoke(event) ではマウス直下のコマを優先的に逆引きして active を更新
    (キーマップのダブルクリックや UI 操作から呼び出される)。execute のみ
    の場合は現在の active をそのまま使う。
    """

    bl_idname = "bname.enter_panel_mode"
    bl_label = "コマ編集モードへ"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return (
            work is not None
            and work.loaded
            and bool(work.work_dir)
            and get_mode(context) == MODE_PAGE
        )

    def invoke(self, context, event):
        # ダブルクリックからの起動: マウス直下のコマへ active をフォーカス
        hit = _resolve_panel_at_event(context, event)
        if hit is not None:
            work = get_work(context)
            page_idx, panel_idx = hit
            if work is not None and 0 <= page_idx < len(work.pages):
                work.active_page_index = page_idx
                page = work.pages[page_idx]
                if 0 <= panel_idx < len(page.panels):
                    page.active_panel_index = panel_idx
        return self.execute(context)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if (
            work is None
            or page is None
            or not (0 <= page.active_panel_index < len(page.panels))
        ):
            self.report({"WARNING"}, "編集対象のコマが選択されていません")
            return {"CANCELLED"}
        entry = page.panels[page.active_panel_index]
        stem = entry.panel_stem
        if not paths.is_valid_panel_stem(stem):
            self.report({"ERROR"}, f"不正なコマ stem: {stem}")
            return {"CANCELLED"}
        index = int(stem.split("_", 1)[1])
        work_dir = Path(work.work_dir)

        try:
            # 1) 現在の mainfile が work.blend なら上書き保存
            cur = blend_io.current_mainfile_path()
            expected_work = paths.work_blend_path(work_dir).resolve()
            if cur is not None and cur == expected_work:
                blend_io.save_current_as(expected_work)

            # 2) panel_NNN.blend を開く。未作成なら現シーンを新規保存して遷移。
            if blend_io.panel_blend_exists(work_dir, page.id, index):
                ok = blend_io.open_panel_blend(work_dir, page.id, index)
                if not ok:
                    self.report({"ERROR"}, "panel.blend を開けませんでした")
                    return {"CANCELLED"}
            else:
                ok = blend_io.save_panel_blend(work_dir, page.id, index)
                if not ok:
                    self.report({"ERROR"}, "panel.blend の新規作成に失敗")
                    return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001
            _logger.exception("enter_panel_mode failed")
            self.report({"ERROR"}, f"コマ編集モード遷移失敗: {exc}")
            return {"CANCELLED"}

        # load_post ハンドラがモード/stem を同期するが、念のため明示的にも設定
        set_mode(MODE_PANEL, context)
        context.scene.bname_current_panel_stem = stem
        self.report({"INFO"}, f"コマ編集モード: {stem}")
        return {"FINISHED"}


class BNAME_OT_exit_panel_mode(Operator):
    """コマ編集モードを抜けて overview モード (work.blend) へ戻る.

    panel_NNN.blend を保存し、work.blend を開く。
    """

    bl_idname = "bname.exit_panel_mode"
    bl_label = "紙面編集モードへ戻る"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return get_mode(context) == MODE_PANEL

    def execute(self, context):
        # 1) サムネイル生成 (panel.blend 切替前に現在の描画を記録)
        work = get_work(context)
        page = get_active_page(context)
        stem = getattr(context.scene, "bname_current_panel_stem", "")
        if (
            work is not None
            and work.loaded
            and page is not None
            and stem
            and paths.is_valid_panel_stem(stem)
        ):
            try:
                from . import thumbnail_op

                index = int(stem.split("_", 1)[1])
                out = paths.panel_thumb_path(Path(work.work_dir), page.id, index)
                thumbnail_op.take_area_screenshot(context, out)
            except Exception:  # noqa: BLE001
                _logger.exception("auto thumbnail failed on exit_panel_mode")

        # 2) 現在の panel.blend を保存 → work.blend を開く
        if (
            work is not None
            and work.loaded
            and page is not None
            and paths.is_valid_panel_stem(stem)
        ):
            work_dir = Path(work.work_dir)
            try:
                index = int(stem.split("_", 1)[1])
                cur = blend_io.current_mainfile_path()
                expected_panel = paths.panel_blend_path(work_dir, page.id, index).resolve()
                if cur is not None and cur == expected_panel:
                    blend_io.save_current_as(expected_panel)
                # work.blend を開く. 通常 work_new で必ず作られているはずで、
                # 無い場合は user が外部削除した等の異常系。現在開いている
                # panel.blend のシーンを work.blend として保存するとパネルの
                # 3D データが work.blend に紛れ込むため、その fallback は
                # 行わず、エラー報告だけして現状維持する。
                if blend_io.work_blend_exists(work_dir):
                    blend_io.open_work_blend(work_dir)
                else:
                    _logger.error(
                        "exit_panel_mode: work.blend not found at %s",
                        paths.work_blend_path(work_dir),
                    )
                    self.report(
                        {"ERROR"},
                        "work.blend が見つかりません. 作品フォルダの整合性を確認してください",
                    )
                    return {"CANCELLED"}
            except Exception as exc:  # noqa: BLE001
                _logger.exception("exit_panel_mode blend switch failed")
                self.report({"ERROR"}, f"work.blend 切替失敗: {exc}")
                return {"CANCELLED"}

        set_mode(MODE_PAGE, context)
        context.scene.bname_current_panel_stem = ""
        self.report({"INFO"}, "紙面編集モード")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_enter_panel_mode,
    BNAME_OT_exit_panel_mode,
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
