"""cNN の表示用プレビュー画像解決ヘルパ."""

from __future__ import annotations

from pathlib import Path

from . import paths


def coma_id_from_entry(entry) -> str:
    """ComaEntry から cNN ID を取り出す."""
    coma_id = str(getattr(entry, "coma_id", "") or getattr(entry, "id", "") or "")
    return coma_id if paths.is_valid_coma_id(coma_id) else ""


def coma_preview_source_path(work_dir: Path, page_id: str, entry) -> Path | None:
    """表示・書き出しに使う preview/thumb を返す.

    手動生成 preview と自動更新 thumb の両方がある場合は、新しい方を使う。
    これにより、古い preview が残っていても、コマ編集後の thumb が紙面表示を
    上書きできる。
    """
    coma_id = coma_id_from_entry(entry)
    if not coma_id:
        return None
    work_dir = Path(work_dir)
    candidates = [
        paths.coma_preview_path(work_dir, page_id, coma_id),
        paths.coma_thumb_path(work_dir, page_id, coma_id),
    ]
    existing = [p for p in candidates if p.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda p: (p.stat().st_mtime, p.name.endswith("_preview.png")))
