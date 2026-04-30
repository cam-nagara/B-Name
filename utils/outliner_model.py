"""Outliner Object/Collection モデルの構築と保守.

計画書 ``docs/outliner_object_layer_plan_2026-04-30.md`` Phase 0/1 の中核。

最終的な Outliner 構造 (計画書 §3.1):

    B-Name
      P0000__outside__ページ外
        L0010__image__参照画像
        L0020__gp__全体メモ
      P0001__p0001__1ページ
        C0010__c01__コマ1
          ...
        C0020__c02__コマ2
          ...

このモジュールは Collection の生成・保守・配置だけを扱う。Object 側 (画像 plane /
raster plane / GP Object) の生成は ``utils/layer_object_sync.py`` から呼ぶ。

依存関係: ``utils/object_naming.py`` のみ (上層には依存しない)。
"""

from __future__ import annotations

from typing import Iterable, Optional

import bpy

from . import log
from . import object_naming as on

_logger = log.get_logger(__name__)

ROOT_COLLECTION_NAME = "B-Name"

# outside 用の安定 ID (固定値)。``bname_id`` として書き込み、検出 scan の
# 突合に使う。
OUTSIDE_BNAME_ID = "__outside__"

# ページ / コマ Collection の color_tag (Blender 標準の COLOR_01..08)
# 紫 = COLOR_06、水色 (青) = COLOR_05
PAGE_COLOR_TAG = "COLOR_06"
COMA_COLOR_TAG = "COLOR_05"


def _set_collection_name_safe(coll: bpy.types.Collection, desired: str) -> None:
    """Collection 名を desired に揃える。既に同名なら no-op、不可なら無視."""
    if not desired:
        return
    if coll.name == desired:
        return
    try:
        coll.name = desired
    except Exception:  # noqa: BLE001
        pass


def _set_collection_color_tag(coll: bpy.types.Collection, tag: str) -> None:
    """color_tag を設定 (Blender 5.x で対応可)."""
    if not tag:
        return
    try:
        if getattr(coll, "color_tag", None) != tag:
            coll.color_tag = tag
    except Exception:  # noqa: BLE001
        pass


ROOT_BNAME_ID = "__root__"


def ensure_root_collection(scene: bpy.types.Scene) -> bpy.types.Collection:
    """``B-Name`` ルート Collection を確保し scene に link.

    まず ``bname_id="__root__"`` で逆引き、見つからなければ名前 ``B-Name`` で
    既存 Collection を再利用、それも無ければ新規作成。これによりユーザーが
    別目的で ``B-Name`` 名の Collection を作っていても、bname_id 同一の
    管理下 Collection を優先採用する。
    """
    coll = on.find_collection_by_bname_id(ROOT_BNAME_ID, kind="root")
    if coll is None:
        coll = bpy.data.collections.get(ROOT_COLLECTION_NAME)
        if coll is None:
            coll = bpy.data.collections.new(ROOT_COLLECTION_NAME)
    if scene is not None:
        scene_coll = scene.collection
        if coll.name not in scene_coll.children:
            try:
                scene_coll.children.link(coll)
            except Exception:  # noqa: BLE001
                _logger.exception("link root collection to scene failed")
    on.stamp_identity(
        coll,
        kind="root",
        bname_id=ROOT_BNAME_ID,
        title=ROOT_COLLECTION_NAME,
        z_index=0,
    )
    return coll


def ensure_outside_collection(scene: bpy.types.Scene) -> bpy.types.Collection:
    """``P0000__outside__ページ外`` Collection を確保."""
    root = ensure_root_collection(scene)
    existing = on.find_collection_by_bname_id(OUTSIDE_BNAME_ID, kind="outside")
    if existing is None:
        existing = bpy.data.collections.new("P0000__outside__ページ外")
    on.stamp_identity(
        existing,
        kind="outside",
        bname_id=OUTSIDE_BNAME_ID,
        title="ページ外",
        z_index=0,
    )
    _normalize_collection_parent(existing, root, scene)
    # シンプル名: "outside" (alpha sort で o は p の前に来る)
    _set_collection_name_safe(existing, "outside")
    return existing


def ensure_page_collection(
    scene: bpy.types.Scene, page_id: str, title: str = ""
) -> Optional[bpy.types.Collection]:
    """ページ Collection を確保し、ルート直下に置く."""
    if not page_id:
        return None
    root = ensure_root_collection(scene)
    coll = on.find_collection_by_bname_id(page_id, kind="page")
    if coll is None:
        z = on.page_id_to_z_number(page_id)
        canonical, _ = on.make_canonical_name("page", z, page_id, title or page_id)
        coll = bpy.data.collections.new(canonical)
    on.stamp_identity(
        coll,
        kind="page",
        bname_id=page_id,
        title=title or page_id,
        z_index=on.page_id_to_z_number(page_id),
    )
    # 既に scene.collection 直下や別の Collection 配下に置かれている場合も
    # root 直下のみへ正規化する。
    _normalize_collection_parent(coll, root, scene)
    # シンプル名 (page_id 直接) + 紫カラータグ
    _set_collection_name_safe(coll, page_id)
    _set_collection_color_tag(coll, PAGE_COLOR_TAG)
    return coll


def ensure_coma_collection(
    scene: bpy.types.Scene, page_id: str, coma_id: str, title: str = ""
) -> Optional[bpy.types.Collection]:
    """コマ Collection を確保し、ページ直下に置く.

    ``bname_id`` は ``"<page_id>:<coma_id>"`` として一意化する (異なるページ
    間で同じ ``c01`` が使われるため)。
    """
    if not page_id or not coma_id:
        return None
    page_coll = ensure_page_collection(scene, page_id)
    if page_coll is None:
        return None
    coma_bname_id = f"{page_id}:{coma_id}"
    coll = on.find_collection_by_bname_id(coma_bname_id, kind="coma")
    if coll is None:
        # 新規生成は coma_id を直接名前に。Blender が同名衝突時に .001 を
        # 付加するが、bname_id で逆引きするので問題ない。
        coll = bpy.data.collections.new(coma_id)
    on.stamp_identity(
        coll,
        kind="coma",
        bname_id=coma_bname_id,
        title=title or coma_id,
        z_index=on.coma_id_to_z_number(coma_id) * 10,
        parent_key=page_id,
    )
    # 既存の親リンクが page_coll でなければ正規化 (Phase 0-2 はページ間移動を拒否)
    _normalize_collection_parent(coll, page_coll, scene)
    # シンプル名 (coma_id 直接) + 水色カラータグ
    _set_collection_name_safe(coll, coma_id)
    _set_collection_color_tag(coll, COMA_COLOR_TAG)
    return coll


def ensure_folder_collection(
    scene: bpy.types.Scene,
    folder_id: str,
    title: str,
    parent_kind: str,
    parent_key: str,
    z_index: int,
) -> Optional[bpy.types.Collection]:
    """汎用フォルダ Collection を確保."""
    if not folder_id:
        return None
    coll = on.find_collection_by_bname_id(folder_id, kind="folder")
    if coll is None:
        # シンプル名: title 優先、なければ folder_id
        coll = bpy.data.collections.new(title or folder_id)
    on.stamp_identity(
        coll,
        kind="folder",
        bname_id=folder_id,
        title=title or folder_id,
        z_index=z_index,
        parent_key=parent_key,
        folder_id=folder_id,
    )
    parent_coll = _resolve_parent_collection(scene, parent_kind, parent_key)
    if parent_coll is not None:
        _normalize_collection_parent(coll, parent_coll, scene)
    _set_collection_name_safe(coll, title or folder_id)
    return coll


def _resolve_parent_collection(
    scene: bpy.types.Scene, parent_kind: str, parent_key: str
) -> Optional[bpy.types.Collection]:
    """``parent_kind`` / ``parent_key`` から対応 Collection を取得."""
    if parent_kind == "outside" or parent_kind == "none" or not parent_key:
        return ensure_outside_collection(scene)
    if parent_kind == "page":
        return on.find_collection_by_bname_id(parent_key, kind="page")
    if parent_kind == "coma":
        return on.find_collection_by_bname_id(parent_key, kind="coma")
    if parent_kind == "folder":
        return on.find_collection_by_bname_id(parent_key, kind="folder")
    return None


def _iter_potential_parent_collections(
    scene: Optional[bpy.types.Scene],
) -> Iterable[bpy.types.Collection]:
    """child Collection を引いてくる可能性のある親候補を列挙.

    ``bpy.data.collections`` には ``scene.collection`` が **含まれない**
    ため、scene 直下に link されたケースを検出するには明示的に追加する。
    """
    seen: set[int] = set()
    if scene is not None:
        sc = scene.collection
        if sc is not None:
            seen.add(id(sc))
            yield sc
    for c in bpy.data.collections:
        if id(c) in seen:
            continue
        seen.add(id(c))
        yield c


def _normalize_collection_parent(
    child: bpy.types.Collection,
    expected_parent: bpy.types.Collection,
    scene: Optional[bpy.types.Scene] = None,
) -> None:
    """``child`` を ``expected_parent`` 直下のみに置く (B-Name 管理に限る).

    管理外 Collection (``bname_managed`` False) は触らない。
    scene.collection を含めて走査するため、ユーザーが Outliner で
    シーン直下に D&D したケースも検出する。``children`` の包含判定は
    name 文字列ではなく **identity 比較** で行う (.001 自動付加リネーム後の
    名前ズレで検出失敗するのを防ぐ)。
    """
    if not on.is_managed(child):
        return
    if on.should_skip_normalize(child):
        return
    # identity 比較で親を検出
    parents = [
        c
        for c in _iter_potential_parent_collections(scene)
        if any(cc is child for cc in c.children)
    ]
    if expected_parent in parents and len(parents) == 1:
        return
    for p in parents:
        if p is expected_parent:
            continue
        try:
            p.children.unlink(child)
        except Exception:  # noqa: BLE001
            pass
    if expected_parent not in parents:
        try:
            expected_parent.children.link(child)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "normalize parent failed: %s -> %s", child.name, expected_parent.name
            )


def link_object_to_parent(
    scene: bpy.types.Scene,
    obj: bpy.types.Object,
    parent_kind: str,
    parent_key: str,
    folder_id: str = "",
) -> Optional[bpy.types.Collection]:
    """``obj`` を Collection の指定の親へ link し、他の B-Name 管理 Collection
    からは unlink する.

    管理外 Collection (例: scene.collection) は触らない (ユーザーの意図的
    多重 link を尊重)。
    """
    if folder_id:
        target = on.find_collection_by_bname_id(folder_id, kind="folder")
    else:
        target = _resolve_parent_collection(scene, parent_kind, parent_key)
    if target is None:
        return None

    # 既存の B-Name 管理 Collection への link を全部外す (`bname_no_normalize`
    # が立っていれば触らない)。scene.collection は管理外扱いなので残す。
    # users_collection で直接所属コレクションを取得 (O(M) → O(1) コレクション数)。
    if not on.should_skip_normalize(obj):
        for coll in list(getattr(obj, "users_collection", ()) or ()):
            if coll is target:
                continue
            if not on.is_managed(coll):
                continue
            try:
                coll.objects.unlink(obj)
            except Exception:  # noqa: BLE001
                pass

    # identity ベースで重複 link を回避
    if not any(o is obj for o in target.objects):
        try:
            target.objects.link(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("link object to parent failed: %s", obj.name)
            return None

    # custom property 反映
    obj[on.PROP_PARENT_KEY] = parent_key
    obj[on.PROP_FOLDER_ID] = folder_id
    return target


def parent_key_from_collection(coll: bpy.types.Collection) -> tuple[str, str]:
    """Collection の kind/bname_id から ``(parent_kind, parent_key)`` を返す."""
    kind = on.get_kind(coll)
    bname_id = on.get_bname_id(coll)
    if kind == "outside":
        return ("none", "")
    if kind == "page":
        return ("page", bname_id)
    if kind == "coma":
        return ("coma", bname_id)
    if kind == "folder":
        return ("folder", bname_id)
    return ("none", "")


def find_managed_parent_collection(
    obj: bpy.types.Object,
) -> Optional[bpy.types.Collection]:
    """``obj`` が現在 link されている B-Name 管理 Collection の 1 つを返す.

    複数あれば最初に見つかったもの (`§5.3` 正規化前提)。
    scene.collection は管理外なのでここでは見ない。``users_collection`` を
    直接参照して全 Collection 走査を避ける。
    """
    for coll in getattr(obj, "users_collection", ()) or ():
        if on.is_managed(coll):
            return coll
    return None
