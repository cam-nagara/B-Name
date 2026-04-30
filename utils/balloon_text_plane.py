"""フキダシ / テキスト用 plane Object ヘルパ (Phase 4).

計画書 ``docs/outliner_object_layer_plan_2026-04-30.md`` Phase 4 を実装。
Phase 3b の image_plane_object.py と同方針で「overlay は残置 + Object は
データとして並列生成 (二重描画回避のため material alpha=0)」とする。

balloon は将来 Mesh / Curve で実形状を生成する想定だが、Phase 4 では矩形
Mesh plane で型と階層管理だけ提供する。Phase 4c 以降で実形状 Mesh / Curve
へ置換する。

text は B-Name typography から生成した透過画像 plane を貼る想定。Phase 4
では placeholder (1x1 透明) を貼る。

主要 API:
    - ``ensure_balloon_object(scene, entry, page, folder_id)``
    - ``ensure_text_plane_object(scene, entry, page, folder_id)``
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import layer_object_sync as los
from . import log
from . import object_naming as on
from .geom import mm_to_m
from . import image_plane_object as ipo

_logger = log.get_logger(__name__)

# Phase 4 用の prefix (image_plane と区別)
BALLOON_PLANE_NAME_PREFIX = "balloon_plane_"
BALLOON_MESH_NAME_PREFIX = "balloon_mesh_"
BALLOON_MAT_NAME_PREFIX = "balloon_mat_"
TEXT_PLANE_NAME_PREFIX = "text_plane_"
TEXT_MESH_NAME_PREFIX = "text_mesh_"
TEXT_MAT_NAME_PREFIX = "text_mat_"

# 二重描画回避フラグ (Phase 4c で True 切替)
PROP_BALLOON_VISIBLE_VIA_OBJECT = "bname_balloon_visible_via_object"
PROP_TEXT_VISIBLE_VIA_OBJECT = "bname_text_visible_via_object"


def _resolve_parent_for_entry(
    entry, page, folder_id: str
) -> tuple[str, str, str]:
    """``entry.parent_kind / parent_key / folder_key`` を outliner_model 互換に変換.

    Returns:
        ``(stamp_kind, stamp_key, stamp_folder_id)``
    """
    entry_parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    entry_parent_key = str(getattr(entry, "parent_key", "") or "")
    entry_folder_id = folder_id or str(getattr(entry, "folder_key", "") or "")
    entry_id = str(getattr(entry, "id", "") or "")

    if entry_parent_kind in {"none", "outside"}:
        return "outside", "", ""
    if entry_parent_kind == "coma" and entry_parent_key:
        return "coma", entry_parent_key, entry_folder_id
    if entry_parent_kind == "folder":
        if entry_folder_id:
            return "folder", entry_folder_id, entry_folder_id
        # folder 指定なのに folder_id 空: 警告 + page fallback
        _logger.warning(
            "balloon/text entry %s: parent_kind=folder だが folder_id 空。page fallback",
            entry_id,
        )
    # page or fallback
    return "page", entry_parent_key or str(getattr(page, "id", "") or ""), entry_folder_id


def _ensure_simple_plane_material(
    name: str,
    image: Optional[bpy.types.Image] = None,
) -> bpy.types.Material:
    """balloon/text 共通の薄い material を ensure (image_plane と同方式)."""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes = nt.nodes
    links = nt.links

    img_node = nodes.get(ipo.NODE_IMAGE_TEXTURE)
    transparent = nodes.get(ipo.NODE_TRANSPARENT)
    principled = nodes.get(ipo.NODE_PRINCIPLED)
    mix = nodes.get(ipo.NODE_MIX)
    output = nodes.get(ipo.NODE_OUTPUT)

    needs_rebuild = (
        img_node is None or transparent is None or principled is None
        or mix is None or output is None
    )

    if needs_rebuild:
        for n in list(nodes):
            if n.name in {
                ipo.NODE_IMAGE_TEXTURE, ipo.NODE_PRINCIPLED,
                ipo.NODE_TRANSPARENT, ipo.NODE_MIX, ipo.NODE_OUTPUT,
            }:
                nodes.remove(n)
        for n in list(nodes):
            if n.bl_idname in {"ShaderNodeBsdfPrincipled", "ShaderNodeOutputMaterial"}:
                nodes.remove(n)

        img_node = nodes.new("ShaderNodeTexImage")
        img_node.name = ipo.NODE_IMAGE_TEXTURE
        img_node.location = (-400, 0)

        transparent = nodes.new("ShaderNodeBsdfTransparent")
        transparent.name = ipo.NODE_TRANSPARENT
        transparent.location = (-200, -200)

        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.name = ipo.NODE_PRINCIPLED
        principled.location = (-200, 0)

        mix = nodes.new("ShaderNodeMixShader")
        mix.name = ipo.NODE_MIX
        mix.location = (50, 0)
        mix.inputs[0].default_value = 0.0  # 完全透明スタート

        output = nodes.new("ShaderNodeOutputMaterial")
        output.name = ipo.NODE_OUTPUT
        output.location = (250, 0)

        links.new(img_node.outputs["Color"], principled.inputs["Base Color"])
        links.new(transparent.outputs["BSDF"], mix.inputs[1])
        links.new(principled.outputs["BSDF"], mix.inputs[2])
        links.new(mix.outputs["Shader"], output.inputs["Surface"])

    # Image を最新に差し替える (None なら ImageTexture node に何も繋がない)
    if image is not None and img_node.image is not image:
        img_node.image = image

    try:
        mat.blend_method = "BLEND"
    except (AttributeError, TypeError):
        pass
    return mat


def _ensure_placeholder_image(name: str) -> Optional[bpy.types.Image]:
    img = bpy.data.images.get(name)
    if img is not None:
        return img
    try:
        img = bpy.data.images.new(name=name, width=1, height=1, alpha=True)
        img.pixels = [0.0, 0.0, 0.0, 0.0]
        return img
    except Exception:  # noqa: BLE001
        _logger.exception("placeholder image create failed: %s", name)
        return None


def _ensure_simple_mesh(name: str, width_m: float, height_m: float) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.get(name)
    if mesh is None:
        mesh = bpy.data.meshes.new(name)
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
    return mesh


# kind 別 z_index ベースオフセット (Outliner alpha sort で
# raster < image < balloon < text < effect の順に並ぶよう base を分ける)
BALLOON_Z_BASE = 1000
TEXT_Z_BASE = 2000


def _balloon_z_index_for_entry(page, entry_id: str) -> int:
    """page.balloons 配列 index に基づき BALLOON_Z_BASE + i*10 を返す."""
    balloons = getattr(page, "balloons", None)
    if balloons is None:
        return BALLOON_Z_BASE
    for i, e in enumerate(balloons):
        if str(getattr(e, "id", "") or "") == entry_id:
            return BALLOON_Z_BASE + (i + 1) * 10
    return BALLOON_Z_BASE


def _text_z_index_for_entry(page, entry_id: str) -> int:
    texts = getattr(page, "texts", None)
    if texts is None:
        return TEXT_Z_BASE
    for i, e in enumerate(texts):
        if str(getattr(e, "id", "") or "") == entry_id:
            return TEXT_Z_BASE + (i + 1) * 10
    return TEXT_Z_BASE


def ensure_balloon_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    """``BNameBalloonEntry`` に対応する balloon plane Object を ensure."""
    if scene is None or entry is None or page is None:
        return None
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None

    width_m = mm_to_m(float(getattr(entry, "width_mm", 40.0) or 40.0))
    height_m = mm_to_m(float(getattr(entry, "height_mm", 20.0) or 20.0))
    mesh = _ensure_simple_mesh(
        f"{BALLOON_MESH_NAME_PREFIX}{balloon_id}", width_m, height_m
    )

    img = _ensure_placeholder_image(f"BNameBalloon_placeholder_{balloon_id}")
    mat = _ensure_simple_plane_material(
        f"{BALLOON_MAT_NAME_PREFIX}{balloon_id}", image=img
    )

    obj_name = f"{BALLOON_PLANE_NAME_PREFIX}{balloon_id}"
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    else:
        obj.data = mesh
    if not obj.data.materials:
        obj.data.materials.append(mat)
    elif obj.data.materials[0] is not mat:
        obj.data.materials[0] = mat

    # 位置 (mm → m, ページローカル座標)
    obj.location.x = mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0))
    obj.location.y = mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0))

    if PROP_BALLOON_VISIBLE_VIA_OBJECT not in obj.keys():
        obj[PROP_BALLOON_VISIBLE_VIA_OBJECT] = False

    stamp_kind, stamp_key, stamp_folder = _resolve_parent_for_entry(
        entry, page, folder_id
    )
    los.stamp_layer_object(
        obj,
        kind="balloon",
        bname_id=balloon_id,
        title=str(getattr(entry, "title", "") or balloon_id),
        z_index=_balloon_z_index_for_entry(page, balloon_id),
        parent_kind=stamp_kind,
        parent_key=stamp_key,
        folder_id=stamp_folder,
        scene=scene,
    )
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    return obj


def ensure_text_plane_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    """``BNameTextEntry`` に対応する text plane Object を ensure.

    Phase 4 では typography 画像生成は行わず、placeholder 画像 (1x1 透明)
    を貼って Outliner 階層に登録する。Phase 4c 以降で typography の
    透過画像を貼り、overlay 非表示と切替える。
    """
    if scene is None or entry is None or page is None:
        return None
    text_id = str(getattr(entry, "id", "") or "")
    if not text_id:
        return None

    width_m = mm_to_m(float(getattr(entry, "width_mm", 30.0) or 30.0))
    height_m = mm_to_m(float(getattr(entry, "height_mm", 15.0) or 15.0))
    mesh = _ensure_simple_mesh(
        f"{TEXT_MESH_NAME_PREFIX}{text_id}", width_m, height_m
    )

    img = _ensure_placeholder_image(f"BNameText_placeholder_{text_id}")
    mat = _ensure_simple_plane_material(
        f"{TEXT_MAT_NAME_PREFIX}{text_id}", image=img
    )

    obj_name = f"{TEXT_PLANE_NAME_PREFIX}{text_id}"
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    else:
        obj.data = mesh
    if not obj.data.materials:
        obj.data.materials.append(mat)
    elif obj.data.materials[0] is not mat:
        obj.data.materials[0] = mat

    obj.location.x = mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0))
    obj.location.y = mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0))

    if PROP_TEXT_VISIBLE_VIA_OBJECT not in obj.keys():
        obj[PROP_TEXT_VISIBLE_VIA_OBJECT] = False

    stamp_kind, stamp_key, stamp_folder = _resolve_parent_for_entry(
        entry, page, folder_id
    )
    los.stamp_layer_object(
        obj,
        kind="text",
        bname_id=text_id,
        title=str(getattr(entry, "body", "") or text_id)[:40],
        z_index=_text_z_index_for_entry(page, text_id),
        parent_kind=stamp_kind,
        parent_key=stamp_key,
        folder_id=stamp_folder,
        scene=scene,
    )
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    return obj


def find_balloon_entry(scene, balloon_id: str):
    """全 page の balloons から id で逆引き."""
    work = getattr(scene, "bname_work", None)
    if work is None:
        return None, None
    for page in getattr(work, "pages", []):
        for entry in getattr(page, "balloons", []):
            if str(getattr(entry, "id", "") or "") == balloon_id:
                return page, entry
    return None, None


def find_text_entry(scene, text_id: str):
    work = getattr(scene, "bname_work", None)
    if work is None:
        return None, None
    for page in getattr(work, "pages", []):
        for entry in getattr(page, "texts", []):
            if str(getattr(entry, "id", "") or "") == text_id:
                return page, entry
    return None, None
