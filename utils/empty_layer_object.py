"""画像 / テキスト レイヤーの Outliner 用 Empty Object ヘルパ.

設計方針 (テキストと画像はオーバーレイ描画方式に統一):
    - Outliner 上は **軽量な Empty Object** だけを置き、Object 化のメリット
      (D&D / 親子変更 / 表示 ON/OFF / Modifier ベースのマスク) を享受する。
    - 実際の絵柄描画は既存 GPU オーバーレイ (`ui/overlay_image.py` /
      `ui/overlay_text.py`) が担当する。
    - Pillow 経由の画像生成や Image データブロック大量生成を回避し、編集
      応答とメモリ消費を改善。

export pipeline (`io/export_pipeline.py`) は **PropertyGroup (BNameImageLayer
/ BNameTextEntry) を直接読んで Pillow 合成** しているため、Empty 化しても
PNG / PSD 出力結果には影響しない。

Empty Object の役割:
    - `bname_kind` / `bname_id` / `bname_managed` / `bname_parent_key` /
      `bname_z_index` / `bname_title` を保持
    - location は entry の x_mm / y_mm から mm→m 換算で同期
    - empty_display_type で視認性確保 (PLAIN_AXES + 小さい size)
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import layer_object_sync as los
from . import log
from . import object_naming as on
from .geom import mm_to_m

_logger = log.get_logger(__name__)

IMAGE_EMPTY_NAME_PREFIX = "image_"
TEXT_EMPTY_NAME_PREFIX = "text_"

# Empty 表示サイズ (m)。1mm 相当でほぼ点
_EMPTY_DISPLAY_SIZE = 0.001
_EMPTY_DISPLAY_TYPE = "PLAIN_AXES"


def _resolve_parent_for_entry(entry, page, folder_id: str) -> tuple[str, str, str]:
    entry_parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    entry_parent_key = str(getattr(entry, "parent_key", "") or "")
    entry_folder_id = folder_id or str(getattr(entry, "folder_key", "") or "")
    if entry_parent_kind in {"none", "outside"}:
        return "outside", "", ""
    if entry_parent_kind == "coma" and entry_parent_key:
        return "coma", entry_parent_key, entry_folder_id
    if entry_parent_kind == "folder" and entry_folder_id:
        return "folder", entry_folder_id, entry_folder_id
    return (
        "page",
        entry_parent_key or str(getattr(page, "id", "") or ""),
        entry_folder_id,
    )


def _ensure_empty_object(name: str) -> bpy.types.Object:
    """Empty Object を ensure (既存があれば再利用)."""
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, None)
    # Empty として表示
    try:
        obj.empty_display_type = _EMPTY_DISPLAY_TYPE
        obj.empty_display_size = _EMPTY_DISPLAY_SIZE
    except Exception:  # noqa: BLE001
        pass
    return obj


def _stamp_and_link(
    obj: bpy.types.Object,
    *,
    kind: str,
    bname_id: str,
    title: str,
    z_index: int,
    parent_kind: str,
    parent_key: str,
    folder_id: str,
    scene: bpy.types.Scene,
) -> None:
    los.stamp_layer_object(
        obj,
        kind=kind,
        bname_id=bname_id,
        title=title,
        z_index=z_index,
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=folder_id,
        scene=scene,
    )


def ensure_image_empty_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    """``BNameImageLayer`` entry に対応する Empty Object を生成・更新する."""
    if scene is None or entry is None or page is None:
        return None
    image_id = str(getattr(entry, "id", "") or "")
    if not image_id:
        return None
    obj_name = f"{IMAGE_EMPTY_NAME_PREFIX}{image_id}"
    obj = _ensure_empty_object(obj_name)
    obj.location.x = mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0))
    obj.location.y = mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0))
    stamp_kind, stamp_key, stamp_folder = _resolve_parent_for_entry(
        entry, page, folder_id
    )
    # z_index は image_layers 配列 index ベース
    z_index = 0
    coll = getattr(scene, "bname_image_layers", None)
    if coll is not None:
        for i, e in enumerate(coll):
            if str(getattr(e, "id", "") or "") == image_id:
                z_index = (i + 1) * 10
                break
    _stamp_and_link(
        obj,
        kind="image",
        bname_id=image_id,
        title=str(getattr(entry, "title", "") or image_id),
        z_index=z_index,
        parent_kind=stamp_kind,
        parent_key=stamp_key,
        folder_id=stamp_folder,
        scene=scene,
    )
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    return obj


def ensure_text_empty_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    """``BNameTextEntry`` に対応する Empty Object を生成・更新する."""
    if scene is None or entry is None or page is None:
        return None
    text_id = str(getattr(entry, "id", "") or "")
    if not text_id:
        return None
    obj_name = f"{TEXT_EMPTY_NAME_PREFIX}{text_id}"
    obj = _ensure_empty_object(obj_name)
    obj.location.x = mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0))
    obj.location.y = mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0))
    stamp_kind, stamp_key, stamp_folder = _resolve_parent_for_entry(
        entry, page, folder_id
    )
    TEXT_Z_BASE = 2000
    z_index = TEXT_Z_BASE
    texts = getattr(page, "texts", None)
    if texts is not None:
        for i, e in enumerate(texts):
            if str(getattr(e, "id", "") or "") == text_id:
                z_index = TEXT_Z_BASE + (i + 1) * 10
                break
    _stamp_and_link(
        obj,
        kind="text",
        bname_id=text_id,
        title=str(getattr(entry, "body", "") or text_id)[:40],
        z_index=z_index,
        parent_kind=stamp_kind,
        parent_key=stamp_key,
        folder_id=stamp_folder,
        scene=scene,
    )
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    return obj


def find_image_entry(scene, image_id: str):
    coll = getattr(scene, "bname_image_layers", None) if scene is not None else None
    if coll is None:
        return None
    for e in coll:
        if str(getattr(e, "id", "") or "") == image_id:
            return e
    return None


def find_text_entry(scene, text_id: str):
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if work is None:
        return None, None
    for page in getattr(work, "pages", []):
        for entry in getattr(page, "texts", []):
            if str(getattr(entry, "id", "") or "") == text_id:
                return page, entry
    return None, None


# ---------- Empty.location → entry.x_mm/y_mm の双方向同期 ----------

def sync_entry_position_from_object(scene: bpy.types.Scene, obj: bpy.types.Object) -> bool:
    """Empty.location が変わったら対応 entry.x_mm/y_mm に書戻す.

    オーバーレイ描画は entry の x_mm/y_mm を読むため、Outliner 上 Empty を
    動かしても overlay 表示が連動するようにする。
    """
    if obj is None or not on.is_managed(obj):
        return False
    kind = on.get_kind(obj)
    if kind not in {"image", "text"}:
        return False
    bname_id = on.get_bname_id(obj)
    if not bname_id:
        return False

    new_x_mm = obj.location.x * 1000.0  # m → mm
    new_y_mm = obj.location.y * 1000.0

    if kind == "image":
        entry = find_image_entry(scene, bname_id)
        if entry is None:
            return False
    else:  # text
        _page, entry = find_text_entry(scene, bname_id)
        if entry is None:
            return False

    cur_x = float(getattr(entry, "x_mm", 0.0) or 0.0)
    cur_y = float(getattr(entry, "y_mm", 0.0) or 0.0)
    if abs(cur_x - new_x_mm) < 1e-4 and abs(cur_y - new_y_mm) < 1e-4:
        return False
    with los.suppress_sync():
        try:
            entry.x_mm = new_x_mm
            entry.y_mm = new_y_mm
        except Exception:  # noqa: BLE001
            _logger.exception("sync entry position failed: %s", bname_id)
            return False
    return True
