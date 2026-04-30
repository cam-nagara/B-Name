"""3D View ヘッダーの「ページ一覧に戻る」ボタン (フォールバック UI).

N パネル B-Name タブが閉じている、または ``bname_mode`` が
load_post 失敗で未同期のケースでも、ユーザーが必ず戻り道を持てるよう
3D View ヘッダーに常時アクセス可能なボタンを差し込む。

表示条件:
- 現在の mainfile が ``pNNNN/cNN/cNN.blend`` 形式 (= コマ編集中)
- または ``bname_mode == MODE_COMA``
"""

from __future__ import annotations

from pathlib import Path

import bpy

from ..core.mode import MODE_COMA, get_mode
from ..utils import paths as _paths


def _is_in_coma_blend() -> bool:
    fp = bpy.data.filepath
    if not fp:
        return False
    try:
        path = Path(fp).resolve()
    except OSError:
        return False
    parts = path.parts
    if len(parts) < 3:
        return False
    page_id, coma_id, fname = parts[-3], parts[-2], parts[-1]
    return (
        _paths.is_valid_page_id(page_id)
        and _paths.is_valid_coma_id(coma_id)
        and fname == f"{coma_id}.blend"
    )


def _draw_header(self, context):
    if get_mode(context) != MODE_COMA and not _is_in_coma_blend():
        return
    layout = self.layout
    layout.separator()
    layout.operator(
        "bname.exit_coma_mode_safe",
        text="ページ一覧へ",
        icon="BACK",
    )


def register() -> None:
    bpy.types.VIEW3D_HT_header.append(_draw_header)


def unregister() -> None:
    try:
        bpy.types.VIEW3D_HT_header.remove(_draw_header)
    except (ValueError, RuntimeError, AttributeError):
        pass
