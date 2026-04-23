"""コマファイル (panel_NNN.blend / panel_NNN.json / _thumb.png / _preview.png) の I/O.

計画書 4.4 / 3.3.3 参照。ファイル名採番・重複時リネーム・他ページへの
移動・複製を担当。panel_NNN.blend の実ロード/セーブは operators/ 層で
bpy.ops.wm.* を呼ぶ。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..utils import json_io, log, paths
from . import schema

_logger = log.get_logger(__name__)


# ---------- 採番 ----------


def existing_panel_stems(work_dir: Path, page_id: str) -> list[str]:
    """ページ内の既存 panel_NNN stem を列挙."""
    paths.validate_page_id(page_id)
    pd = paths.panels_dir(Path(work_dir), page_id)
    if not pd.is_dir():
        return []
    stems: set[str] = set()
    for p in pd.iterdir():
        if p.suffix in (".blend", ".json", ".png") and p.stem.startswith("panel_"):
            # "panel_001_thumb" → "panel_001" に正規化
            base = p.stem.split("_thumb")[0].split("_preview")[0]
            if paths.is_valid_panel_stem(base):
                stems.add(base)
    return sorted(stems)


def allocate_new_panel_stem(work_dir: Path, page_id: str) -> str:
    existing = existing_panel_stems(work_dir, page_id)
    idx = paths.next_available_panel_index(existing)
    return paths.format_panel_stem(idx)


# ---------- panel_NNN.json ----------


def save_panel_meta(work_dir: Path, page_id: str, entry) -> Path:
    paths.validate_page_id(page_id)
    paths.validate_panel_stem(entry.panel_stem)
    index = int(entry.panel_stem.split("_", 1)[1])
    out = paths.panel_meta_path(Path(work_dir), page_id, index)
    data = schema.panel_entry_to_dict(entry)
    json_io.write_json(out, data)
    return out


def load_panel_meta(work_dir: Path, page_id: str, panel_stem: str, entry) -> dict:
    paths.validate_page_id(page_id)
    paths.validate_panel_stem(panel_stem)
    index = int(panel_stem.split("_", 1)[1])
    path = paths.panel_meta_path(Path(work_dir), page_id, index)
    if not path.is_file():
        return {}
    data = json_io.read_json(path)
    schema.panel_entry_from_dict(entry, data)
    return data


# ---------- ファイル移動/複製 ----------


def _panel_files(work_dir: Path, page_id: str, stem: str) -> list[Path]:
    """panel_NNN に関連する全ファイル (.blend/.json/_thumb.png/_preview.png) を列挙."""
    pd = paths.panels_dir(Path(work_dir), page_id)
    candidates = [
        pd / f"{stem}.blend",
        pd / f"{stem}.json",
        pd / f"{stem}_thumb.png",
        pd / f"{stem}_preview.png",
    ]
    return [p for p in candidates if p.exists()]


def move_panel_files(
    work_dir: Path,
    src_page_id: str,
    dst_page_id: str,
    src_stem: str,
    dst_stem: str,
) -> list[Path]:
    """panel ファイル一式を別ページへ移動 (ファイル名重複時は呼出側で dst_stem 解決)."""
    paths.validate_page_id(src_page_id)
    paths.validate_page_id(dst_page_id)
    paths.validate_panel_stem(src_stem)
    paths.validate_panel_stem(dst_stem)
    dst_dir = paths.panels_dir(Path(work_dir), dst_page_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for src in _panel_files(work_dir, src_page_id, src_stem):
        # 拡張子と suffix 構造を保持したまま dst_stem にリネーム
        suffix = src.name[len(src_stem):]  # 例: "_thumb.png" or ".blend"
        dst = dst_dir / f"{dst_stem}{suffix}"
        if dst.exists():
            raise FileExistsError(f"destination already exists: {dst}")
        shutil.move(str(src), str(dst))
        moved.append(dst)
    _logger.info(
        "panel moved: %s/%s -> %s/%s (%d files)",
        src_page_id, src_stem, dst_page_id, dst_stem, len(moved),
    )
    return moved


def copy_panel_files(
    work_dir: Path,
    src_page_id: str,
    dst_page_id: str,
    src_stem: str,
    dst_stem: str,
) -> list[Path]:
    """panel ファイル一式を別ページへコピー (複製)."""
    paths.validate_page_id(src_page_id)
    paths.validate_page_id(dst_page_id)
    paths.validate_panel_stem(src_stem)
    paths.validate_panel_stem(dst_stem)
    dst_dir = paths.panels_dir(Path(work_dir), dst_page_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for src in _panel_files(work_dir, src_page_id, src_stem):
        suffix = src.name[len(src_stem):]
        dst = dst_dir / f"{dst_stem}{suffix}"
        if dst.exists():
            raise FileExistsError(f"destination already exists: {dst}")
        shutil.copy2(str(src), str(dst))
        copied.append(dst)
    _logger.info(
        "panel copied: %s/%s -> %s/%s (%d files)",
        src_page_id, src_stem, dst_page_id, dst_stem, len(copied),
    )
    return copied


def remove_panel_files(work_dir: Path, page_id: str, stem: str) -> int:
    paths.validate_page_id(page_id)
    paths.validate_panel_stem(stem)
    count = 0
    for p in _panel_files(work_dir, page_id, stem):
        try:
            p.unlink()
            count += 1
        except OSError as exc:
            _logger.warning("failed to unlink %s: %s", p, exc)
    _logger.info("panel removed: %s/%s (%d files)", page_id, stem, count)
    return count
