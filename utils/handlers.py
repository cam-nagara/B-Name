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


def _reconcile_gpencil_collections(context, work) -> None:
    """GP オブジェクト / ページ Collection × pages の整合をとる.

    - 各ページについて常に ``ensure_page_gpencil`` を呼ぶ. ensure は
      idempotent で、不足している Collection / Object / Layer / Frame のみ
      を補完する。既存データは破壊しない。
    - これにより:
        - 旧バージョンの .blend で生成されたレイヤー無しフレーム無し GP にも
          load 時にデフォルトレイヤー + 現在フレームの空フレームが補充される
        - GP/Collection が完全に欠落しているページには新規生成される
    - GP/Collection はあるが pages.json に無い page_ID のものは放置
      (ユーザーのワーク中データを勝手に消さない)
    - 全ページ Collection の grid offset を apply して配置を補正

    Phase 2 以降、work.blend を開いた直後の整合確認用。
    """
    # gpencil / page_grid は遅延 import (utils 内循環を避ける)
    from . import gpencil as gp_utils
    from . import page_grid

    scene = getattr(context, "scene", None) if context else None
    if scene is None:
        scene = bpy.context.scene
    if scene is None or work is None:
        return
    for page_entry in work.pages:
        if not page_entry.id:
            continue
        try:
            gp_utils.ensure_page_gpencil(scene, page_entry.id)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "load_post: ensure_page_gpencil failed for %s", page_entry.id
            )
    try:
        page_grid.apply_page_collection_transforms(context, work)
    except Exception:  # noqa: BLE001
        _logger.exception("load_post: apply_page_collection_transforms failed")


@persistent
def _bname_on_load_post(filepath_arg) -> None:  # signature: (str,) in Blender handlers
    """.blend ロード直後に B-Name 作品のメタ情報を再同期."""
    try:
        # 遅延 import: サブシステムの初期化順を回避
        from ..core.work import get_work
        from ..io import page_io, work_io

        scene = bpy.context.scene
        if scene is None:
            return
        blend_path = Path(bpy.data.filepath)
        if str(blend_path) == "" or not blend_path.is_file():
            return
        work_dir = _find_work_root(blend_path)
        if work_dir is None:
            return
        work = get_work(bpy.context)
        if work is None:
            return
        try:
            work_io.load_work_json(work_dir, work)
            page_io.load_pages_json(work_dir, work)
            # 全ページの panels を各 page.json から再ロード
            # (他ページの panels が古い Scene キャッシュで上書きされる事故を防ぐ)
            _reload_all_pages_panels(work, work_dir)
        except Exception:  # noqa: BLE001
            _logger.exception("load_post: failed to sync work/pages json")
            return
        work.work_dir = str(work_dir.resolve())
        work.loaded = True
        _sync_active_from_blend_path(scene, work, work_dir, blend_path)
        # work.blend を開いた場合のみ GP×ページ整合を確認・grid 再配置 +
        # ビューポート背景色を用紙色に設定
        try:
            rel = blend_path.resolve().relative_to(work_dir.resolve())
            if len(rel.parts) == 1 and rel.parts[0] == paths.WORK_BLEND_NAME:
                _reconcile_gpencil_collections(bpy.context, work)
                try:
                    from ..ui import overlay as _overlay

                    _overlay.apply_paper_background_color(bpy.context)
                except Exception:  # noqa: BLE001
                    _logger.exception(
                        "load_post: apply_paper_background_color failed"
                    )
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
