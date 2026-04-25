"""JSON 読み書き共通ヘルパ.

- 読込み: UTF-8 / UTF-8 BOM 両対応 (Meldex 等が BOM 付きで吐いたファイルも受理)
- 書込み: UTF-8 (BOM なし) / インデント 2 / 非 ASCII をエスケープしない
- アトミック書込み: 同ディレクトリの一時ファイルへ書いてから rename

JSON ファイルは .bname フォルダ内の work.json / pages.json / page.json /
panel_NNN.json / imported.json で共通利用する。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def read_json(path: str | os.PathLike) -> Any:
    """UTF-8 / UTF-8-BOM 両対応で JSON を読み込む."""
    p = Path(path)
    with open(p, "rb") as f:
        raw = f.read()
    # UTF-8 BOM (EF BB BF) を剥がす
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8")
    return json.loads(text)


def write_json(path: str | os.PathLike, data: Any, *, indent: int = 2) -> None:
    """UTF-8 (BOM なし) でアトミックに JSON を書き込む.

    同ディレクトリ内の一時ファイルに書いてから os.replace で置き換えるため、
    書込み途中の停電・クラッシュでも破損ファイルが残らない。

    Windows で Dropbox / OneDrive / アンチウィルス / Indexer 等が同名ファイルを
    瞬間的に握っていると ``os.replace`` が EACCES (PermissionError) で失敗する
    ことがある。これを exponential backoff で短時間リトライして回避する。
    対象 OSError は (PermissionError, OSError errno=13/32) を含む。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # delete=False で自分で削除/rename を管理
    fd, tmp_path = tempfile.mkstemp(
        prefix=p.name + ".", suffix=".tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.write("\n")
        # アトミック置換 (Dropbox 等のロック競合に備えてリトライ)
        _replace_with_retry(tmp_path, p)
    except Exception:
        # 失敗時は一時ファイルを掃除
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _replace_with_retry(src: str, dst: Path,
                        attempts: int = 6, initial_delay: float = 0.1) -> None:
    """``os.replace`` を exponential backoff でリトライ.

    Windows + Dropbox 環境で頻発する WinError 5 / WinError 32 (sharing
    violation) を回避する。各試行間隔は 0.1s → 0.2s → 0.4s ... と倍化、
    合計待ち時間は約 6.3 秒。最終的に失敗したら例外を再送出する。
    """
    delay = initial_delay
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except (PermissionError, OSError) as exc:
            last_exc = exc
            # 最終試行ならエラーをそのまま投げる
            if i == attempts - 1:
                break
            time.sleep(delay)
            delay *= 2.0
    # ここに来たら全試行失敗
    if last_exc is not None:
        raise last_exc


def read_json_or_default(path: str | os.PathLike, default: Any) -> Any:
    """ファイルが無い / 壊れている場合は default を返す."""
    p = Path(path)
    if not p.is_file():
        return default
    try:
        return read_json(p)
    except (OSError, json.JSONDecodeError):
        return default
