"""Grease Pencil v3 ヘルパ.

計画書 10 章参照. v3 API のみ使用 (v2 は使わない)。

**Blender バージョン間の API 差異**:
- Blender 4.3〜4.x: ``bpy.data.grease_pencils_v3`` (v2 と並存していた時期)
- Blender 5.x: ``bpy.data.grease_pencils`` (v3 が既定化、サフィックス撤去)

両方を自動検出して同じ方法で扱えるよう ``_gp_data_blocks()`` でラップ。

Phase 2 以降は「ページごとに 1 つの GP オブジェクト」モデル:
- ルート Collection ``B-Name`` の下に ページ Collection ``page_NNNN`` を持ち、
  その中に GP オブジェクト ``page_NNNN_sketch`` (データ ``page_NNNN_sketch_data``)
  を配置する。ページ Collection 自体の transform に grid offset (負の X) を
  かけることで overview での全ページ配置を実現する。
"""

from __future__ import annotations

from typing import Iterable

import bpy

from ..utils import log

_logger = log.get_logger(__name__)


def _gp_data_blocks():
    """v3 GreasePencil データブロックコレクションを返す.

    Blender 5.x は ``bpy.data.grease_pencils``、4.3〜4.x は
    ``bpy.data.grease_pencils_v3`` を公開する。どちらか存在するものを返す。
    どちらも無い場合は RuntimeError (v2 のみの古い Blender では動作しない)。
    """
    coll = getattr(bpy.data, "grease_pencils_v3", None)
    if coll is not None:
        return coll
    coll = getattr(bpy.data, "grease_pencils", None)
    if coll is not None:
        return coll
    raise RuntimeError(
        "Grease Pencil v3 data-blocks not available (requires Blender 4.3+)"
    )


# ---------- 命名規則 ----------

ROOT_COLLECTION_NAME = "B-Name"


def page_collection_name(page_id: str) -> str:
    return f"page_{page_id}"


def page_gp_object_name(page_id: str) -> str:
    return f"page_{page_id}_sketch"


def page_gp_data_name(page_id: str) -> str:
    return f"page_{page_id}_sketch_data"


# ---------- GP v3 低レベル ----------


def ensure_gpencil(name: str):
    """名前つき GreasePencil v3 データブロックを取得/生成."""
    blocks = _gp_data_blocks()
    gp_data = blocks.get(name)
    if gp_data is None:
        gp_data = blocks.new(name)
    return gp_data


def ensure_gpencil_object(name: str, link_to_collection=True):
    """v3 GreasePencil Object を取得/生成."""
    obj = bpy.data.objects.get(name)
    if obj is None:
        gp_data = ensure_gpencil(name + "_data")
        obj = bpy.data.objects.new(name, gp_data)
        if link_to_collection and bpy.context.scene is not None:
            bpy.context.scene.collection.objects.link(obj)
    return obj


def ensure_layer(gp_data, layer_name: str):
    """GreasePencil v3 レイヤーを取得/生成."""
    layer = gp_data.layers.get(layer_name)
    if layer is None:
        layer = gp_data.layers.new(layer_name)
    return layer


# ---------- Grease Pencil マテリアル ----------

_DEFAULT_STROKE_MAT_NAME = "BName_Pen_Black"
_LAYER_MATERIAL_PROP = "bname_material_name"
_LAYER_MATERIAL_PREFIX = "BName_GP_Layer_"


def ensure_default_stroke_material(
    obj,
    name: str = _DEFAULT_STROKE_MAT_NAME,
    color: tuple = (0.0, 0.0, 0.0, 1.0),
):
    """GP Object に黒線ストロークマテリアルを確保・attach し active 化.

    Blender UI の「Add > Grease Pencil > Empty」等で GP オブジェクトを
    生成すると既定の黒線マテリアルが自動付与されるが、Python API で
    ``bpy.data.grease_pencils.new()`` / ``bpy.data.objects.new()`` から
    直接生成した場合はマテリアルが付かない。結果として Draw モードで
    ストロークがブラシ既定色 (Pencil 等ではごく淡色) で描画され、
    「白い線しか出ない」ように見える。
    この関数は ``BName_Pen_Black`` マテリアルを確保し、GP Object の
    material slot に attach + active 化する。
    """
    if obj is None or obj.type != "GREASEPENCIL":
        return None

    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
        # GP v3 ではマテリアル生成直後に ``grease_pencil`` サブ struct が
        # 未初期化の場合があるので、旧 v2 互換 API の
        # ``create_gpencil_data`` で初期化を促す。既に存在していれば no-op。
        if getattr(mat, "grease_pencil", None) is None:
            try:
                bpy.data.materials.create_gpencil_data(mat)
            except (AttributeError, RuntimeError):
                pass
        gp_style = getattr(mat, "grease_pencil", None)
        if gp_style is not None:
            try:
                gp_style.show_stroke = True
            except Exception:  # noqa: BLE001
                pass
            try:
                gp_style.color = color
            except Exception:  # noqa: BLE001
                pass
            try:
                gp_style.show_fill = False
            except Exception:  # noqa: BLE001
                pass

    # Object の material slot に追加 (未追加なら)
    try:
        existing_names = [m.name for m in obj.data.materials if m is not None]
        if mat.name not in existing_names:
            obj.data.materials.append(mat)
            existing_names.append(mat.name)
        # active material slot をこのマテリアルに
        try:
            obj.active_material_index = existing_names.index(mat.name)
        except ValueError:
            pass
    except Exception:  # noqa: BLE001
        _logger.exception("ensure_default_stroke_material: attach failed")
    return mat


def _ensure_gp_material_data(mat):
    if mat is None:
        return None
    if getattr(mat, "grease_pencil", None) is None:
        try:
            bpy.data.materials.create_gpencil_data(mat)
        except (AttributeError, RuntimeError):
            pass
    return getattr(mat, "grease_pencil", None)


def _safe_material_suffix(name: str) -> str:
    cleaned = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in str(name))
    cleaned = cleaned.strip().strip(".")
    return cleaned or "Layer"


def _layer_material_name(layer) -> str:
    try:
        value = layer.get(_LAYER_MATERIAL_PROP, "")
        if value:
            return str(value)
    except Exception:  # noqa: BLE001
        pass
    return f"{_LAYER_MATERIAL_PREFIX}{_safe_material_suffix(getattr(layer, 'name', 'Layer'))}"


def _store_layer_material_name(layer, material_name: str) -> None:
    try:
        layer[_LAYER_MATERIAL_PROP] = material_name
    except Exception:  # noqa: BLE001
        pass


def _material_slot_index(obj, mat) -> int:
    mats = getattr(getattr(obj, "data", None), "materials", None)
    if mats is None or mat is None:
        return -1
    for i, existing in enumerate(mats):
        if existing is mat or getattr(existing, "name", None) == mat.name:
            return i
    try:
        mats.append(mat)
        return len(mats) - 1
    except Exception:  # noqa: BLE001
        _logger.exception("material slot append failed: %s", getattr(mat, "name", ""))
        return -1


def _assign_material_to_layer_strokes(layer, material_index: int) -> None:
    if material_index < 0:
        return
    frames = getattr(layer, "frames", None)
    if frames is None:
        return
    for frame in frames:
        drawing = getattr(frame, "drawing", None)
        strokes = getattr(drawing, "strokes", None)
        if strokes is None:
            continue
        for stroke in strokes:
            try:
                stroke.material_index = material_index
            except Exception:  # noqa: BLE001
                pass


def ensure_layer_material(
    obj,
    layer,
    *,
    activate: bool = False,
    assign_existing: bool = True,
):
    """GP レイヤー専用の内部マテリアルを確保し、必要なら active 化する.

    B-Name UI ではマテリアルを見せず、レイヤーの線色/塗り色として扱う。
    実体は Grease Pencil の描画仕様に合わせてレイヤーごとに 1 マテリアルを
    自動管理する。
    """
    if obj is None or layer is None or getattr(obj, "type", "") != "GREASEPENCIL":
        return None

    mat_name = _layer_material_name(layer)
    created = False
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(name=mat_name)
        created = True
        _store_layer_material_name(layer, mat.name)
    else:
        _store_layer_material_name(layer, mat.name)

    style_missing = getattr(mat, "grease_pencil", None) is None
    gp_style = _ensure_gp_material_data(mat)
    if gp_style is not None:
        if created or style_missing:
            try:
                gp_style.show_stroke = True
            except Exception:  # noqa: BLE001
                pass
            try:
                gp_style.color = (0.0, 0.0, 0.0, 1.0)
            except Exception:  # noqa: BLE001
                pass
            try:
                gp_style.fill_color = (1.0, 1.0, 1.0, 1.0)
            except Exception:  # noqa: BLE001
                pass
            try:
                gp_style.show_fill = False
            except Exception:  # noqa: BLE001
                pass
    try:
        mat.diffuse_color = tuple(getattr(gp_style, "color", mat.diffuse_color))
    except Exception:  # noqa: BLE001
        pass

    material_index = _material_slot_index(obj, mat)
    if activate and material_index >= 0:
        try:
            obj.active_material_index = material_index
        except Exception:  # noqa: BLE001
            pass
    if assign_existing:
        _assign_material_to_layer_strokes(layer, material_index)
    return mat


def ensure_active_layer_material(obj, *, activate: bool = True):
    layers = getattr(getattr(obj, "data", None), "layers", None)
    if layers is None:
        return None
    layer = getattr(layers, "active", None)
    return ensure_layer_material(obj, layer, activate=activate)


def layer_effectively_hidden(layer) -> bool:
    """レイヤー自身または親フォルダが非表示なら True."""
    if bool(getattr(layer, "hide", False)):
        return True
    group = getattr(layer, "parent_group", None)
    while group is not None:
        if bool(getattr(group, "hide", False)):
            return True
        group = getattr(group, "parent_group", None)
    return False


def layer_effectively_locked(layer) -> bool:
    """レイヤー自身または親フォルダがロックなら True."""
    if bool(getattr(layer, "lock", False)):
        return True
    group = getattr(layer, "parent_group", None)
    while group is not None:
        if bool(getattr(group, "lock", False)):
            return True
        group = getattr(group, "parent_group", None)
    return False


def is_layer_group(node) -> bool:
    return hasattr(node, "children") and hasattr(node, "is_expanded")


def unique_layer_group_name(gp_data, base: str = "フォルダ") -> str:
    groups = getattr(gp_data, "layer_groups", None)
    if groups is None:
        return base
    existing = {group.name for group in groups}
    name = base
    i = 0
    while name in existing:
        i += 1
        name = f"{base}.{i:03d}"
    return name


def move_layer_to_group(gp_data, layer, group) -> bool:
    layers = getattr(gp_data, "layers", None)
    if layers is None or layer is None:
        return False
    try:
        layers.move_to_layer_group(layer, group)
    except Exception:  # noqa: BLE001
        _logger.exception("move layer to group failed")
        return False
    return True


def move_group_to_group(gp_data, group, parent_group) -> bool:
    groups = getattr(gp_data, "layer_groups", None)
    if groups is None or group is None:
        return False
    try:
        groups.move_to_layer_group(group, parent_group)
    except Exception:  # noqa: BLE001
        _logger.exception("move group to group failed")
        return False
    return True


def remove_layer_group_preserve_children(gp_data, group) -> bool:
    """フォルダを削除し、中身のレイヤー/子フォルダは親階層へ退避する."""
    groups = getattr(gp_data, "layer_groups", None)
    if groups is None or group is None:
        return False
    parent = getattr(group, "parent_group", None)
    for child in list(getattr(group, "children", [])):
        if is_layer_group(child):
            move_group_to_group(gp_data, child, parent)
        else:
            move_layer_to_group(gp_data, child, parent)
    try:
        groups.remove(group)
    except Exception:  # noqa: BLE001
        _logger.exception("remove layer group failed: %s", getattr(group, "name", ""))
        return False
    return True


# ---------- ページ Collection / GP ----------


def ensure_root_collection(scene):
    """ルート Collection ``B-Name`` を scene 直下に確保."""
    root = bpy.data.collections.get(ROOT_COLLECTION_NAME)
    if root is None:
        root = bpy.data.collections.new(ROOT_COLLECTION_NAME)
    if scene is not None and root.name not in scene.collection.children:
        # 他の親 (data-level 孤児化) に既にリンクされている場合は触らない。
        # scene.collection 直下に無ければリンクする。
        if not _is_linked_anywhere_in_scene(scene, root):
            scene.collection.children.link(root)
    return root


def _is_linked_anywhere_in_scene(scene, collection) -> bool:
    """scene 以下の任意の Collection 階層に ``collection`` が既にリンクされているか."""
    def walk(coll):
        if coll is None:
            return False
        for child in coll.children:
            if child == collection:
                return True
            if walk(child):
                return True
        return False

    return walk(scene.collection)


def ensure_page_collection(scene, page_id: str):
    """``B-Name/page_NNNN`` Collection を取得/生成して返す."""
    root = ensure_root_collection(scene)
    name = page_collection_name(page_id)
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in root.children:
        if not _is_linked_anywhere_in_scene(scene, coll):
            root.children.link(coll)
    return coll


# ---------- master GP (作品全ページ共通の単一 GP) ----------
#
# 旧仕様: ページごとに page_NNNN_sketch GP を生成 → ストロークがどのページに
# 属するか分かりにくい問題があった。
# 新仕様: 作品全体で 1 つの ``bname_master_sketch`` GP を持つ。各レイヤーは
# 全ページに横断的に存在する (CSP のレイヤーパネル感覚)。ストロークの
# world 座標がそのままページ位置を表す。
# 既存 .blend に残る page_NNNN_sketch は「残置」(削除も移行もしない)。

MASTER_GP_OBJECT_NAME = "bname_master_sketch"
MASTER_GP_DATA_NAME = "bname_master_sketch_data"


def ensure_master_gpencil(scene, layer_name: str = "ネーム"):
    """作品全体で唯一の master GP オブジェクトを取得/生成して返す.

    - Object 名: ``bname_master_sketch``
    - Data 名: ``bname_master_sketch_data``
    - ルート Collection (B-Name) 直下にリンク
    - location は (0, 0, GP_Z_LIFT_M) 固定 (用紙 overlay z=0 より +1mm 手前)
    - 既定レイヤー + 現在フレーム + 黒線マテリアルを自動補完
    """
    from .page_grid import GP_Z_LIFT_M

    root = ensure_root_collection(scene)
    obj = bpy.data.objects.get(MASTER_GP_OBJECT_NAME)
    if obj is None:
        gp_data = ensure_gpencil(MASTER_GP_DATA_NAME)
        obj = bpy.data.objects.new(MASTER_GP_OBJECT_NAME, gp_data)
    # ルート Collection にリンク (他コレクションからは外す)
    _relink_object_to_collection_only(scene, obj, root)
    # location を固定 (Z リフトのみ)
    try:
        obj.location = (0.0, 0.0, GP_Z_LIFT_M)
    except Exception:  # noqa: BLE001
        pass
    # 既定レイヤー + フレーム
    layer = None
    if len(obj.data.layers) == 0:
        try:
            layer = ensure_layer(obj.data, layer_name)
        except Exception:  # noqa: BLE001
            _logger.exception("ensure_master_gpencil: default layer create failed")
    else:
        layer = getattr(obj.data.layers, "active", None) or obj.data.layers[0]
    if layer is not None and hasattr(layer, "frames"):
        if len(layer.frames) == 0:
            try:
                frame_num = scene.frame_current if scene is not None else 1
                ensure_active_frame(layer, frame_number=frame_num)
            except Exception:  # noqa: BLE001
                _logger.exception("ensure_master_gpencil: default frame create failed")
    # レイヤー専用マテリアル
    try:
        layers = getattr(obj.data, "layers", None)
        if layers is not None and len(layers) > 0:
            for existing_layer in layers:
                ensure_layer_material(
                    obj,
                    existing_layer,
                    activate=(existing_layer == layer),
                    assign_existing=True,
                )
        else:
            ensure_default_stroke_material(obj)
    except Exception:  # noqa: BLE001
        _logger.exception("ensure_master_gpencil: layer material setup failed")
    return obj


def get_master_gpencil():
    """既存の master GP オブジェクトを返す (無ければ None)."""
    return bpy.data.objects.get(MASTER_GP_OBJECT_NAME)


# ---------- 旧紙メッシュ互換 ----------

PAPER_MATERIAL_NAME = "BName_Paper_White"


def _ensure_paper_material():
    """全ページ共有の白マテリアルを取得/生成 (Solid 表示で白く見せる)."""
    mat = bpy.data.materials.get(PAPER_MATERIAL_NAME)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(PAPER_MATERIAL_NAME)
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)  # Solid Display=Material 時の表示色
    mat.use_nodes = False
    return mat


def _paper_rgba_from_value(color_value) -> tuple[float, float, float, float]:
    """PropertyGroup / シーケンス由来の色値を紙表示用 RGBA に正規化."""
    if color_value is None:
        return (1.0, 1.0, 1.0, 1.0)
    try:
        r = float(color_value[0])
        g = float(color_value[1])
        b = float(color_value[2])
    except Exception:  # noqa: BLE001
        return (1.0, 1.0, 1.0, 1.0)
    alpha = 1.0
    try:
        alpha = float(color_value[3])
    except Exception:  # noqa: BLE001
        alpha = 1.0
    return (
        max(0.0, min(1.0, r)),
        max(0.0, min(1.0, g)),
        max(0.0, min(1.0, b)),
        max(0.0, min(1.0, alpha)),
    )


def sync_paper_material_color(color_value) -> object | None:
    """旧紙メッシュ共有マテリアルが残っていれば ``paper_color`` に同期."""
    mat = bpy.data.materials.get(PAPER_MATERIAL_NAME)
    if mat is None:
        return None
    rgba = _paper_rgba_from_value(color_value)
    try:
        if tuple(float(c) for c in mat.diffuse_color[:4]) != rgba:
            mat.diffuse_color = rgba
        mat.update_tag()
    except Exception:  # noqa: BLE001
        _logger.exception("sync_paper_material_color: material update failed")
        return mat

    # 互換: 過去ファイルに紙オブジェクトが残っていた場合だけ色を合わせる。
    for obj in tuple(bpy.data.objects):
        if not obj.name.startswith("page_") or not obj.name.endswith("_paper"):
            continue
        try:
            obj.color = rgba
        except Exception:  # noqa: BLE001
            pass
    return mat


def page_paper_object_name(page_id: str) -> str:
    return f"page_{page_id}_paper"


def page_paper_mesh_name(page_id: str) -> str:
    return f"page_{page_id}_paper_data"


def remove_page_paper(page_id: str) -> None:
    """ページ用紙メッシュを削除する。用紙表示は GPU overlay で行う."""
    obj_name = page_paper_object_name(page_id)
    mesh_name = page_paper_mesh_name(page_id)
    obj = bpy.data.objects.get(obj_name)
    if obj is not None:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.exception("remove paper object failed: %s", obj_name)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is not None and mesh.users == 0:
        try:
            bpy.data.meshes.remove(mesh)
        except Exception:  # noqa: BLE001
            _logger.exception("remove paper mesh failed: %s", mesh_name)


def remove_all_page_papers() -> None:
    """旧仕様で作られた page_XXXX_paper 系オブジェクト/メッシュを掃除する."""
    for obj in tuple(bpy.data.objects):
        name = str(getattr(obj, "name", "") or "")
        if name.startswith("page_") and (name.endswith("_paper") or "_paper." in name):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:  # noqa: BLE001
                _logger.exception("remove paper object failed: %s", name)
    for mesh in tuple(bpy.data.meshes):
        name = str(getattr(mesh, "name", "") or "")
        if name.startswith("page_") and (name.endswith("_paper_data") or "_paper_data." in name):
            if mesh.users != 0:
                continue
            try:
                bpy.data.meshes.remove(mesh)
            except Exception:  # noqa: BLE001
                _logger.exception("remove paper mesh failed: %s", name)


def ensure_page_paper(
    scene,
    page_id: str,
    canvas_width_mm: float,
    canvas_height_mm: float,
    paper_color=None,
):
    """互換用 no-op。用紙表示は実メッシュではなく GPU overlay で行う."""
    _ = scene, canvas_width_mm, canvas_height_mm, paper_color
    remove_page_paper(page_id)
    return None


def get_page_paper(page_id: str):
    return bpy.data.objects.get(page_paper_object_name(page_id))


def ensure_page_gpencil(scene, page_id: str, layer_name: str = "ネーム"):
    """[新仕様] master GP のラッパー — ページ単位の GP は作らない.

    旧仕様 (page_NNNN_sketch) は廃止。ストロークがどのページにあるかを
    座標で判定する master GP 方式に統一。
    この関数は既存呼び出し箇所の互換維持のために残し、内部で:
      - ページ Collection を確保
      - 旧仕様の紙メッシュがあれば削除
      - master GP を ensure (作品で 1 つだけ)
    を実行し、master GP オブジェクトを返す。
    """
    # ページ Collection は旧 page GP 互換の入れ物として残すが、紙メッシュは作らない。
    ensure_page_collection(scene, page_id)
    remove_page_paper(page_id)
    # 新仕様: 全ページ共通の master GP を返す
    return ensure_master_gpencil(scene, layer_name=layer_name)


def _relink_object_to_collection_only(scene, obj, target_coll) -> None:
    """``obj`` を ``target_coll`` のみにリンクし、他の Collection からは外す.

    scene.collection 直下に残っていると overview の grid transform が効かない
    (scene.collection 直下のオブジェクトは Collection transform を持たない)。
    """
    # 既にリンク済みの Collection 一覧
    linked = [c for c in bpy.data.collections if obj.name in c.objects]
    # scene 直下リンクも外す
    if scene is not None and obj.name in scene.collection.objects:
        try:
            scene.collection.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass
    for c in linked:
        if c is target_coll:
            continue
        try:
            c.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass
    if obj.name not in target_coll.objects:
        try:
            target_coll.objects.link(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("link to %s failed", target_coll.name)


def remove_page_gpencil(page_id: str) -> None:
    """ページ GP オブジェクト / データ / Collection / 旧紙メッシュを完全削除.

    データブロックは users=0 になった段階でクリーンアップ。
    旧紙メッシュ (page_NNNN_paper) と紙メッシュデータも併せて削除する
    (残すと .blend サイズが膨らみ、削除済みページのデータが幽霊として残る)。
    """
    obj_name = page_gp_object_name(page_id)
    data_name = page_gp_data_name(page_id)
    coll_name = page_collection_name(page_id)

    # GP オブジェクト
    obj = bpy.data.objects.get(obj_name)
    if obj is not None:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.exception("remove GP object failed: %s", obj_name)

    remove_page_paper(page_id)

    # GP データブロック
    try:
        blocks = _gp_data_blocks()
    except RuntimeError:
        blocks = None
    if blocks is not None:
        gp_data = blocks.get(data_name)
        if gp_data is not None and gp_data.users == 0:
            try:
                blocks.remove(gp_data)
            except Exception:  # noqa: BLE001
                _logger.exception("remove GP data failed: %s", data_name)

    coll = bpy.data.collections.get(coll_name)
    if coll is not None:
        try:
            bpy.data.collections.remove(coll)
        except Exception:  # noqa: BLE001
            _logger.exception("remove page collection failed: %s", coll_name)


def get_page_collection(page_id: str):
    """既存の ``page_NNNN`` Collection を返す (無ければ None)."""
    return bpy.data.collections.get(page_collection_name(page_id))


def get_page_gpencil(page_id: str):
    """既存の ``page_NNNN_sketch`` GP オブジェクトを返す (無ければ None)."""
    return bpy.data.objects.get(page_gp_object_name(page_id))


# ---------- 見開き統合/解除用のリネーム・再リンクヘルパ ----------


def rename_gp_object_and_data(obj, new_obj_name: str, new_data_name: str | None = None) -> None:
    """GP Object と その data-block を安全に rename.

    Blender は衝突時に ``.001`` サフィックスを付けて別名で登録するため、
    事前に衝突チェックする。衝突がある場合は他方を先にリネームするなど
    呼出側で順序を調整すること。
    """
    if obj is None:
        return
    if obj.name != new_obj_name:
        obj.name = new_obj_name
    if new_data_name is not None and obj.data is not None and obj.data.name != new_data_name:
        obj.data.name = new_data_name


def rename_page_collection(old_id: str, new_id: str) -> object | None:
    """``page_<old_id>`` Collection を ``page_<new_id>`` に rename."""
    old_name = page_collection_name(old_id)
    new_name = page_collection_name(new_id)
    coll = bpy.data.collections.get(old_name)
    if coll is None:
        return None
    if coll.name != new_name:
        coll.name = new_name
    return coll


def relink_object_to_page(scene, obj, target_page_id: str) -> None:
    """``obj`` を ``page_<target_page_id>`` Collection のみリンクし直す.

    target Collection が無ければ生成。既に他の Collection にリンクされて
    いれば unlink。見開き統合/解除で GP を別ページ Collection へ移すときに
    使う。
    """
    if obj is None:
        return
    target = ensure_page_collection(scene, target_page_id)
    _relink_object_to_collection_only(scene, obj, target)


def add_stroke_to_drawing(
    drawing,
    points_xyz: Iterable[tuple[float, float, float]],
    radius: float = 0.01,
    radii: Iterable[float] | None = None,
    cyclic: bool = False,
    material_index: int | None = None,
) -> bool:
    """GreasePencilDrawing に 1 ストロークを追加.

    Blender 5.x 系の API では ``drawing.add_strokes([n_points])`` で新規
    ストロークを作り、attribute API で ``position`` / ``radius`` を書き込む。
    動作しないバージョンでは False を返す。
    """
    pts = list(points_xyz)
    if not pts:
        return False
    point_radii = list(radii or [])
    try:
        start_index = len(getattr(drawing, "strokes", []))
        strokes = drawing.add_strokes([len(pts)])
        if strokes is None:
            stroke = drawing.strokes[start_index]
        else:
            stroke = strokes[0]
        stroke.cyclic = cyclic
        if material_index is not None:
            try:
                mat_index = int(material_index)
                if mat_index >= 0:
                    stroke.material_index = mat_index
            except Exception:  # noqa: BLE001
                pass
        if hasattr(stroke, "points") and len(stroke.points) >= len(pts):
            for i, (x, y, z) in enumerate(pts):
                point = stroke.points[i]
                point.position = (x, y, z)
                if hasattr(point, "radius"):
                    point.radius = point_radii[i] if i < len(point_radii) else radius
            return True
        pos_attr = drawing.attributes.get("position")
        if pos_attr is None:
            return False
        offset = getattr(stroke.points, "offset", 0)
        for i, (x, y, z) in enumerate(pts):
            pos_attr.data[offset + i].vector = (x, y, z)
        rad_attr = drawing.attributes.get("radius")
        if rad_attr is not None:
            for i in range(len(pts)):
                rad_attr.data[offset + i].value = point_radii[i] if i < len(point_radii) else radius
        if material_index is not None:
            try:
                mat_index = int(material_index)
                if mat_index >= 0:
                    stroke.material_index = mat_index
            except Exception:  # noqa: BLE001
                pass
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("add_stroke_to_drawing failed: %s", exc)
        return False


def ensure_active_frame(layer, frame_number: int | None = None):
    """指定フレームに GreasePencilFrame を取得/生成.

    frame_number=None なら現在のシーンフレーム。
    """
    if frame_number is None:
        frame_number = bpy.context.scene.frame_current
    # v3 は layer.frames リストで管理。既存があれば再利用。
    for frame in layer.frames:
        if frame.frame_number == frame_number:
            return frame
    try:
        return layer.frames.new(frame_number=frame_number)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("frame.new failed: %s", exc)
        return None
