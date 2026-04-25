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
PAGES_DIR_NAME = "pages"
PANELS_DIR_NAME = "panels"
ASSETS_DIR_NAME = "assets"
ASSETS_TEMPLATES_DIR = "templates"
ASSETS_BRUSHES_DIR = "brushes"
ASSETS_MODELS_DIR = "models"
ASSETS_BALLOONS_DIR = "balloons"
ASSETS_EFFECTS_DIR = "effects"
SCENARIO_DIR_NAME = "scenario"
SCENARIO_FILE_NAME = "imported.json"
EXPORTS_DIR_NAME = "exports"

BNAME_DIR_SUFFIX = ".bname"


# 単ページ ("0001") と見開き ("0020-0021") のみ許可
_PAGE_ID_RE = re.compile(r"^\d{4}(-\d{4})?$")
_PANEL_STEM_RE = re.compile(r"^panel_\d{3}$")


def is_valid_page_id(page_id: str) -> bool:
    return isinstance(page_id, str) and bool(_PAGE_ID_RE.match(page_id))


def is_valid_panel_stem(stem: str) -> bool:
    return isinstance(stem, str) and bool(_PANEL_STEM_RE.match(stem))


def validate_page_id(page_id: str) -> str:
    """不正な page_id ならエラー。呼び出し側はパス結合前に必ず通すこと."""
    if not is_valid_page_id(page_id):
        raise ValueError(f"invalid page_id: {page_id!r}")
    return page_id


def validate_panel_stem(stem: str) -> str:
    if not is_valid_panel_stem(stem):
        raise ValueError(f"invalid panel stem: {stem!r}")
    return stem


def format_page_id(index: int) -> str:
    """ページ番号を 4 桁ゼロパディング文字列に変換 (例: 1 → "0001")."""
    if index < 0:
        raise ValueError(f"page index must be non-negative: {index}")
    return f"{index:04d}"


def format_spread_id(left: int, right: int) -> str:
    """見開きページの ID を生成 (例: 20, 21 → "0020-0021")."""
    return f"{format_page_id(left)}-{format_page_id(right)}"


def format_panel_stem(index: int) -> str:
    """コマファイルの連番 stem (3 桁ゼロパディング、例: 1 → "panel_001")."""
    if index < 0:
        raise ValueError(f"panel index must be non-negative: {index}")
    return f"panel_{index:03d}"


def work_meta_path(work_dir: Path) -> Path:
    return Path(work_dir) / WORK_META_NAME


def pages_meta_path(work_dir: Path) -> Path:
    return Path(work_dir) / PAGES_META_NAME


def page_dir(work_dir: Path, page_id: str) -> Path:
    return Path(work_dir) / PAGES_DIR_NAME / page_id


def page_meta_path(work_dir: Path, page_id: str) -> Path:
    return page_dir(work_dir, page_id) / PAGE_META_NAME


def work_blend_path(work_dir: Path) -> Path:
    """作品マスター .blend のパス (``<work>.bname/work.blend``)."""
    return Path(work_dir) / WORK_BLEND_NAME


def panels_dir(work_dir: Path, page_id: str) -> Path:
    return page_dir(work_dir, page_id) / PANELS_DIR_NAME


def panel_blend_path(work_dir: Path, page_id: str, panel_index: int) -> Path:
    return panels_dir(work_dir, page_id) / f"{format_panel_stem(panel_index)}.blend"


def panel_meta_path(work_dir: Path, page_id: str, panel_index: int) -> Path:
    return panels_dir(work_dir, page_id) / f"{format_panel_stem(panel_index)}.json"


def panel_thumb_path(work_dir: Path, page_id: str, panel_index: int) -> Path:
    return panels_dir(work_dir, page_id) / f"{format_panel_stem(panel_index)}_thumb.png"


def panel_preview_path(work_dir: Path, page_id: str, panel_index: int) -> Path:
    return panels_dir(work_dir, page_id) / f"{format_panel_stem(panel_index)}_preview.png"


def assets_dir(work_dir: Path) -> Path:
    return Path(work_dir) / ASSETS_DIR_NAME


def scenario_dir(work_dir: Path) -> Path:
    return Path(work_dir) / SCENARIO_DIR_NAME


def scenario_file(work_dir: Path) -> Path:
    return scenario_dir(work_dir) / SCENARIO_FILE_NAME


def exports_dir(work_dir: Path) -> Path:
    return Path(work_dir) / EXPORTS_DIR_NAME


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
        head = page_id.split("-", 1)[0]  # 見開きは左ページ番号を使う
        if head.isdigit():
            used.add(int(head))
    i = 1
    while i in used:
        i += 1
    return i


def next_available_panel_index(existing_stems: Iterable[str]) -> int:
    """既存コマ stem から空き番号の最小値を採番."""
    used: set[int] = set()
    for stem in existing_stems:
        if stem.startswith("panel_"):
            tail = stem[len("panel_"):]
            if tail.isdigit():
                used.add(int(tail))
    i = 1
    while i in used:
        i += 1
    return i
