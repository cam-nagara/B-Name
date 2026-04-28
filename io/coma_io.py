"""コマファイル (cNN/cNN.blend / cNN/cNN.json / _thumb.png / _preview.png) の I/O.

計画書 4.4 / 3.3.3 参照。ファイル名採番・重複時リネーム・他ページへの
移動・複製を担当。cNN.blend の実ロード/セーブは operators/ 層で
bpy.ops.wm.* を呼ぶ。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..utils import json_io, log, paths
from . import schema

_logger = log.get_logger(__name__)


# ---------- 採番 ----------


def existing_coma_ids(work_dir: Path, page_id: str) -> list[str]:
    """ページ内の既存 cNN ID を列挙."""
    paths.validate_page_id(page_id)
    page_path = paths.page_dir(Path(work_dir), page_id)
    if not page_path.is_dir():
        return []
    ids = {p.name for p in page_path.iterdir() if p.is_dir() and paths.is_valid_coma_id(p.name)}
    return sorted(ids)


def allocate_new_coma_id(work_dir: Path, page_id: str) -> str:
    existing = existing_coma_ids(work_dir, page_id)
    idx = paths.next_available_coma_index(existing)
    return paths.format_coma_id(idx)


# ---------- cNN.json ----------


def save_coma_meta(work_dir: Path, page_id: str, entry) -> Path:
    paths.validate_page_id(page_id)
    paths.validate_coma_id(entry.coma_id)
    out = paths.coma_json_path(Path(work_dir), page_id, entry.coma_id)
    data = schema.coma_entry_to_dict(entry)
    json_io.write_json(out, data)
    return out


def load_coma_meta(work_dir: Path, page_id: str, coma_id: str, entry) -> dict:
    paths.validate_page_id(page_id)
    paths.validate_coma_id(coma_id)
    path = paths.coma_json_path(Path(work_dir), page_id, coma_id)
    if not path.is_file():
        return {}
    data = json_io.read_json(path)
    schema.coma_entry_from_dict(entry, data)
    return data


# ---------- ファイル移動/複製 ----------


def _coma_artifact_files(work_dir: Path, page_id: str, coma_id: str) -> list[Path]:
    """cNN に関連する主要ファイル (.blend/.json/_thumb.png/_preview.png) を列挙."""
    pd = paths.coma_dir(Path(work_dir), page_id, coma_id)
    candidates = [
        pd / f"{coma_id}.blend",
        pd / f"{coma_id}.json",
        pd / f"{coma_id}_thumb.png",
        pd / f"{coma_id}_preview.png",
    ]
    return [p for p in candidates if p.exists()]


def _rename_coma_artifacts(coma_path: Path, old_id: str, new_id: str) -> list[Path]:
    renamed: list[Path] = []
    for suffix in (".blend", ".json", "_thumb.png", "_preview.png"):
        src = coma_path / f"{old_id}{suffix}"
        if not src.exists():
            continue
        dst = coma_path / f"{new_id}{suffix}"
        if src == dst:
            renamed.append(dst)
            continue
        if dst.exists():
            raise FileExistsError(f"destination already exists: {dst}")
        src.rename(dst)
        renamed.append(dst)
    return renamed


def move_coma_files(
    work_dir: Path,
    src_page_id: str,
    dst_page_id: str,
    src_coma_id: str,
    dst_coma_id: str,
) -> list[Path]:
    """コマディレクトリ一式を別ページへ移動."""
    paths.validate_page_id(src_page_id)
    paths.validate_page_id(dst_page_id)
    paths.validate_coma_id(src_coma_id)
    paths.validate_coma_id(dst_coma_id)
    src_dir = paths.coma_dir(Path(work_dir), src_page_id, src_coma_id)
    dst_dir = paths.coma_dir(Path(work_dir), dst_page_id, dst_coma_id)
    if not src_dir.exists():
        return []
    if dst_dir.exists():
        raise FileExistsError(f"destination already exists: {dst_dir}")
    dst_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_dir), str(dst_dir))
    moved = _rename_coma_artifacts(dst_dir, src_coma_id, dst_coma_id)
    moved.append(dst_dir)
    _logger.info(
        "coma moved: %s/%s -> %s/%s (%d paths)",
        src_page_id, src_coma_id, dst_page_id, dst_coma_id, len(moved),
    )
    return moved


def copy_coma_files(
    work_dir: Path,
    src_page_id: str,
    dst_page_id: str,
    src_coma_id: str,
    dst_coma_id: str,
) -> list[Path]:
    """コマディレクトリ一式を別ページへコピー."""
    paths.validate_page_id(src_page_id)
    paths.validate_page_id(dst_page_id)
    paths.validate_coma_id(src_coma_id)
    paths.validate_coma_id(dst_coma_id)
    src_dir = paths.coma_dir(Path(work_dir), src_page_id, src_coma_id)
    dst_dir = paths.coma_dir(Path(work_dir), dst_page_id, dst_coma_id)
    if not src_dir.exists():
        return []
    if dst_dir.exists():
        raise FileExistsError(f"destination already exists: {dst_dir}")
    dst_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_dir, dst_dir)
    copied = _rename_coma_artifacts(dst_dir, src_coma_id, dst_coma_id)
    copied.append(dst_dir)
    _logger.info(
        "coma copied: %s/%s -> %s/%s (%d paths)",
        src_page_id, src_coma_id, dst_page_id, dst_coma_id, len(copied),
    )
    return copied


def remove_coma_files(work_dir: Path, page_id: str, coma_id: str) -> int:
    paths.validate_page_id(page_id)
    paths.validate_coma_id(coma_id)
    coma_path = paths.coma_dir(Path(work_dir), page_id, coma_id)
    if not coma_path.exists():
        return 0
    shutil.rmtree(coma_path)
    _logger.info("coma removed: %s/%s", page_id, coma_id)
    return 1
