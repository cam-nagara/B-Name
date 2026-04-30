"""画像レイヤー用 image plane Object ヘルパ (Phase 3b).

計画書 ``docs/outliner_object_layer_plan_2026-04-30.md`` Phase 3 のうち
「image レイヤーを plane Object 化する」部分を実装。

設計方針 (二重描画回避):
    既存の GPU overlay (``ui/overlay_image.py``) は引き続き描画を担当する。
    Phase 3b では image plane Object を **データとして存在させる** が、
    視覚的にはまだ overlay 任せにする。Object 側の material を不可視
    (alpha=0) にして二重描画を避ける。

    Phase 3c 以降で「Object material を可視にして overlay を非表示」へ
    段階的に切り替える想定。Outliner 上の D&D / 階層管理は Phase 3b の
    時点で機能する (visible とは独立)。

主要 API:
    - ``ensure_image_plane_object(scene, entry, page)``: entry に対応する
      image plane Object を生成・更新し、Outliner Collection に link 同期。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import layer_object_sync as los
from . import log
from . import object_naming as on
from .geom import mm_to_m

_logger = log.get_logger(__name__)

IMAGE_PLANE_NAME_PREFIX = "image_plane_"
IMAGE_MESH_NAME_PREFIX = "image_mesh_"
IMAGE_MATERIAL_NAME_PREFIX = "image_mat_"

# ノード名 (重複生成防止 / 検索用)
NODE_IMAGE_TEXTURE = "BName Image Texture"
NODE_PRINCIPLED = "BName Image Principled"
NODE_OUTPUT = "BName Image Output"
NODE_TRANSPARENT = "BName Image Transparent"
NODE_MIX = "BName Image Mix"

# Object が overlay と二重描画しないよう、Phase 3b ではマテリアル alpha を
# 既定 0 にして「データだけ存在し画面に出ない」状態にする。
PROP_VISIBLE_VIA_OBJECT = "bname_image_visible_via_object"


def image_plane_name(image_id: str) -> str:
    return f"{IMAGE_PLANE_NAME_PREFIX}{image_id}"


def image_mesh_name(image_id: str) -> str:
    return f"{IMAGE_MESH_NAME_PREFIX}{image_id}"


def image_material_name(image_id: str) -> str:
    return f"{IMAGE_MATERIAL_NAME_PREFIX}{image_id}"


def _ensure_image_datablock(filepath: str, image_id: str) -> Optional[bpy.types.Image]:
    """Image data block を ensure。filepath が空なら空 image (1x1) を生成.

    既存 Image との filepath 比較は ``bpy.path.abspath`` で正規化してから
    行う。Blender が自動的に ``//`` 相対パスへ変換する場合があるため、
    生の文字列比較では毎回 reload してしまい paint 中の未保存内容が
    破壊される事故が起きる。
    """
    name = f"BNameImage_{image_id}"
    img = bpy.data.images.get(name)
    if img is not None:
        if filepath:
            try:
                cur_abs = bpy.path.abspath(img.filepath) if img.filepath else ""
                new_abs = bpy.path.abspath(filepath)
                if cur_abs != new_abs:
                    img.filepath = filepath
                    img.reload()
            except Exception:  # noqa: BLE001
                pass
        return img
    if filepath:
        try:
            img = bpy.data.images.load(filepath, check_existing=True)
            img.name = name
            return img
        except Exception:  # noqa: BLE001
            _logger.exception("image load failed: %s", filepath)
    # 1x1 透明 placeholder
    try:
        img = bpy.data.images.new(name=name, width=1, height=1, alpha=True)
        img.pixels = [0.0, 0.0, 0.0, 0.0]
        return img
    except Exception:  # noqa: BLE001
        _logger.exception("image placeholder create failed")
        return None


def _ensure_image_material(image_id: str, image: bpy.types.Image) -> bpy.types.Material:
    """Image plane 用の最小マテリアルを ensure.

    既存ノードがある場合は **再利用** し、Image だけ最新に差し替える。
    ノードが揃っていない場合のみ全構築する。これにより Phase 3c で
    ユーザーが Mix Shader Fac=1 (可視) に切替えた状態が、entry の再 ensure
    で 0 にリセットされない。
    """
    name = image_material_name(image_id)
    mat = bpy.data.materials.get(name)
    is_new = mat is None
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes = nt.nodes
    links = nt.links

    # 既存 B-Name 管理ノードを取得 (再利用)
    img_node = nodes.get(NODE_IMAGE_TEXTURE)
    transparent = nodes.get(NODE_TRANSPARENT)
    principled = nodes.get(NODE_PRINCIPLED)
    mix = nodes.get(NODE_MIX)
    output = nodes.get(NODE_OUTPUT)

    needs_rebuild = (
        img_node is None or transparent is None or principled is None
        or mix is None or output is None
    )

    if needs_rebuild:
        # 不完全な状態の B-Name ノードと、既定で生成された Principled/Output
        # をすべて取り除いてから再構築する。
        for n in list(nodes):
            if n.name in {NODE_IMAGE_TEXTURE, NODE_PRINCIPLED, NODE_TRANSPARENT, NODE_MIX, NODE_OUTPUT}:
                nodes.remove(n)
        for n in list(nodes):
            if n.bl_idname in {"ShaderNodeBsdfPrincipled", "ShaderNodeOutputMaterial"}:
                nodes.remove(n)

        img_node = nodes.new("ShaderNodeTexImage")
        img_node.name = NODE_IMAGE_TEXTURE
        img_node.location = (-400, 0)

        transparent = nodes.new("ShaderNodeBsdfTransparent")
        transparent.name = NODE_TRANSPARENT
        transparent.location = (-200, -200)

        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.name = NODE_PRINCIPLED
        principled.location = (-200, 0)

        mix = nodes.new("ShaderNodeMixShader")
        mix.name = NODE_MIX
        mix.location = (50, 0)
        # 新規生成時のみ Fac=0 (overlay と二重描画させない)。
        # 既存マテリアルを再利用する場合はユーザー設定を保持する。
        mix.inputs[0].default_value = 0.0

        output = nodes.new("ShaderNodeOutputMaterial")
        output.name = NODE_OUTPUT
        output.location = (250, 0)

        links.new(img_node.outputs["Color"], principled.inputs["Base Color"])
        links.new(transparent.outputs["BSDF"], mix.inputs[1])
        links.new(principled.outputs["BSDF"], mix.inputs[2])
        links.new(mix.outputs["Shader"], output.inputs["Surface"])

    # Image は毎回最新値に差し替える (filepath 変更等を反映)。
    if img_node.image is not image:
        img_node.image = image

    # blend_method は Blender 5.x の EEVEE Next でも有効。shadow_method は
    # 5.x で削除されたため設定しない。
    try:
        mat.blend_method = "BLEND"
    except (AttributeError, TypeError):
        pass
    return mat


_MESH_DIM_PROP = "bname_image_plane_dims"  # Mesh に保存する (w_m, h_m)


def _ensure_image_mesh(image_id: str, width_m: float, height_m: float) -> bpy.types.Mesh:
    """plane Mesh (4 頂点) を ensure。サイズ変化があれば再構築する.

    既存 Mesh が既に同じサイズで生成されているなら **頂点を破壊せず再利用**
    する (ユーザーの Edit Mode 編集 / Vertex Group / Shape Key を保持)。
    Blender の浮動小数誤差は 1e-7 で十分。
    """
    name = image_mesh_name(image_id)
    mesh = bpy.data.meshes.get(name)
    if mesh is None:
        mesh = bpy.data.meshes.new(name)

    # サイズ不変なら触らない
    cached = mesh.get(_MESH_DIM_PROP)
    if cached is not None:
        try:
            cw, ch = float(cached[0]), float(cached[1])
            if abs(cw - width_m) < 1e-7 and abs(ch - height_m) < 1e-7 and len(mesh.vertices) == 4:
                return mesh
        except Exception:  # noqa: BLE001
            pass

    # サイズ変更あり: 再構築
    mesh.clear_geometry()
    verts = [
        (0.0, 0.0, 0.0),
        (width_m, 0.0, 0.0),
        (width_m, height_m, 0.0),
        (0.0, height_m, 0.0),
    ]
    faces = [(0, 1, 2, 3)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    if mesh.uv_layers:
        uv = mesh.uv_layers.active
    else:
        uv = mesh.uv_layers.new(name="UVMap")
    coords = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    for i, loop in enumerate(mesh.loops):
        uv.data[loop.index].uv = coords[i]
    mesh[_MESH_DIM_PROP] = (width_m, height_m)
    return mesh


def ensure_image_plane_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    """``BNameImageLayer`` entry に対応する image plane Object を生成・更新する.

    既存 GPU overlay は引き続き描画責務を持つ。Object の material は
    ``Mix Shader`` の Fac=0 (完全 transparent 側) で初期化され、視覚は
    overlay に任せる。Outliner D&D / 階層管理のみ Phase 3b 範囲。
    """
    if scene is None or entry is None:
        return None
    image_id = str(getattr(entry, "id", "") or "")
    if not image_id:
        return None

    # 1. Image data block
    filepath = str(getattr(entry, "filepath", "") or "")
    img = _ensure_image_datablock(filepath, image_id)
    if img is None:
        return None

    # 2. plane Mesh (size mm → m)
    width_m = mm_to_m(float(getattr(entry, "width_mm", 100.0) or 100.0))
    height_m = mm_to_m(float(getattr(entry, "height_mm", 100.0) or 100.0))
    mesh = _ensure_image_mesh(image_id, width_m, height_m)

    # 3. material
    mat = _ensure_image_material(image_id, img)

    # 4. Object 生成 or 再利用
    obj_name = image_plane_name(image_id)
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    else:
        obj.data = mesh
    # material slot
    if not obj.data.materials:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat

    # 5. 位置 (mm → m, ページローカル座標)
    x_m = mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0))
    y_m = mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0))
    obj.location.x = x_m
    obj.location.y = y_m
    # z は stamp_layer_object 内で z_index から計算される

    # 6. Phase 3b: 既定 invisible 表示扱い (overlay と二重描画させない)。
    # 既にプロパティが立っているなら値を保持 (Phase 3c でユーザーが切替えた
    # 設定を ensure 再実行で潰さないため)。
    if PROP_VISIBLE_VIA_OBJECT not in obj.keys():
        obj[PROP_VISIBLE_VIA_OBJECT] = False

    # 7. parent kind/key の解決 (BNameImageLayer の parent_kind は文字列)
    entry_parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    entry_parent_key = str(getattr(entry, "parent_key", "") or "")
    entry_folder_id = folder_id or str(getattr(entry, "folder_key", "") or "")

    if entry_parent_kind in {"none", "outside"}:
        stamp_kind = "outside"
        stamp_key = ""
    elif entry_parent_kind == "coma" and entry_parent_key:
        stamp_kind = "coma"
        stamp_key = entry_parent_key
    elif entry_parent_kind == "folder" and entry_folder_id:
        stamp_kind = "folder"
        stamp_key = entry_folder_id
    else:
        stamp_kind = "page"
        stamp_key = entry_parent_key or str(getattr(page, "id", "") or "")

    # 8. z_index は image_layers 配列 index ベースで採番 (raster と同方式)
    z_index = 0
    coll = getattr(scene, "bname_image_layers", None)
    if coll is not None:
        for i, e in enumerate(coll):
            if str(getattr(e, "id", "") or "") == image_id:
                z_index = (i + 1) * 10
                break

    # 9. stamp + Outliner Collection link
    los.stamp_layer_object(
        obj,
        kind="image",
        bname_id=image_id,
        title=str(getattr(entry, "title", "") or image_id),
        z_index=z_index,
        parent_kind=stamp_kind,
        parent_key=stamp_key,
        folder_id=entry_folder_id,
        scene=scene,
    )

    # 10. visible / locked を Object フラグに反映 (data は可視性独立)
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))

    return obj
