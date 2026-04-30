"""テキスト Plane Object ヘルパ (Phase 4c).

`BNameTextEntry` から透過画像を貼った Plane Object を生成する。
typography の組版を Pillow Image に描画して Blender Image に転写し、
Plane の Image Texture material に貼り付ける。

Pillow が無い環境では placeholder (1x1 透明) のままにフォールバック。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import layer_object_sync as los
from . import log
from . import object_naming as on
from .geom import mm_to_m

_logger = log.get_logger(__name__)

TEXT_PLANE_NAME_PREFIX = "text_plane_"
TEXT_MESH_NAME_PREFIX = "text_mesh_"
TEXT_MAT_NAME_PREFIX = "text_mat_"
TEXT_IMAGE_NAME_PREFIX = "BNameText_"

NODE_IMAGE_TEXTURE = "BName Text Texture"
NODE_PRINCIPLED = "BName Text Principled"
NODE_TRANSPARENT = "BName Text Transparent"
NODE_MIX = "BName Text Mix"
NODE_OUTPUT = "BName Text Output"


def _ensure_text_image(image_name: str, w_px: int, h_px: int):
    """Pillow で透過テキスト画像を作って Blender Image に転写.

    Pillow が無ければ placeholder (1x1 透明) を返す。
    """
    img = bpy.data.images.get(image_name)
    if img is None:
        try:
            img = bpy.data.images.new(
                name=image_name, width=max(1, w_px), height=max(1, h_px), alpha=True
            )
        except Exception:  # noqa: BLE001
            _logger.exception("text image create failed")
            return None
    return img


def _render_typography_to_image(entry, image, w_px: int, h_px: int) -> bool:
    """entry.body を typography で組版し、Pillow 経由で image にピクセルを
    転写する。Pillow が無ければ False を返す。
    """
    if image is None:
        return False
    try:
        from ..typography import export_renderer, layout as type_layout
        if not export_renderer.has_pillow():
            return False
        from PIL import Image as PILImage
    except Exception:  # noqa: BLE001
        return False

    body = str(getattr(entry, "body", "") or "")
    if not body:
        return False
    # 組版設定 (簡易: フォントは entry 由来 or デフォルト)
    width_mm = float(getattr(entry, "width_mm", 30.0) or 30.0)
    height_mm = float(getattr(entry, "height_mm", 15.0) or 15.0)
    font_size_pt = float(getattr(entry, "font_size_pt", 9.0) or 9.0)
    px_per_mm = w_px / max(0.1, width_mm)
    try:
        result = type_layout.typeset(
            body=body,
            width_mm=width_mm,
            height_mm=height_mm,
            font_size_pt=font_size_pt,
            vertical=bool(getattr(entry, "vertical", True)),
        )
    except Exception:  # noqa: BLE001
        return False

    pil_img = PILImage.new("RGBA", (w_px, h_px), (0, 0, 0, 0))
    try:
        font_path = str(getattr(entry, "font_path", "") or "")
        export_renderer.render_to_image(
            result,
            pil_img,
            font_path=font_path,
            px_per_mm=px_per_mm,
            color=(0, 0, 0, 255),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("typography render failed")
        return False

    # Pillow → Blender Image 転写 (上下反転 + 0..1 float)
    try:
        flipped = pil_img.transpose(PILImage.FLIP_TOP_BOTTOM)
        pixels = list(flipped.getdata())  # [(r,g,b,a), ...]
        flat = []
        for px in pixels:
            flat.extend((px[0] / 255.0, px[1] / 255.0, px[2] / 255.0, px[3] / 255.0))
        if image.size[0] != w_px or image.size[1] != h_px:
            image.scale(w_px, h_px)
        image.pixels = flat
        image.update()
    except Exception:  # noqa: BLE001
        _logger.exception("text image pixel transfer failed")
        return False
    return True


def _ensure_text_mesh(mesh_name: str, width_m: float, height_m: float) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    cached_w = mesh.get("bname_text_w")
    cached_h = mesh.get("bname_text_h")
    if (
        cached_w is not None
        and cached_h is not None
        and abs(float(cached_w) - width_m) < 1e-7
        and abs(float(cached_h) - height_m) < 1e-7
        and len(mesh.vertices) == 4
    ):
        return mesh
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
    mesh["bname_text_w"] = width_m
    mesh["bname_text_h"] = height_m
    return mesh


def _ensure_text_material(name: str, image: Optional[bpy.types.Image]) -> bpy.types.Material:
    """テキスト plane 用マテリアル. Image Texture + Transparent + Mix 構成."""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes = nt.nodes
    links = nt.links

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
        # Mix Fac には Image alpha を接続 (テキストの透過部分は Transparent 側)
        output = nodes.new("ShaderNodeOutputMaterial")
        output.name = NODE_OUTPUT
        output.location = (250, 0)
        links.new(img_node.outputs["Color"], principled.inputs["Base Color"])
        links.new(img_node.outputs["Alpha"], mix.inputs[0])  # alpha で Mix
        links.new(transparent.outputs["BSDF"], mix.inputs[1])
        links.new(principled.outputs["BSDF"], mix.inputs[2])
        links.new(mix.outputs["Shader"], output.inputs["Surface"])
    if image is not None and img_node.image is not image:
        img_node.image = image
    try:
        mat.blend_method = "BLEND"
    except (AttributeError, TypeError):
        pass
    return mat


def ensure_text_plane_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    if scene is None or entry is None or page is None:
        return None
    text_id = str(getattr(entry, "id", "") or "")
    if not text_id:
        return None

    width_mm = float(getattr(entry, "width_mm", 30.0) or 30.0)
    height_mm = float(getattr(entry, "height_mm", 15.0) or 15.0)
    width_m = mm_to_m(width_mm)
    height_m = mm_to_m(height_mm)

    # 解像度 (mm × 8 px/mm = 200 DPI 相当の控えめ解像度)
    px_per_mm = 8
    w_px = max(1, int(width_mm * px_per_mm))
    h_px = max(1, int(height_mm * px_per_mm))

    image_name = f"{TEXT_IMAGE_NAME_PREFIX}{text_id}"
    image = _ensure_text_image(image_name, w_px, h_px)
    if image is not None:
        # typography で実テキスト画像を生成 (Pillow 不在時は placeholder のまま)
        if not _render_typography_to_image(entry, image, w_px, h_px):
            # placeholder: 透明
            try:
                image.pixels = [0.0] * (w_px * h_px * 4)
            except Exception:  # noqa: BLE001
                pass

    mesh = _ensure_text_mesh(f"{TEXT_MESH_NAME_PREFIX}{text_id}", width_m, height_m)
    mat = _ensure_text_material(f"{TEXT_MAT_NAME_PREFIX}{text_id}", image)

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

    # parent 解決
    entry_parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    entry_parent_key = str(getattr(entry, "parent_key", "") or "")
    entry_folder_id = folder_id or str(getattr(entry, "folder_key", "") or "")
    if entry_parent_kind in {"none", "outside"}:
        stamp_kind, stamp_key, stamp_folder = "outside", "", ""
    elif entry_parent_kind == "coma" and entry_parent_key:
        stamp_kind, stamp_key, stamp_folder = "coma", entry_parent_key, entry_folder_id
    elif entry_parent_kind == "folder" and entry_folder_id:
        stamp_kind, stamp_key, stamp_folder = "folder", entry_folder_id, entry_folder_id
    else:
        stamp_kind = "page"
        stamp_key = entry_parent_key or str(getattr(page, "id", "") or "")
        stamp_folder = entry_folder_id

    TEXT_Z_BASE = 2000
    z_index = TEXT_Z_BASE
    texts = getattr(page, "texts", None)
    if texts is not None:
        for i, e in enumerate(texts):
            if str(getattr(e, "id", "") or "") == text_id:
                z_index = TEXT_Z_BASE + (i + 1) * 10
                break

    los.stamp_layer_object(
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

    # マスク適用
    try:
        from . import mask_apply

        mask_apply.apply_mask_to_layer_object(obj)
    except Exception:  # noqa: BLE001
        pass
    return obj


def find_text_entry(scene, text_id: str):
    work = getattr(scene, "bname_work", None)
    if work is None:
        return None, None
    for page in getattr(work, "pages", []):
        for entry in getattr(page, "texts", []):
            if str(getattr(entry, "id", "") or "") == text_id:
                return page, entry
    return None, None
