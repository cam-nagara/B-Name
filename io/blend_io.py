"""Blender mainfile (.blend) の save/open ラッパ.

Phase 1 (overview 再設計): モデル変更あり。
- **work.blend** — 全ページの 2D データを載せるマスター .blend。起動時はこれが
  mainfile。overview 編集ホーム。
- **cNN.blend** — 各コマの 3D シーン。コマ編集モード時のみ mainfile。

モード遷移は「現在の mainfile を save_as_mainfile で当該 .blend として保存」
→「切替先の .blend を open_mainfile で開く」の 2 段で行う。以前の ``page.blend``
(1 ページ 1 mainfile) は廃止された (計画書 3. Phase 1 参照)。
"""

from __future__ import annotations

from pathlib import Path

import bpy

from ..utils import log, paths

_logger = log.get_logger(__name__)


def save_current_as(blend_path: Path) -> bool:
    """現在の mainfile を指定パスに save_as_mainfile で保存する.

    親ディレクトリは自動生成。成功時 True、失敗時 False を返す。
    """
    blend_path = Path(blend_path)
    blend_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        bpy.ops.wm.save_as_mainfile(
            filepath=str(blend_path.resolve()),
            check_existing=False,
            compress=True,
        )
        _logger.info("mainfile saved: %s", blend_path)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.exception("save_as_mainfile failed: %s (%s)", blend_path, exc)
        return False


def open_mainfile(blend_path: Path) -> bool:
    """指定 .blend を open_mainfile で開く. 存在しなければ False."""
    blend_path = Path(blend_path)
    if not blend_path.is_file():
        _logger.warning("blend file missing: %s", blend_path)
        return False
    try:
        bpy.ops.wm.open_mainfile(filepath=str(blend_path.resolve()))
        _logger.info("mainfile opened: %s", blend_path)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.exception("open_mainfile failed: %s (%s)", blend_path, exc)
        return False


def read_homefile() -> bool:
    """空の mainfile 状態に戻す (factory startup でなく user startup)."""
    try:
        bpy.ops.wm.read_homefile()
        _logger.info("mainfile reset to homefile")
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.exception("read_homefile failed: %s", exc)
        return False


# ---------- work.blend (マスター) ----------


def save_work_blend(work_dir: Path) -> bool:
    """現在の mainfile を ``<work>.bname/work.blend`` に保存."""
    return save_current_as(paths.work_blend_path(Path(work_dir)))


def open_work_blend(work_dir: Path) -> bool:
    return open_mainfile(paths.work_blend_path(Path(work_dir)))


def work_blend_exists(work_dir: Path) -> bool:
    return paths.work_blend_path(Path(work_dir)).is_file()


# ---------- cNN.blend (コマ 3D) ----------


def save_coma_blend(work_dir: Path, page_id: str, coma_id: str) -> bool:
    if not paths.is_valid_page_id(page_id) or not paths.is_valid_coma_id(coma_id):
        return False
    return save_current_as(paths.coma_blend_path(Path(work_dir), page_id, coma_id))


def open_coma_blend(work_dir: Path, page_id: str, coma_id: str) -> bool:
    if not paths.is_valid_page_id(page_id) or not paths.is_valid_coma_id(coma_id):
        return False
    return open_mainfile(paths.coma_blend_path(Path(work_dir), page_id, coma_id))


def coma_blend_exists(work_dir: Path, page_id: str, coma_id: str) -> bool:
    if not paths.is_valid_page_id(page_id) or not paths.is_valid_coma_id(coma_id):
        return False
    return paths.coma_blend_path(Path(work_dir), page_id, coma_id).is_file()


def current_mainfile_path() -> Path | None:
    """現在開いている mainfile の絶対パス. 未保存なら None."""
    p = bpy.data.filepath
    if not p:
        return None
    return Path(p).resolve()
