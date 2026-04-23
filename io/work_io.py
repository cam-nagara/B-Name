"""作品 (.bname) フォルダの作成・読み込み・保存.

ディレクトリ構造は計画書 4.2 参照:
  MyWork.bname/
    work.json
    pages.json  (page_io 担当)
    pages/      (page_io 担当)
    assets/
    scenario/
    exports/
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..utils import json_io, log, paths
from . import schema

_logger = log.get_logger(__name__)


# ---------- 新規作成 ----------


def create_bname_skeleton(work_dir: Path) -> None:
    """.bname フォルダのディレクトリ骨格を作成する (中身の JSON は別関数で書く)."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / paths.PAGES_DIR_NAME).mkdir(exist_ok=True)
    assets = paths.assets_dir(work_dir)
    assets.mkdir(exist_ok=True)
    (assets / paths.ASSETS_BRUSHES_DIR).mkdir(exist_ok=True)
    (assets / paths.ASSETS_TEMPLATES_DIR).mkdir(exist_ok=True)
    (assets / paths.ASSETS_MODELS_DIR).mkdir(exist_ok=True)
    (assets / paths.ASSETS_BALLOONS_DIR).mkdir(exist_ok=True)
    (assets / paths.ASSETS_EFFECTS_DIR).mkdir(exist_ok=True)
    paths.scenario_dir(work_dir).mkdir(exist_ok=True)
    paths.exports_dir(work_dir).mkdir(exist_ok=True)
    _logger.info("bname skeleton created: %s", work_dir)


# ---------- work.json ----------


def save_work_json(work_dir: Path, work) -> Path:
    """BNameWorkData → work.json に保存."""
    work_dir = Path(work_dir)
    data = schema.work_to_dict(work)
    data["lastSaved"] = datetime.now().astimezone().isoformat(timespec="seconds")
    out = paths.work_meta_path(work_dir)
    json_io.write_json(out, data)
    _logger.debug("work.json saved: %s", out)
    return out


def load_work_json(work_dir: Path, work) -> dict[str, Any]:
    """work.json → BNameWorkData に読み込み。戻り値は読込み生 dict."""
    path = paths.work_meta_path(Path(work_dir))
    if not path.is_file():
        raise FileNotFoundError(f"work.json not found: {path}")
    data = json_io.read_json(path)
    _warn_if_unknown_schema(data.get("schemaVersion"), schema.WORK_SCHEMA_VERSION, "work.json")
    schema.work_from_dict(work, data)
    work.work_dir = str(Path(work_dir).resolve())
    work.loaded = True
    _logger.info("work.json loaded: %s", path)
    return data


def _warn_if_unknown_schema(found: Any, expected: int, label: str) -> None:
    if found is None:
        _logger.warning("%s: schemaVersion missing, treating as v%d", label, expected)
        return
    try:
        if int(found) > expected:
            _logger.warning(
                "%s: schemaVersion=%s is newer than supported v%d; loading anyway",
                label,
                found,
                expected,
            )
    except (TypeError, ValueError):
        _logger.warning("%s: invalid schemaVersion=%r", label, found)
