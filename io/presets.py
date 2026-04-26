"""用紙プリセット管理.

2 層で保持 (計画書 3.2.3 / 4.3):
- グローバル: アドオン同梱の ``presets/paper/`` (B-Name/presets/paper/)
- 作品ローカル: ``MyWork.bname/assets/templates/``

既定プリセット「集英社マンガ誌汎用」は同梱 JSON として配布する。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import json_io, log, paths
from . import schema

_logger = log.get_logger(__name__)

# アドオンルート直下の presets/paper/
_ADDON_ROOT = Path(__file__).resolve().parent.parent
GLOBAL_PRESETS_DIR = _ADDON_ROOT / "presets" / "paper"

PRESET_SUFFIX = ".json"


@dataclass(frozen=True)
class PaperPreset:
    name: str
    description: str
    path: Path
    source: str  # "global" | "local"
    data: dict[str, Any]


# ---------- 列挙 ----------


def list_global_presets() -> list[PaperPreset]:
    return _list_presets_in_dir(GLOBAL_PRESETS_DIR, source="global")


def list_local_presets(work_dir: Path) -> list[PaperPreset]:
    templates = paths.assets_dir(Path(work_dir)) / paths.ASSETS_TEMPLATES_DIR
    return _list_presets_in_dir(templates, source="local")


def list_all_presets(work_dir: Path | None) -> list[PaperPreset]:
    """グローバル → 作品ローカルの順で返す (同名があればローカル優先で上書き)."""
    presets = {p.name: p for p in list_global_presets()}
    if work_dir is not None:
        for p in list_local_presets(work_dir):
            presets[p.name] = p
    return list(presets.values())


def _list_presets_in_dir(base: Path, *, source: str) -> list[PaperPreset]:
    if not base.is_dir():
        return []
    out: list[PaperPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read preset %s: %s", path, exc)
            continue
        if data.get("presetType") != "paper":
            continue
        name = data.get("presetName") or path.stem
        out.append(
            PaperPreset(
                name=name,
                description=data.get("description", ""),
                path=path,
                source=source,
                data=data,
            )
        )
    return out


# ---------- 適用・保存 ----------


def apply_preset_to_paper(preset: PaperPreset, paper) -> None:
    schema.paper_from_dict(paper, preset.data.get("paper", {}))
    paper.preset_name = preset.name


def save_local_preset(work_dir: Path, paper, name: str, description: str = "") -> Path:
    """現在の PaperSettings を作品ローカルプリセットとして保存."""
    templates = paths.assets_dir(Path(work_dir)) / paths.ASSETS_TEMPLATES_DIR
    templates.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_filename(name)
    out = templates / f"{safe_name}{PRESET_SUFFIX}"
    paper_data = schema.paper_to_dict(paper)
    paper_data["presetName"] = name
    data = {
        "schemaVersion": 1,
        "presetType": "paper",
        "presetName": name,
        "description": description,
        "paper": paper_data,
    }
    json_io.write_json(out, data)
    _logger.info("local preset saved: %s", out)
    return out


def load_preset_by_name(name: str, work_dir: Path | None) -> PaperPreset | None:
    for preset in list_all_presets(work_dir):
        if preset.name == name:
            return preset
    return None


def load_default_preset(paper) -> PaperPreset | None:
    """既定の「集英社マンガ誌汎用」を PaperSettings に適用."""
    for preset in list_global_presets():
        if preset.name == "集英社マンガ誌汎用":
            apply_preset_to_paper(preset, paper)
            return preset
    _logger.warning("default preset '集英社マンガ誌汎用' not found under %s", GLOBAL_PRESETS_DIR)
    return None


# ---------- util ----------

_FORBIDDEN_CHARS = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _FORBIDDEN_CHARS else ch for ch in name.strip())
    cleaned = cleaned.rstrip(". ")
    return cleaned or "preset"
