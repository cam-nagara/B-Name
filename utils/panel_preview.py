"""panel_NNN の表示用プレビュー画像解決ヘルパ."""

from __future__ import annotations

from pathlib import Path

from . import paths


def panel_index_from_entry(entry) -> int | None:
    """PanelEntry から panel_NNN の数値 index を取り出す."""
    stem = getattr(entry, "panel_stem", "")
    if isinstance(stem, str) and stem.startswith("panel_"):
        try:
            return int(stem.split("_", 1)[1])
        except (ValueError, IndexError):
            pass
    try:
        return int(getattr(entry, "id", ""))
    except (TypeError, ValueError):
        return None


def panel_preview_source_path(work_dir: Path, page_id: str, entry) -> Path | None:
    """表示・書き出しに使う panel preview/thumb を返す.

    手動生成 preview と自動更新 thumb の両方がある場合は、新しい方を使う。
    これにより、古い preview が残っていても、コマ編集後の thumb が紙面表示を
    上書きできる。
    """
    panel_index = panel_index_from_entry(entry)
    if panel_index is None:
        return None
    work_dir = Path(work_dir)
    candidates = [
        paths.panel_preview_path(work_dir, page_id, panel_index),
        paths.panel_thumb_path(work_dir, page_id, panel_index),
    ]
    existing = [p for p in candidates if p.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda p: (p.stat().st_mtime, p.name.endswith("_preview.png")))
