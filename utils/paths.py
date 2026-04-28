"""パス構築ユーティリティ.

作品フォルダ (.bname) 直下・ページディレクトリ・コマファイルの相対パスを
一元的に構築する。.bname フォルダの命名規則 (4.1-4.4) を 1 箇所に集約。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

WORK_META_NAME = "work.json"
WORK_BLEND_NAME = "work.blend"
PAGES_META_NAME = "pages.json"
PAGE_META_NAME = "page.json"
ASSETS_DIR_NAME = "assets"
ASSETS_TEMPLATES_DIR = "templates"
ASSETS_BRUSHES_DIR = "brushes"
ASSETS_MODELS_DIR = "models"
ASSETS_BALLOONS_DIR = "balloons"
ASSETS_EFFECTS_DIR = "effects"
SCENARIO_DIR_NAME = "scenario"
SCENARIO_FILE_NAME = "imported.json"
EXPORTS_DIR_NAME = "exports"
RASTER_DIR_NAME = "raster"
RASTER_TRASH_DIR_NAME = ".trash"

BNAME_DIR_SUFFIX = ".bname"


# 単ページ ("p0001") と見開き ("p0020-0021") のみ許可
_PAGE_ID_RE = re.compile(r"^p\d{4}(-\d{4})?$")
_COMA_ID_RE = re.compile(r"^c\d{2}$")


def is_valid_page_id(page_id: str) -> bool:
    return isinstance(page_id, str) and bool(_PAGE_ID_RE.match(page_id))


def is_valid_coma_id(coma_id: str) -> bool:
    if not isinstance(coma_id, str) or not _COMA_ID_RE.match(coma_id):
        return False
    try:
        return 1 <= int(coma_id[1:]) <= 99
    except ValueError:
        return False


def validate_page_id(page_id: str) -> str:
    """不正な page_id ならエラー。呼び出し側はパス結合前に必ず通すこと."""
    if not is_valid_page_id(page_id):
        raise ValueError(f"invalid page_id: {page_id!r}")
    return page_id


def validate_coma_id(coma_id: str) -> str:
    if not is_valid_coma_id(coma_id):
        raise ValueError(f"invalid coma_id: {coma_id!r}")
    return coma_id


def format_page_id(index: int) -> str:
    """ページ番号を 4 桁ゼロパディング ID に変換 (例: 1 → "p0001")."""
    if index < 1 or index > 9999:
        raise ValueError(f"page index must be 1..9999: {index}")
    return f"p{index:04d}"


def format_spread_id(left: int, right: int) -> str:
    """見開きページの ID を生成 (例: 20, 21 → "p0020-0021")."""
    left_id = format_page_id(left)
    right_num = format_page_id(right)[1:]
    return f"{left_id}-{right_num}"


def format_coma_id(index: int) -> str:
    """コマ ID を 2 桁ゼロパディングで生成 (例: 1 → "c01")."""
    if index < 1 or index > 99:
        raise ValueError(f"coma index must be 1..99: {index}")
    return f"c{index:02d}"


def work_meta_path(work_dir: Path) -> Path:
    return Path(work_dir) / WORK_META_NAME


def pages_meta_path(work_dir: Path) -> Path:
    return Path(work_dir) / PAGES_META_NAME


def page_dir(work_dir: Path, page_id: str) -> Path:
    return Path(work_dir) / validate_page_id(page_id)


def page_meta_path(work_dir: Path, page_id: str) -> Path:
    return page_dir(work_dir, page_id) / PAGE_META_NAME


def work_blend_path(work_dir: Path) -> Path:
    """作品マスター .blend のパス (``<work>.bname/work.blend``)."""
    return Path(work_dir) / WORK_BLEND_NAME


def coma_dir(work_dir: Path, page_id: str, coma_id: str) -> Path:
    return page_dir(work_dir, page_id) / validate_coma_id(coma_id)


def coma_blend_path(work_dir: Path, page_id: str, coma_id: str) -> Path:
    return coma_dir(work_dir, page_id, coma_id) / f"{validate_coma_id(coma_id)}.blend"


def coma_json_path(work_dir: Path, page_id: str, coma_id: str) -> Path:
    return coma_dir(work_dir, page_id, coma_id) / f"{validate_coma_id(coma_id)}.json"


def coma_thumb_path(work_dir: Path, page_id: str, coma_id: str) -> Path:
    return coma_dir(work_dir, page_id, coma_id) / f"{validate_coma_id(coma_id)}_thumb.png"


def coma_preview_path(work_dir: Path, page_id: str, coma_id: str) -> Path:
    return coma_dir(work_dir, page_id, coma_id) / f"{validate_coma_id(coma_id)}_preview.png"


def coma_passes_dir(work_dir: Path, page_id: str, coma_id: str) -> Path:
    return coma_dir(work_dir, page_id, coma_id) / "passes"


def coma_passes_cube_dir(work_dir: Path, page_id: str, coma_id: str) -> Path:
    return coma_passes_dir(work_dir, page_id, coma_id) / "cube"


def assets_dir(work_dir: Path) -> Path:
    return Path(work_dir) / ASSETS_DIR_NAME


def scenario_dir(work_dir: Path) -> Path:
    return Path(work_dir) / SCENARIO_DIR_NAME


def scenario_file(work_dir: Path) -> Path:
    return scenario_dir(work_dir) / SCENARIO_FILE_NAME


def exports_dir(work_dir: Path) -> Path:
    return Path(work_dir) / EXPORTS_DIR_NAME


def raster_dir(work_dir: Path) -> Path:
    return Path(work_dir) / RASTER_DIR_NAME


def raster_trash_dir(work_dir: Path) -> Path:
    return raster_dir(work_dir) / RASTER_TRASH_DIR_NAME


def raster_png_path(work_dir: Path, raster_id: str) -> Path:
    safe_id = re.sub(r"[^0-9a-fA-F]", "", str(raster_id or ""))[:12]
    if not safe_id:
        raise ValueError(f"invalid raster id: {raster_id!r}")
    return raster_dir(work_dir) / f"{safe_id}.png"


def ensure_bname_suffix(path: Path) -> Path:
    """``.bname`` 拡張子を持たせたディレクトリパスを返す (既に持っていればそのまま)."""
    p = Path(path)
    if p.suffix == BNAME_DIR_SUFFIX:
        return p
    return p.with_suffix(BNAME_DIR_SUFFIX)


def as_relative(path: Path, base: Path) -> Path:
    """base からの相対パスを返す。別ドライブ等で不可なら絶対パスを返す."""
    try:
        return Path(path).resolve().relative_to(Path(base).resolve())
    except ValueError:
        return Path(path).resolve()


def next_available_page_index(existing_ids: Iterable[str]) -> int:
    """既存ページ ID から空き番号の最小値を採番."""
    used: set[int] = set()
    for page_id in existing_ids:
        head = str(page_id).split("-", 1)[0]  # 見開きは左ページ番号を使う
        if head.startswith("p"):
            head = head[1:]
        if head.isdigit():
            used.add(int(head))
    i = 1
    while i in used:
        i += 1
    return i


def next_available_coma_index(existing_ids: Iterable[str]) -> int:
    """既存コマ ID から空き番号の最小値を採番."""
    used: set[int] = set()
    for coma_id in existing_ids:
        coma_id = str(coma_id)
        if is_valid_coma_id(coma_id):
            used.add(int(coma_id[1:]))
    i = 1
    while i in used:
        i += 1
    if i > 99:
        raise ValueError("coma count exceeds maximum c99")
    return i
