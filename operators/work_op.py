"""作品 (.bname) の新規作成・オープン・保存・クローズ Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper, ExportHelper

from ..core.mode import MODE_PAGE, MODE_PANEL, get_mode, set_mode
from ..core.work import find_page_by_id, get_active_page, get_work
from ..io import blend_io, page_io, presets, work_io
from ..utils import color_space
from ..utils import gpencil as gp_utils
from ..utils import log, page_grid, paths

_logger = log.get_logger(__name__)


def _apply_phase1_defaults(work) -> None:
    """新規作品のワンショット既定値セット.

    原稿上の表示の初期値:
      - 作品名 ON / 左上 (top-left)
      - 話数 OFF / 上中央 (top-center)
      - サブタイトル OFF / 右上 (top-right)
      - 作者名 ON / 右下 (bottom-right) — 値は OS のユーザー名で初期化
      - ページ番号 ON / 下中央 (bottom-center)
    """
    info = work.work_info
    info.display_work_name.enabled = True
    info.display_work_name.position = "top-left"
    info.display_episode.enabled = False
    info.display_episode.position = "top-center"
    info.display_subtitle.enabled = False
    info.display_subtitle.position = "top-right"
    info.display_author.enabled = True
    info.display_author.position = "bottom-right"
    info.display_page_number.enabled = True
    info.display_page_number.position = "bottom-center"
    info.page_number_start = 1
    # 作者名が未入力なら OS のユーザー名で初期化 (上書きはしない)
    if not info.author:
        try:
            import getpass
            info.author = getpass.getuser()
        except Exception:  # noqa: BLE001
            pass
    # 既定プリセット適用 (見つからなくても既定値は PropertyGroup に入っている)
    presets.load_default_preset(work.paper)
    # セーフライン外塗りは新規作品ごとに既定値へ戻す。
    # PropertyGroup は同一 scene 内で前回値を保持するため、ここで明示的に初期化しないと
    # 「前の作品で変えた色」が新規作品へ漏れる。プリセット適用後に置き直して、
    # 今後プリセット側が拡張されても新規作品の既定を固定する。
    work.safe_area_overlay.enabled = True
    work.safe_area_overlay.color = color_space.srgb_to_linear_rgb((0.7, 0.7, 0.7))


def _cleanup_default_scene_objects() -> None:
    """Blender のデフォルトシーンに含まれる Cube / Light / Camera を削除.

    B-Name の新規作品ではネームキャンバスを真正面から見るため、3D の既定
    ライトやカメラは不要。ユーザーが作ったオブジェクトと名前衝突しないよう、
    Blender 既定の "Cube" / "Light" / "Camera" という正確な名前のみを対象とする。
    """
    default_names = ("Cube", "Light", "Camera")
    for name in default_names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.warning("failed to remove default object: %s", name)
    # 孤児化したデータブロック (Mesh/Light/Camera 本体) も掃除
    for mesh in tuple(bpy.data.meshes):
        if mesh.name == "Cube" and mesh.users == 0:
            try:
                bpy.data.meshes.remove(mesh)
            except Exception:  # noqa: BLE001
                pass
    for light_data in tuple(bpy.data.lights):
        if light_data.name == "Light" and light_data.users == 0:
            try:
                bpy.data.lights.remove(light_data)
            except Exception:  # noqa: BLE001
                pass
    for cam_data in tuple(bpy.data.cameras):
        if cam_data.name == "Camera" and cam_data.users == 0:
            try:
                bpy.data.cameras.remove(cam_data)
            except Exception:  # noqa: BLE001
                pass


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

            # 最初のページ 0001 を自動生成し、現在のシーンを work.blend として保存.
            # work.blend は作品全体のマスター .blend で、全ページの 2D データ
            # (コマ枠・GP・テキスト・フキダシ等) が 1 つのシーンに載る。
            entry = page_io.register_new_page(work)
            page_io.ensure_page_dir(work_dir, entry.id)
            from .panel_op import create_basic_frame_panel

            create_basic_frame_panel(work, entry, work_dir)
            page_io.save_pages_json(work_dir, work)

            # デフォルトシーンの Cube/Light/Camera を削除してから保存
            # (ネームキャンバスに余計な 3D オブジェクトが載らないようにする)
            _cleanup_default_scene_objects()

            # 初期ページの GP オブジェクト + ページ Collection を生成し、
            # grid transform を適用 (1 ページ目なので offset は (0,0) だが
            # 以降のページ追加で効いてくる呼び出しを揃える)
            gp_initial_obj = gp_utils.ensure_page_gpencil(context.scene, entry.id)
            page_grid.apply_page_collection_transforms(context, work)

            # overview 編集モード既定。保存前にモード/stem を確実にセット。
            set_mode(MODE_PAGE, context)
            context.scene.bname_current_panel_stem = ""
            context.scene.bname_current_panel_page_id = ""
            try:
                from ..utils import display_settings

                display_settings.apply_standard_color_management(context.scene)
            except Exception:  # noqa: BLE001
                _logger.exception("work_new: color management setup failed")
            try:
                from . import preset_op

                preset_op.sync_paper_preset_selector(context)
            except Exception:  # noqa: BLE001
                _logger.exception("work_new: preset selector sync failed")

            blend_io.save_work_blend(work_dir)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("work_new failed")
            work.loaded = False
            self.report({"ERROR"}, f"作成失敗: {exc}")
            return {"CANCELLED"}

        # --- 作成直後の UX 整備 ---
        # 0) 旧バージョンで白く書き換えられた可能性のあるビューポート背景を
        #    テーマ既定 (灰色) に戻す + Solid+Flat 照明に切替 (B-Name の標準表示)
        try:
            from ..ui import overlay as _overlay

            _overlay.reset_viewport_background_to_theme(context)
            _overlay.apply_bname_shading_mode(context)
        except Exception:  # noqa: BLE001
            _logger.exception("work_new: shading/background setup failed")

        # 1) ビューポートを全ページフィット (overview モードを維持したままキャンバス可視化)
        try:
            bpy.ops.bname.view_fit_all("INVOKE_DEFAULT")
        except Exception:  # noqa: BLE001
            _logger.exception("work_new: view_fit_all failed")

        # 2) 初期ページ GP を view_layer の active に設定し、ユーザーがモード切替
        #    (Draw / Edit) すればすぐ描画に入れる状態にする。モード遷移自体は
        #    ユーザーの意図を尊重して自動化しない (Phase 2 設計方針)。
        try:
            view_layer = context.view_layer
            if view_layer is not None and gp_initial_obj is not None:
                for o in list(context.selected_objects):
                    if o is not gp_initial_obj:
                        try:
                            o.select_set(False)
                        except Exception:  # noqa: BLE001
                            pass
                view_layer.objects.active = gp_initial_obj
                try:
                    gp_initial_obj.select_set(True)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            _logger.exception("work_new: set active GP failed")

        self.report({"INFO"}, f"作品を作成: {work_dir.name} (page 0001 を初期化)")
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
            work.work_dir = str(work_dir.resolve())
            work.loaded = True
            set_mode(MODE_PAGE, context)
            context.scene.bname_current_panel_stem = ""
            context.scene.bname_current_panel_page_id = ""
            try:
                from ..utils import display_settings

                display_settings.apply_standard_color_management(context.scene)
            except Exception:  # noqa: BLE001
                _logger.exception("work_open: color management setup failed")
            try:
                from . import preset_op

                preset_op.sync_paper_preset_selector(context)
            except Exception:  # noqa: BLE001
                _logger.exception("work_open: preset selector sync failed")
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

        # work.blend を自動オープン (なければ JSON のみ読み込んだ状態)
        if blend_io.work_blend_exists(work_dir):
            blend_io.open_work_blend(work_dir)
            # load_post ハンドラが JSON 再同期と mode/stem の再設定を担う

        # 背景をテーマ既定に戻す + Solid+Flat 照明に切替
        try:
            from ..ui import overlay as _overlay

            _overlay.reset_viewport_background_to_theme(context)
            _overlay.apply_bname_shading_mode(context)
        except Exception:  # noqa: BLE001
            _logger.exception("work_open: shading/background setup failed")

        self.report({"INFO"}, f"作品を開きました: {work_dir.name}")
        return {"FINISHED"}


class BNAME_OT_work_save(Operator):
    """現在の作品データを保存 (work.json / pages.json + 現在の mainfile .blend)."""

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
            # 1) JSON メタ保存
            work_io.save_work_json(work_dir, work)
            page_io.save_pages_json(work_dir, work)
            mode = get_mode(context)
            if mode == MODE_PANEL:
                page_id = getattr(context.scene, "bname_current_panel_page_id", "")
                page = find_page_by_id(work, page_id)
            else:
                page = get_active_page(context)
            if page is not None:
                page_io.save_page_json(work_dir, page)

            # 2) .blend 保存. ユーザーが File > Save As で work_dir 外に保存
            #    していた場合は、そのパスを尊重して save_mainfile する (B-Name の
            #    期待パスへ強制リロケートしない)。work_dir 内 or 未保存なら
            #    overview モードなら work.blend、panel モードなら panel_NNN.blend
            #    を期待パスとして save_as_mainfile する。
            cur = blend_io.current_mainfile_path()
            work_dir_resolved = work_dir.resolve()
            in_work_dir = False
            if cur is not None:
                try:
                    cur.relative_to(work_dir_resolved)
                    in_work_dir = True
                except ValueError:
                    in_work_dir = False

            saved_blend = False
            saved_path = ""
            if cur is not None and not in_work_dir:
                # work_dir 外 → ユーザーの Save As パスをそのまま尊重
                try:
                    bpy.ops.wm.save_mainfile(compress=True)
                    saved_blend = True
                    saved_path = str(cur)
                except Exception as exc:  # noqa: BLE001
                    _logger.exception("save_mainfile (external path) failed")
                    saved_blend = False
            else:
                # work_dir 内 or 未保存 → B-Name 期待パスへ save_as
                if mode == MODE_PANEL:
                    stem = getattr(context.scene, "bname_current_panel_stem", "")
                    page_id = getattr(context.scene, "bname_current_panel_page_id", "")
                    if paths.is_valid_panel_stem(stem) and paths.is_valid_page_id(page_id):
                        index = int(stem.split("_", 1)[1])
                        saved_blend = blend_io.save_panel_blend(
                            work_dir, page_id, index
                        )
                        if saved_blend:
                            saved_path = str(
                                paths.panel_blend_path(work_dir, page_id, index)
                            )
                else:
                    saved_blend = blend_io.save_work_blend(work_dir)
                    if saved_blend:
                        saved_path = str(paths.work_blend_path(work_dir))
        except Exception as exc:  # noqa: BLE001
            _logger.exception("work_save failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        if saved_blend:
            self.report({"INFO"}, f"作品を保存: {Path(saved_path).name}")
        else:
            self.report({"WARNING"}, "JSON は保存、.blend 保存はスキップ")
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
        set_mode(MODE_PAGE, context)
        context.scene.bname_current_panel_stem = ""
        context.scene.bname_current_panel_page_id = ""
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
