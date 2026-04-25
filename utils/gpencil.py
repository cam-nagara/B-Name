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


# ---------- 既定マテリアル (ブラック描画色) ----------

_DEFAULT_STROKE_MAT_NAME = "BName_Pen_Black"


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


# ---------- 紙メッシュ (用紙の白い実体) ----------

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


def page_paper_object_name(page_id: str) -> str:
    return f"page_{page_id}_paper"


def page_paper_mesh_name(page_id: str) -> str:
    return f"page_{page_id}_paper_data"


def ensure_page_paper(scene, page_id: str, canvas_width_mm: float, canvas_height_mm: float):
    """ページ用紙メッシュ (Plane) をページ Collection に生成/更新.

    GPU overlay で紙塗りすると POST_VIEW で必ず GP の上に乗ってしまうため、
    ジオメトリパスで描画される実メッシュ Plane を z=0 に置き、GP は z>0 に
    持ち上げることで Z 順を制御する (page_grid.GP_Z_LIFT_M)。

    既存メッシュがあればサイズだけ更新する。
    """
    from .geom import mm_to_m

    coll = ensure_page_collection(scene, page_id)
    mesh_name = page_paper_mesh_name(page_id)
    obj_name = page_paper_object_name(page_id)

    w = mm_to_m(float(canvas_width_mm))
    h = mm_to_m(float(canvas_height_mm))
    verts = [(0.0, 0.0, 0.0), (w, 0.0, 0.0), (w, h, 0.0), (0.0, h, 0.0)]
    faces = [(0, 1, 2, 3)]

    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
    else:
        # サイズ更新 (canvas_width/height_mm が変わった場合)
        try:
            for i, v in enumerate(verts):
                mesh.vertices[i].co = v
            mesh.update()
        except Exception:  # noqa: BLE001
            pass

    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        try:
            obj.data = mesh
        except Exception:  # noqa: BLE001
            pass

    # マテリアル割当 (1 つだけ)
    mat = _ensure_paper_material()
    if len(obj.data.materials) == 0:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat

    # 編集を防ぐためのフラグ (誤って動かさないように)
    try:
        obj.hide_select = True
        obj.show_in_front = False
    except Exception:  # noqa: BLE001
        pass

    _relink_object_to_collection_only(scene, obj, coll)
    return obj


def get_page_paper(page_id: str):
    return bpy.data.objects.get(page_paper_object_name(page_id))


def ensure_page_gpencil(scene, page_id: str, layer_name: str = "ネーム"):
    """ページ GP オブジェクト + 既定レイヤー + 既定フレームを取得/生成して返す.

    - Object 名: ``page_NNNN_sketch``
    - Data 名: ``page_NNNN_sketch_data``
    - ページ Collection 配下にリンク (scene 直下には link しない)
    - デフォルトレイヤーにシーン現在フレーム (通常は 1) の GreasePencilFrame を
      自動生成。GP v3 では Draw モードで描画するにはレイヤーに 1 枚以上の
      フレームが必要 (Blender 5.x は空レイヤーに直接ドローできず
      「ドローするグリースペンシルフレームがありません」エラーになる)。
    """
    coll = ensure_page_collection(scene, page_id)
    # 用紙メッシュも併設 (GP より背面の白い実体)
    try:
        from ..core.work import get_work
        work = get_work(bpy.context)
        if work is not None:
            ensure_page_paper(
                scene, page_id,
                float(work.paper.canvas_width_mm),
                float(work.paper.canvas_height_mm),
            )
    except Exception:  # noqa: BLE001
        _logger.exception("ensure_page_gpencil: paper mesh setup failed for %s", page_id)
    obj_name = page_gp_object_name(page_id)
    data_name = page_gp_data_name(page_id)
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        gp_data = ensure_gpencil(data_name)
        obj = bpy.data.objects.new(obj_name, gp_data)
    # ページ Collection にリンク (他コレクションからは外す)
    _relink_object_to_collection_only(scene, obj, coll)
    # 既定レイヤー + 現在フレーム用の空フレームを確保
    layer = None
    if len(obj.data.layers) == 0:
        try:
            layer = ensure_layer(obj.data, layer_name)
        except Exception:  # noqa: BLE001
            _logger.exception("ensure_page_gpencil: default layer create failed")
    else:
        layer = getattr(obj.data.layers, "active", None) or obj.data.layers[0]
    if layer is not None and hasattr(layer, "frames"):
        if len(layer.frames) == 0:
            try:
                frame_num = scene.frame_current if scene is not None else 1
                ensure_active_frame(layer, frame_number=frame_num)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "ensure_page_gpencil: default frame create failed for %s",
                    page_id,
                )
    # 黒線ストロークマテリアルを確保 (白い線で描かれるのを防止)
    try:
        ensure_default_stroke_material(obj)
    except Exception:  # noqa: BLE001
        _logger.exception(
            "ensure_page_gpencil: default stroke material setup failed for %s",
            page_id,
        )
    return obj


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
    """ページ GP オブジェクト / データ / Collection / 紙メッシュを完全削除.

    データブロックは users=0 になった段階でクリーンアップ。
    紙メッシュ (page_NNNN_paper) と紙メッシュデータも併せて削除する
    (残すと .blend サイズが膨らみ、削除済みページのデータが幽霊として残る)。
    """
    obj_name = page_gp_object_name(page_id)
    data_name = page_gp_data_name(page_id)
    coll_name = page_collection_name(page_id)
    paper_obj_name = page_paper_object_name(page_id)
    paper_mesh_name = page_paper_mesh_name(page_id)

    # GP オブジェクト
    obj = bpy.data.objects.get(obj_name)
    if obj is not None:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.exception("remove GP object failed: %s", obj_name)

    # 紙オブジェクト (新設)
    paper_obj = bpy.data.objects.get(paper_obj_name)
    if paper_obj is not None:
        try:
            bpy.data.objects.remove(paper_obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.exception("remove paper object failed: %s", paper_obj_name)

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

    # 紙メッシュデータ (users=0 のときだけ削除して、共有マテリアルは温存)
    paper_mesh = bpy.data.meshes.get(paper_mesh_name)
    if paper_mesh is not None and paper_mesh.users == 0:
        try:
            bpy.data.meshes.remove(paper_mesh)
        except Exception:  # noqa: BLE001
            _logger.exception("remove paper mesh failed: %s", paper_mesh_name)

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
    cyclic: bool = False,
) -> bool:
    """GreasePencilDrawing に 1 ストロークを追加.

    Blender 5.x 系の API では ``drawing.add_strokes([n_points])`` で新規
    ストロークを作り、attribute API で ``position`` / ``radius`` を書き込む。
    動作しないバージョンでは False を返す。
    """
    pts = list(points_xyz)
    if not pts:
        return False
    try:
        strokes = drawing.add_strokes([len(pts)])
        stroke = strokes[0]
        stroke.cyclic = cyclic
        pos_attr = drawing.attributes.get("position")
        if pos_attr is None:
            return False
        offset = stroke.points.offset
        for i, (x, y, z) in enumerate(pts):
            pos_attr.data[offset + i].vector = (x, y, z)
        rad_attr = drawing.attributes.get("radius")
        if rad_attr is not None:
            for i in range(len(pts)):
                rad_attr.data[offset + i].value = radius
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
