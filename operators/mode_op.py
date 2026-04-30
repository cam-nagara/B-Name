"""overview 編集モード / コマ編集モードの切替 Operator.

モード切替時の .blend 入出力:
- **enter_coma_mode**: 現在の work.blend を save → cNN.blend を open
  (cNN.blend が未作成なら、空 scene から新規生成)
- **exit_coma_mode**: 現在の cNN.blend を save → work.blend を open
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import Operator

from ..core.mode import MODE_PAGE, MODE_COMA, get_mode, set_mode
from ..core.work import get_active_page, get_work
from ..io import blend_io, page_io, work_io
from ..utils import geom, log, paths
from . import coma_modal_state

_logger = log.get_logger(__name__)


def _save_current_work_metadata(work, page) -> None:
    """mainfile 切替前に JSON 側へ現在の用紙/ページ状態を反映する."""
    if work is None or not getattr(work, "work_dir", ""):
        return
    work_dir = Path(work.work_dir)
    work_io.save_work_json(work_dir, work)
    page_io.save_pages_json(work_dir, work)
    if page is not None:
        page_io.save_page_json(work_dir, page)


def _resolve_coma_at_event(context, event) -> tuple[int, int] | None:
    """``event.mouse_x/y`` の位置から (page_index, coma_index) を逆引き.

    VIEW_3D エリアに乗っていない場合は None。overview モードなら全ページを
    走査、OFF なら active ページのみ。Z 順最大 (最前面) のヒットを返す。
    """
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    # coma_picker ヘルパを遅延 import (operators→utils の循環依存回避)
    from . import coma_picker

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
            return coma_picker.find_coma_at_world_mm(work, x_mm, y_mm)
    return None


class BNAME_OT_enter_coma_mode(Operator):
    """選択中 or マウス直下のコマの 3D シーンに入る (コマ編集モード).

    work.blend を保存し、cNN.blend を開く。未作成なら空の scene から
    cNN.blend を初期化する。

    invoke(event) ではマウス直下のコマを優先的に逆引きして active を更新
    (キーマップのダブルクリックや UI 操作から呼び出される)。execute のみ
    の場合は現在の active をそのまま使う。
    """

    bl_idname = "bname.enter_coma_mode"
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
        # Blender が Object モード以外 (例: GP 描画モード PAINT_GREASE_PENCIL,
        # Edit モード等) のときはダブルクリックを譲る (描画ストロークなどに干渉しない)。
        cur_mode = getattr(context, "mode", "")
        if cur_mode != "OBJECT":
            print(f"[B-Name][OP] enter_coma_mode: skip (context.mode={cur_mode!r})")
            return {"PASS_THROUGH"}
        print(f"[B-Name][OP] enter_coma_mode.invoke event.type={event.type} value={event.value}"
              f" poll_ok={self.__class__.poll(context)}")
        # ダブルクリックからの起動: マウス直下のコマへ active をフォーカス
        hit = _resolve_coma_at_event(context, event)
        if hit is None:
            # ダブルクリック時のみ、未ヒットなら現在の active panel に
            # フォールバックせず何もしない。UI ボタンからの EXEC_DEFAULT は
            # 従来どおり active panel を対象に execute へ入る。
            return {"PASS_THROUGH"}

        work = get_work(context)
        page_idx, coma_idx = hit
        if work is None or not (0 <= page_idx < len(work.pages)):
            return {"PASS_THROUGH"}
        page = work.pages[page_idx]
        if not (0 <= coma_idx < len(page.comas)):
            return {"PASS_THROUGH"}

        work.active_page_index = page_idx
        page.active_coma_index = coma_idx
        return self.execute(context)

    def execute(self, context):
        coma_modal_state.finish_all(context)
        work = get_work(context)
        page = get_active_page(context)
        if (
            work is None
            or page is None
            or not (0 <= page.active_coma_index < len(page.comas))
        ):
            self.report({"WARNING"}, "編集対象のコマが選択されていません")
            return {"CANCELLED"}
        entry = page.comas[page.active_coma_index]
        stem = entry.coma_id
        if not paths.is_valid_coma_id(stem):
            self.report({"ERROR"}, f"不正なコマ stem: {stem}")
            return {"CANCELLED"}
        page_id = page.id
        work_dir = Path(work.work_dir)

        try:
            # work.blend を開く前後の load_post は work.json を正として再同期する。
            # 用紙色や開始ページを UI で変えた直後に cNN.blend へ入っても
            # 古い JSON で巻き戻らないよう、mainfile 切替前に必ず保存する。
            _save_current_work_metadata(work, page)

            # 1) 現在の mainfile が work.blend なら上書き保存
            cur = blend_io.current_mainfile_path()
            expected_work = paths.work_blend_path(work_dir).resolve()
            if cur is not None and cur == expected_work:
                blend_io.save_current_as(expected_work)

            try:
                from ..utils import coma_camera

                coma_camera.ensure_reference_images(work, page_id, stem)
            except Exception:  # noqa: BLE001
                _logger.exception("enter_coma_mode: panel camera references failed")

            # 2) cNN.blend を開く。未作成なら現シーンを新規保存して遷移。
            if blend_io.coma_blend_exists(work_dir, page_id, stem):
                ok = blend_io.open_coma_blend(work_dir, page_id, stem)
                if not ok:
                    self.report({"ERROR"}, "cNN.blend を開けませんでした")
                    return {"CANCELLED"}
            else:
                from ..utils import coma_scene

                template_path, template_error = coma_scene.resolve_coma_blend_template_path(
                    work, work_dir
                )
                if template_error:
                    self.report({"ERROR"}, template_error)
                    return {"CANCELLED"}
                if template_path is None and not blend_io.read_homefile():
                    self.report({"ERROR"}, "cNN.blend の初期化に失敗")
                    return {"CANCELLED"}
                ok = coma_scene.bootstrap_new_coma_blend(
                    bpy.context,
                    work_dir,
                    page_id,
                    stem,
                    template_path=template_path,
                )
                if not ok:
                    self.report({"ERROR"}, "cNN.blend の新規作成に失敗")
                    try:
                        blend_io.open_work_blend(work_dir)
                    except Exception:  # noqa: BLE001
                        _logger.exception("enter_coma_mode: failed to restore work.blend")
                    return {"CANCELLED"}
                try:
                    from ..utils import coma_camera

                    coma_camera.ensure_coma_camera_scene(
                        bpy.context,
                        page_id=page_id,
                        coma_id=stem,
                        generate_references=True,
                    )
                except Exception:  # noqa: BLE001
                    _logger.exception("enter_coma_mode: initial panel camera setup failed")
                ok = blend_io.save_coma_blend(work_dir, page_id, stem)
                if not ok:
                    self.report({"ERROR"}, "cNN.blend の新規保存に失敗")
                    try:
                        blend_io.open_work_blend(work_dir)
                    except Exception:  # noqa: BLE001
                        _logger.exception("enter_coma_mode: failed to restore work.blend")
                    return {"CANCELLED"}
                # save_as_mainfile 直後は load_post が走らないので、mode/stem/page_id と
                # viewport 状態は明示的に current scene に反映する。
                try:
                    from ..ui import overlay as _overlay

                    set_mode(MODE_COMA, bpy.context)
                    bpy.context.scene.bname_current_coma_id = stem
                    bpy.context.scene.bname_current_coma_page_id = page_id
                    if hasattr(bpy.context.scene, "bname_active_layer_kind"):
                        bpy.context.scene.bname_active_layer_kind = "coma"
                    _overlay.reset_viewport_background_to_theme(bpy.context)
                    _overlay.apply_bname_shading_mode(bpy.context)
                except Exception:  # noqa: BLE001
                    _logger.exception("enter_coma_mode: initial panel scene finalize failed")
        except Exception as exc:  # noqa: BLE001
            _logger.exception("enter_coma_mode failed")
            self.report({"ERROR"}, f"コマ編集モード遷移失敗: {exc}")
            return {"CANCELLED"}

        # load_post ハンドラがモード/stem を同期するが、念のため明示的にも設定
        ctx = bpy.context
        set_mode(MODE_COMA, ctx)
        ctx.scene.bname_current_coma_id = stem
        ctx.scene.bname_current_coma_page_id = page_id
        if hasattr(ctx.scene, "bname_active_layer_kind"):
            ctx.scene.bname_active_layer_kind = "coma"
        try:
            from ..utils import coma_camera

            coma_camera.ensure_coma_camera_scene(
                ctx,
                page_id=page_id,
                coma_id=stem,
                generate_references=True,
            )
        except Exception:  # noqa: BLE001
            _logger.exception("enter_coma_mode: final panel camera setup failed")
        try:
            from ..ui import sidebar as _sidebar

            _sidebar.schedule_open_bname_sidebar()
        except Exception:  # noqa: BLE001
            _logger.exception("enter_coma_mode: B-Name sidebar open failed")
        self.report({"INFO"}, f"コマ編集モード: {stem}")
        return {"FINISHED"}


class BNAME_OT_exit_coma_mode(Operator):
    """コマ編集モードを抜けて overview モード (work.blend) へ戻る.

    cNN.blend を保存し、work.blend を開く。
    """

    bl_idname = "bname.exit_coma_mode"
    bl_label = "紙面編集モードへ戻る"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return get_mode(context) == MODE_COMA

    def execute(self, context):
        coma_modal_state.finish_all(context)
        # 1) サムネイル生成 (cNN.blend 切替前に現在の描画を記録)
        work = get_work(context)
        page = get_active_page(context)
        stem = getattr(context.scene, "bname_current_coma_id", "")
        if (
            work is not None
            and work.loaded
            and stem
            and paths.is_valid_coma_id(stem)
        ):
            try:
                from . import thumbnail_op

                page_id = getattr(context.scene, "bname_current_coma_page_id", "")
                if paths.is_valid_page_id(page_id):
                    out = paths.coma_thumb_path(Path(work.work_dir), page_id, stem)
                    thumbnail_op.take_area_screenshot(context, out)
            except Exception:  # noqa: BLE001
                _logger.exception("auto thumbnail failed on exit_coma_mode")

        # 2) 現在の cNN.blend を保存 → work.blend を開く
        if (
            work is not None
            and work.loaded
            and paths.is_valid_coma_id(stem)
        ):
            work_dir = Path(work.work_dir)
            try:
                page_id = getattr(context.scene, "bname_current_coma_page_id", "")
                if not paths.is_valid_page_id(page_id):
                    self.report({"ERROR"}, "編集中コマの page_id が失われています")
                    return {"CANCELLED"}
                cur = blend_io.current_mainfile_path()
                expected_panel = paths.coma_blend_path(work_dir, page_id, stem).resolve()
                if cur is not None and cur == expected_panel:
                    blend_io.save_current_as(expected_panel)
                # work.blend を開く. 通常 work_new で必ず作られているはずで、
                # 無い場合は user が外部削除した等の異常系。現在開いている
                # cNN.blend のシーンを work.blend として保存するとパネルの
                # 3D データが work.blend に紛れ込むため、その fallback は
                # 行わず、エラー報告だけして現状維持する。
                if blend_io.work_blend_exists(work_dir):
                    blend_io.open_work_blend(work_dir)
                else:
                    _logger.error(
                        "exit_coma_mode: work.blend not found at %s",
                        paths.work_blend_path(work_dir),
                    )
                    self.report(
                        {"ERROR"},
                        "work.blend が見つかりません. 作品フォルダの整合性を確認してください",
                    )
                    return {"CANCELLED"}
            except Exception as exc:  # noqa: BLE001
                _logger.exception("exit_coma_mode blend switch failed")
                self.report({"ERROR"}, f"work.blend 切替失敗: {exc}")
                return {"CANCELLED"}

        ctx = bpy.context
        set_mode(MODE_PAGE, ctx)
        ctx.scene.bname_current_coma_id = ""
        ctx.scene.bname_current_coma_page_id = ""
        self.report({"INFO"}, "紙面編集モード")
        return {"FINISHED"}


def _current_blend_is_coma_blend() -> tuple[Path | None, str, str]:
    """開いている mainfile が ``pNNNN/cNN/cNN.blend`` 形式かを判定.

    Returns ``(work_dir, page_id, coma_id)`` を返す。マッチしなければ
    ``(None, "", "")``。``bname_mode`` / ``bname_current_coma_id`` 等の
    Scene プロパティが load_post 失敗で同期されていないケースの救済用。
    """
    fp = bpy.data.filepath
    if not fp:
        return None, "", ""
    try:
        path = Path(fp).resolve()
    except OSError:
        return None, "", ""
    parts = path.parts
    if len(parts) < 4:
        return None, "", ""
    page_id, coma_id, fname = parts[-3], parts[-2], parts[-1]
    if not (
        paths.is_valid_page_id(page_id)
        and paths.is_valid_coma_id(coma_id)
        and fname == f"{coma_id}.blend"
    ):
        return None, "", ""
    work_dir = path.parents[2]
    return work_dir, page_id, coma_id


class BNAME_OT_exit_coma_mode_safe(Operator):
    """ページ一覧へ戻る (堅牢版).

    ``bname.exit_coma_mode`` は ``get_mode == MODE_COMA`` かつ ``work.loaded``
    を要求するが、load_post の遅延・失敗で Scene 状態が壊れていると
    poll/execute が通らずユーザーが帰り道を失う。本 operator は

    1. 通常パス: 既存の ``bname.exit_coma_mode`` を呼ぶ
    2. フォールバック: それが無理なら、開いている .blend のパスから
       ``work_dir`` を逆引きし、直接 ``open_work_blend`` で work.blend を
       開く (cNN.blend は保存しない / Blender は通常 mainfile dirty 状態を
       保存するか聞いてくる)

    どちらにせよ、ユーザーは ``ページ一覧 (work.blend)`` に戻れる。
    """

    bl_idname = "bname.exit_coma_mode_safe"
    bl_label = "ページ一覧に戻る"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        if get_mode(context) == MODE_COMA:
            return True
        work_dir, _page_id, _coma_id = _current_blend_is_coma_blend()
        return work_dir is not None

    def execute(self, context):
        # 1) 通常パス: ``exit_coma_mode`` の poll が通るならそれに委譲
        try:
            if BNAME_OT_exit_coma_mode.poll(context):
                return bpy.ops.bname.exit_coma_mode("EXEC_DEFAULT")
        except Exception:  # noqa: BLE001
            _logger.exception("exit_coma_mode_safe: 通常パス失敗")

        # 2) フォールバック: パスから work_dir を逆引きし、work.blend を開く
        work_dir, page_id, coma_id = _current_blend_is_coma_blend()
        if work_dir is None:
            self.report({"ERROR"}, "コマファイル (cNN.blend) ではありません")
            return {"CANCELLED"}
        try:
            # 念のため現在の cNN.blend に save (上書き保存)
            try:
                cur = blend_io.current_mainfile_path()
                expected_panel = paths.coma_blend_path(work_dir, page_id, coma_id).resolve()
                if cur is not None and cur == expected_panel:
                    blend_io.save_current_as(expected_panel)
            except Exception:  # noqa: BLE001
                _logger.exception("exit_coma_mode_safe: cNN.blend 保存失敗 (続行)")

            if not blend_io.work_blend_exists(work_dir):
                self.report(
                    {"ERROR"},
                    f"work.blend が見つかりません: {paths.work_blend_path(work_dir)}",
                )
                return {"CANCELLED"}
            blend_io.open_work_blend(work_dir)
            # load_post が走るので mode/state は自動で同期される。
            # 念のため現在 scene にも反映 (load_post 前に UI 更新が走る場合)。
            try:
                ctx = bpy.context
                set_mode(MODE_PAGE, ctx)
                ctx.scene.bname_current_coma_id = ""
                ctx.scene.bname_current_coma_page_id = ""
            except Exception:  # noqa: BLE001
                pass
            self.report({"INFO"}, "ページ一覧に戻りました")
            return {"FINISHED"}
        except Exception as exc:  # noqa: BLE001
            _logger.exception("exit_coma_mode_safe: work.blend 切替失敗")
            self.report({"ERROR"}, f"work.blend 切替失敗: {exc}")
            return {"CANCELLED"}


_CLASSES = (
    BNAME_OT_enter_coma_mode,
    BNAME_OT_exit_coma_mode,
    BNAME_OT_exit_coma_mode_safe,
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
