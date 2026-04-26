"""bpy.app.handlers ハンドラ.

``load_post``: .blend ファイル open 後に、B-Name 作品フォルダ配下の
.blend であれば work.json / pages.json を再読み込みして Scene プロパティを
同期する。また、開かれた .blend のパスから active_page_index と
bname_current_panel_stem を自動推定する。

これにより、ページ切替 (page.blend 差替) 時に JSON メタが正しく維持され、
古い .blend 内に残っていた Scene プロパティが上書きされる。
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from . import log, paths

_logger = log.get_logger(__name__)

_HANDLER_NAME = "_bname_on_load_post"


def _find_work_root(blend_path: Path) -> Path | None:
    """blend パスから上位に辿って .bname ディレクトリを探す (最大 6 階層)."""
    p = blend_path.parent
    for _ in range(6):
        if p.suffix == paths.BNAME_DIR_SUFFIX:
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


def _sync_active_from_blend_path(
    scene, work, work_dir: Path, blend_path: Path
) -> None:
    """開かれた blend のパスから mode / active_page_index / panel_stem を推定.

    - ``<work>.bname/work.blend`` → overview モード (MODE_PAGE)
    - ``<work>.bname/pages/NNNN/panels/panel_MMM.blend`` → コマ編集モード
      (MODE_PANEL + active_page_index を該当ページに、panel_stem を設定)
    - それ以外のパス (旧 page.blend 等) は何もしない
    """
    try:
        rel = blend_path.resolve().relative_to(work_dir.resolve())
    except ValueError:
        return
    try:
        from ..core.mode import MODE_PAGE, MODE_PANEL, set_mode
    except Exception:  # noqa: BLE001
        return
    parts = rel.parts

    # work.blend 直下 → overview モード
    if len(parts) == 1 and parts[0] == paths.WORK_BLEND_NAME:
        scene.bname_current_panel_stem = ""
        scene.bname_current_panel_page_id = ""
        set_mode(MODE_PAGE, bpy.context)
        return

    # pages/NNNN/panels/panel_MMM.blend → コマ編集モード
    if (
        len(parts) == 4
        and parts[0] == paths.PAGES_DIR_NAME
        and parts[2] == paths.PANELS_DIR_NAME
        and parts[3].endswith(".blend")
    ):
        page_id = parts[1]
        stem = parts[3][: -len(".blend")]
        if paths.is_valid_page_id(page_id) and paths.is_valid_panel_stem(stem):
            for i, pg in enumerate(work.pages):
                if pg.id == page_id:
                    work.active_page_index = i
                    break
            scene.bname_current_panel_stem = stem
            scene.bname_current_panel_page_id = page_id
            set_mode(MODE_PANEL, bpy.context)
            return

    # それ以外 (未知のパス) は overview 扱いのまま触らない


def _reload_all_pages_panels(work, work_dir: Path) -> None:
    """全ページの ``panels`` を各 ``page.json`` から再ロードして Scene に反映.

    pages.json は全ページのリストだけを持ち、panels は各ページの page.json
    にしか無いため、load_post で pages.json を読み込んだ後に各 page.json
    を個別に再ロードしないと、他ページの panels が現在の .blend に
    キャッシュされた古いものに固定されてしまう。

    load_page_json は内部で ``page_entry.panels.clear()`` → 再構築 するので
    上書き安全。
    """
    from ..io import page_io  # 遅延 import

    for page_entry in work.pages:
        if not page_entry.id:
            continue
        try:
            page_io.load_page_json(work_dir, page_entry)
        except Exception:  # noqa: BLE001
            # 個別 page.json の欠損や不整合はスキップ
            _logger.warning(
                "load_post: failed to load page.json for %s", page_entry.id,
                exc_info=True,
            )


def sync_scene_work_from_disk(context, work_dir: Path):
    """現在 scene の ``bname_work`` を disk 上の work/pages/page json に同期."""
    from ..core.work import get_work
    from ..io import page_io, work_io

    work = get_work(context)
    if work is None:
        return None
    work_io.load_work_json(work_dir, work)
    page_io.load_pages_json(work_dir, work)
    _reload_all_pages_panels(work, work_dir)
    work.work_dir = str(Path(work_dir).resolve())
    work.loaded = True
    return work


def _reconcile_gpencil_collections(context, work) -> None:
    """master GP / 紙メッシュ × pages の整合をとる (新仕様).

    - 作品全体で **唯一の** master GP オブジェクトを ensure (旧 page GP は残置)
    - 各ページの紙メッシュ (page_NNNN_paper) を ensure
    - 全ページ Collection の grid offset を apply (紙メッシュの位置補正)

    旧バージョンの page_NNNN_sketch GP オブジェクトはここでは触らない
    (ユーザーのデータを残置)。新規描画は master GP に行う。
    """
    from . import gpencil as gp_utils
    from . import page_grid

    scene = getattr(context, "scene", None) if context else None
    if scene is None:
        scene = bpy.context.scene
    if scene is None or work is None:
        return

    # 紙メッシュは各ページ単位で必要
    for page_entry in work.pages:
        if not page_entry.id:
            continue
        try:
            gp_utils.ensure_page_paper(
                scene, page_entry.id,
                float(work.paper.canvas_width_mm),
                float(work.paper.canvas_height_mm),
                work.paper.paper_color,
            )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "load_post: ensure_page_paper failed for %s", page_entry.id
            )

    # master GP は作品で 1 つだけ
    try:
        gp_utils.ensure_master_gpencil(scene)
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: ensure_master_gpencil failed")

    try:
        page_grid.apply_page_collection_transforms(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: apply_page_collection_transforms failed")


@persistent
def _bname_on_load_post(filepath_arg) -> None:  # signature: (str,) in Blender handlers
    """.blend ロード直後に B-Name 作品のメタ情報を再同期."""
    try:
        # 遅延 import: サブシステムの初期化順を回避
        scene = bpy.context.scene
        if scene is None:
            return
        blend_path = Path(bpy.data.filepath)
        if str(blend_path) == "" or not blend_path.is_file():
            return
        work_dir = _find_work_root(blend_path)
        if work_dir is None:
            return
        work = sync_scene_work_from_disk(bpy.context, work_dir)
        if work is None:
            return
        try:
            work.work_dir = str(work_dir.resolve())
            work.loaded = True
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: failed to sync work/pages json")
            return
        _sync_active_from_blend_path(scene, work, work_dir, blend_path)
        from . import display_settings

        display_settings.apply_standard_color_management(scene)
        try:
            from ..operators import preset_op

            preset_op.sync_paper_preset_selector(bpy.context)
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: preset selector sync failed")
        # work.blend / panel.blend ごとに Scene の整合を補正する。
        try:
            rel = blend_path.resolve().relative_to(work_dir.resolve())
            if len(rel.parts) == 1 and rel.parts[0] == paths.WORK_BLEND_NAME:
                _reconcile_gpencil_collections(bpy.context, work)
                try:
                    from ..ui import overlay as _overlay

                    _overlay.reset_viewport_background_to_theme(bpy.context)
                    _overlay.apply_bname_shading_mode(bpy.context)
                except Exception:  # noqa: BLE001
                    _logger.exception(
                        "load_post: shading/background reset failed"
                    )
            elif (
                len(rel.parts) == 4
                and rel.parts[0] == paths.PAGES_DIR_NAME
                and rel.parts[2] == paths.PANELS_DIR_NAME
                and rel.parts[3].endswith(".blend")
            ):
                from . import panel_scene
                from . import panel_camera
                from ..ui import overlay as _overlay

                panel_scene.prepare_panel_blend_scene(bpy.context)
                display_settings.apply_standard_color_management(scene)
                panel_camera.ensure_panel_camera_scene(
                    bpy.context,
                    work=work,
                    generate_references=True,
                )
                _overlay.reset_viewport_background_to_theme(bpy.context)
                _overlay.apply_bname_shading_mode(bpy.context)
                panel_camera.schedule_panel_view_camera()
        except ValueError:
            pass
        _logger.info("B-Name: load_post synced for %s", blend_path)
    except Exception:  # noqa: BLE001
        _logger.exception("B-Name load_post handler failed")


def register() -> None:
    """ハンドラを重複なく登録."""
    # 既存の同名ハンドラを除去 (reload 対策)
    for h in list(bpy.app.handlers.load_post):
        if getattr(h, "__name__", "") == _bname_on_load_post.__name__:
            try:
                bpy.app.handlers.load_post.remove(h)
            except ValueError:
                pass
    bpy.app.handlers.load_post.append(_bname_on_load_post)
    _logger.debug("handlers registered")


def unregister() -> None:
    for h in list(bpy.app.handlers.load_post):
        if getattr(h, "__name__", "") == _bname_on_load_post.__name__:
            try:
                bpy.app.handlers.load_post.remove(h)
            except ValueError:
                pass
    _logger.debug("handlers unregistered")
