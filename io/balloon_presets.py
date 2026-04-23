"""カスタムフキダシ形状プリセット管理 (計画書 3.1.4.2b).

2 層:
- 作品ローカル: MyWork.bname/assets/balloons/
- グローバル: <addon>/presets/balloons/

パスツールで作成した閉じた頂点列を JSON として保存。Phase 3 段階では
「選択中の BNameBalloonEntry (shape 任意) の 4 頂点 + 形状パラメータ」
を単純保存する。ベジェ曲線登録は Phase 3 後半。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import json_io, log, paths

_logger = log.get_logger(__name__)

_ADDON_ROOT = Path(__file__).resolve().parent.parent
GLOBAL_BALLOONS_DIR = _ADDON_ROOT / "presets" / "balloons"

PRESET_SUFFIX = ".json"


@dataclass(frozen=True)
class BalloonPreset:
    name: str
    description: str
    path: Path
    source: str  # "global" | "local"
    data: dict[str, Any]


def _list_in_dir(base: Path, *, source: str) -> list[BalloonPreset]:
    if not base.is_dir():
        return []
    out: list[BalloonPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read balloon preset %s: %s", path, exc)
            continue
        if data.get("presetType") != "balloon":
            continue
        name = data.get("presetName") or path.stem
        out.append(
            BalloonPreset(
                name=name,
                description=data.get("description", ""),
                path=path,
                source=source,
                data=data,
            )
        )
    return out


def list_global_presets() -> list[BalloonPreset]:
    return _list_in_dir(GLOBAL_BALLOONS_DIR, source="global")


def list_local_presets(work_dir: Path) -> list[BalloonPreset]:
    target = paths.assets_dir(Path(work_dir)) / paths.ASSETS_BALLOONS_DIR
    return _list_in_dir(target, source="local")


def list_all_presets(work_dir: Path | None) -> list[BalloonPreset]:
    presets = {p.name: p for p in list_global_presets()}
    if work_dir is not None:
        for p in list_local_presets(work_dir):
            presets[p.name] = p
    return list(presets.values())


def save_preset(
    out_path: Path,
    name: str,
    description: str,
    vertices_mm: list[tuple[float, float]],
    *,
    absolute_coords: bool = False,
    extras: dict | None = None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "schemaVersion": 1,
        "presetType": "balloon",
        "presetName": name,
        "description": description,
        "coordMode": "absolute" if absolute_coords else "relative",
        "vertices": [[round(x, 3), round(y, 3)] for x, y in vertices_mm],
    }
    if extras:
        data.update(extras)
    json_io.write_json(out_path, data)
    return out_path


def save_local_preset(
    work_dir: Path,
    name: str,
    description: str,
    vertices_mm: list[tuple[float, float]],
    absolute_coords: bool = False,
) -> Path:
    target_dir = paths.assets_dir(Path(work_dir)) / paths.ASSETS_BALLOONS_DIR
    safe = _sanitize_filename(name)
    out = target_dir / f"{safe}{PRESET_SUFFIX}"
    return save_preset(out, name, description, vertices_mm, absolute_coords=absolute_coords)


def save_global_preset(
    name: str,
    description: str,
    vertices_mm: list[tuple[float, float]],
    absolute_coords: bool = False,
) -> Path:
    safe = _sanitize_filename(name)
    out = GLOBAL_BALLOONS_DIR / f"{safe}{PRESET_SUFFIX}"
    return save_preset(out, name, description, vertices_mm, absolute_coords=absolute_coords)


_FORBIDDEN = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in name.strip())
    return cleaned.rstrip(". ") or "preset"
