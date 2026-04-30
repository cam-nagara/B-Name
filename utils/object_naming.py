"""B-Name Object/Collection 名の prefix 生成と UTF-8 安全切詰め.

計画書 ``docs/outliner_object_layer_plan_2026-04-30.md`` §3.2 を実装。

Object/Collection 名はユーザーが Outliner からリネーム可能なので、真の安定 ID
は ``object["bname_id"]`` (custom property) に保持し、Object 名は派生表示
として B-Name から自動生成する。

Blender 5.1.1 実機で確認した制約:
    - ID name の上限は 255 バイト (UTF-8)。超過分は内部で黙って切り詰められる。
    - 同名衝突時は Blender が ``.001`` ``.002`` を自動付加する。

そのためここでは:
    1. prefix (例: ``L0040__text__``) を生成する。
    2. タイトル部分を UTF-8 安全 (文字境界) に切り詰める。
    3. 結果が 255 バイトを超える場合は ``bname_title_truncated`` フラグを立てる。
    4. ``bname_id`` から既存 Object を逆引きする。
"""

from __future__ import annotations

from typing import Iterable, Optional

import bpy

# Blender 5.1 の ID name 上限。実機検証で 255 バイトを確認 (`a` * 100 が通り
# `"コマ" * 50` (150 文字) が 85 文字 = 255 バイトで打ち切られた)。
MAX_OBJECT_NAME_BYTES = 255

# `.001` 自動付加分とユーザーリネームの予備として確保するマージン。
NAME_SAFETY_MARGIN_BYTES = 8

# kind ごとの prefix 文字 (計画書 §3.2)。
KIND_PREFIX = {
    "page": "P",
    "coma": "C",
    "folder": "F",
    "outside": "P",  # outside は P0000 固定で alpha ソートで先頭に来るようにする
    "image": "L",
    "raster": "L",
    "gp": "L",
    "balloon": "L",
    "text": "L",
    "effect": "L",
}

# Object/Collection の custom property キー
PROP_KIND = "bname_kind"
PROP_ID = "bname_id"
PROP_PARENT_KEY = "bname_parent_key"
PROP_FOLDER_ID = "bname_folder_id"
PROP_Z_INDEX = "bname_z_index"
PROP_TITLE = "bname_title"
PROP_MANAGED = "bname_managed"
PROP_NO_NORMALIZE = "bname_no_normalize"
PROP_TITLE_TRUNCATED = "bname_title_truncated"


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """``text`` を UTF-8 で ``max_bytes`` 以下になるよう文字境界で切詰める.

    戻り値: ``(切詰め後文字列, 切詰めが発生したか)``.
    """
    if max_bytes <= 0:
        return "", bool(text)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    # 文字境界で切詰める。UTF-8 の続行バイトは 0b10xxxxxx なので、後ろから
    # リーディングバイトに当たる位置まで戻す。境界条件に注意:
    # cut が len(encoded) を越えないこと、cut == 0 で空文字を返すこと。
    cut = min(max_bytes, len(encoded) - 1)
    while cut > 0 and cut < len(encoded) and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    if cut <= 0:
        return "", True
    return encoded[:cut].decode("utf-8", errors="ignore"), True


def make_prefix(kind: str, z_index: int, sub_id: str = "") -> str:
    """``L0040__text__`` 形式の prefix を生成する.

    Args:
        kind: ``image`` ``raster`` ``gp`` 等のレイヤー種別、または
            ``page`` ``coma`` ``folder`` ``outside``。
        z_index: 0 詰め 4 桁の数値。レイヤーの場合は z_index、ページ/コマの
            場合はその ID 数値部 (``p0001`` → 1)。
        sub_id: ページ/コマ/フォルダの場合の追加識別子 (``p0001`` ``c01``
            ``folder_xxxxxx`` 等)。レイヤーの場合は kind 名を再掲する。

    Returns:
        例: ``L0040__text__`` ``P0001__p0001__`` ``C0010__c01__``。
    """
    prefix_char = KIND_PREFIX.get(kind, "L")
    if sub_id:
        return f"{prefix_char}{z_index:04d}__{sub_id}__"
    return f"{prefix_char}{z_index:04d}__{kind}__"


def make_canonical_name(
    kind: str,
    z_index: int,
    sub_id: str,
    title: str,
) -> tuple[str, bool]:
    """``L0040__text__セリフ本文`` 形式の正規 Object 名を生成する.

    255 バイトを超える場合は title を UTF-8 安全に切詰める。

    Returns:
        ``(name, truncated)``. truncated は title が切詰められたら True。
    """
    prefix = make_prefix(kind, z_index, sub_id)
    budget = MAX_OBJECT_NAME_BYTES - NAME_SAFETY_MARGIN_BYTES - len(
        prefix.encode("utf-8")
    )
    if budget <= 0:
        # prefix だけで budget を食い切るケースは想定外だが念のため
        return prefix.rstrip("_"), True
    title_clean = (title or "").strip()
    truncated_title, was_truncated = _truncate_utf8(title_clean, budget)
    return f"{prefix}{truncated_title}", was_truncated


def parse_canonical_name(name: str) -> Optional[tuple[str, str, str]]:
    """``L0040__text__セリフ本文`` を ``(prefix_id, sub_id, title)`` に分解.

    解析できない場合 (ユーザーが prefix を完全に潰した場合など) は None。

    Blender 自動付加の ``.001`` `.002` はここでは除去しない。タイトル末尾に
    ユーザーが意図して `.123` を入れたケースを誤除去しないため。Object 同定は
    ``bname_id`` custom property で行うので、Blender の自動付加サフィックスは
    parse 結果に含まれていても支障がない。
    """
    parts = name.split("__", 2)
    if len(parts) < 3:
        return None
    prefix_id, sub_id, title = parts[0], parts[1], parts[2]
    if not prefix_id or len(prefix_id) < 2:
        return None
    return prefix_id, sub_id, title


def assign_canonical_name(obj, kind: str, z_index: int, sub_id: str, title: str) -> str:
    """Object/Collection に正規名を付け直す.

    切詰め発生時は ``bname_title_truncated`` を立てる。Object 名衝突は
    Blender が ``.001`` を自動付加するため気にしない (``bname_id`` で逆引きする)。

    library override / linked Object は名前変更が拒否されるため、リネームを
    試みず custom property のみ更新する。
    """
    # library 由来 (linked / override) は名前変更不可
    try:
        if getattr(getattr(obj, "id_data", obj), "library", None) is not None:
            return obj.name
    except Exception:
        pass
    name, truncated = make_canonical_name(kind, z_index, sub_id, title)
    try:
        obj.name = name
    except Exception:
        pass
    try:
        if truncated:
            obj[PROP_TITLE_TRUNCATED] = True
        elif PROP_TITLE_TRUNCATED in obj:
            del obj[PROP_TITLE_TRUNCATED]
    except Exception:
        pass
    return obj.name


def stamp_identity(
    obj,
    *,
    kind: str,
    bname_id: str,
    title: str = "",
    z_index: int = 0,
    parent_key: str = "",
    folder_id: str = "",
    managed: bool = True,
) -> None:
    """Object/Collection に B-Name 安定 ID と関連メタを書き込む.

    既存値を上書きする。``bname_managed`` を False にしたい場合は呼出側で
    後から書き換えるか、``managed=False`` を指定する。
    """
    obj[PROP_KIND] = kind
    obj[PROP_ID] = bname_id
    obj[PROP_TITLE] = title
    obj[PROP_Z_INDEX] = int(z_index)
    obj[PROP_PARENT_KEY] = parent_key
    obj[PROP_FOLDER_ID] = folder_id
    obj[PROP_MANAGED] = bool(managed)


def is_managed(obj) -> bool:
    """B-Name 管理対象かどうか."""
    return bool(obj.get(PROP_MANAGED, False))


def should_skip_normalize(obj) -> bool:
    """``bname_no_normalize`` 退避フラグが立っているか."""
    return bool(obj.get(PROP_NO_NORMALIZE, False))


def get_bname_id(obj) -> str:
    return str(obj.get(PROP_ID, "") or "")


def get_kind(obj) -> str:
    return str(obj.get(PROP_KIND, "") or "")


def find_object_by_bname_id(bname_id: str, kind: str = "") -> Optional[bpy.types.Object]:
    """``bname_id`` から Object を逆引きする (``.001`` 付き名にも対応).

    同 ``bname_id`` を持つ Object が複数あれば最初に見つかったもの。
    """
    if not bname_id:
        return None
    for obj in bpy.data.objects:
        if obj.get(PROP_ID) != bname_id:
            continue
        if kind and obj.get(PROP_KIND) != kind:
            continue
        return obj
    return None


def find_collection_by_bname_id(
    bname_id: str, kind: str = ""
) -> Optional[bpy.types.Collection]:
    """``bname_id`` から Collection を逆引きする."""
    if not bname_id:
        return None
    for coll in bpy.data.collections:
        if coll.get(PROP_ID) != bname_id:
            continue
        if kind and coll.get(PROP_KIND) != kind:
            continue
        return coll
    return None


def iter_managed_objects(kind: str = "") -> Iterable[bpy.types.Object]:
    """B-Name 管理 Object を列挙する."""
    for obj in bpy.data.objects:
        if not is_managed(obj):
            continue
        if kind and obj.get(PROP_KIND) != kind:
            continue
        yield obj


def iter_managed_collections(kind: str = "") -> Iterable[bpy.types.Collection]:
    """B-Name 管理 Collection を列挙する."""
    for coll in bpy.data.collections:
        if not is_managed(coll):
            continue
        if kind and coll.get(PROP_KIND) != kind:
            continue
        yield coll


def page_id_to_z_number(page_id: str) -> int:
    """``p0001`` → 1, ``p0001-0002`` → 1 (見開きの最初を採用) を返す.

    数値抽出に失敗した場合は 0。先頭の `p`/`P` は **1 文字だけ** 除去する
    (誤入力 ``pp0001`` のようなケースで複数ページが同 z 値に潰れないように)。
    """
    if not page_id:
        return 0
    cleaned = page_id[1:] if page_id[0] in ("p", "P") else page_id
    head = cleaned.split("-", 1)[0]
    try:
        return int(head)
    except ValueError:
        return 0


def coma_id_to_z_number(coma_id: str) -> int:
    """``c01`` → 1 を返す."""
    if not coma_id:
        return 0
    cleaned = coma_id[1:] if coma_id[0] in ("c", "C") else coma_id
    try:
        return int(cleaned)
    except ValueError:
        return 0
