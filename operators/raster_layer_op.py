"""ラスター描画レイヤーの作成・Texture Paint 連携."""

from __future__ import annotations

import shutil
import uuid
from array import array
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..utils import layer_stack as layer_stack_utils
from ..utils import log, paths
from ..utils.geom import mm_to_m, mm_to_px

_logger = log.get_logger(__name__)

# ラスター plane を他レイヤー (paper / mask) より手前に確実に表示するための
# Z リフト (m)。0.005 (5mm) では近すぎたため 0.1 刻み (100mm) に拡大。
# レイヤー間の Z 差を明確化し、 EEVEE Next の z-fight や alpha 競合を回避する。
RASTER_Z_LIFT_M = 0.1
RASTER_MATERIAL_VERSION = 2
RASTER_MATERIAL_VERSION_PROP = "bname_raster_material_version"
RASTER_IMAGE_NODE = "BName Raster Image"
RASTER_EMISSION_NODE = "BName Raster Emission"
RASTER_TRANSPARENT_NODE = "BName Raster Transparent"
RASTER_ALPHA_SCALE_NODE = "BName Raster Alpha Scale"
RASTER_ALPHA_MULTIPLY_NODE = "BName Raster Alpha Multiply"
RASTER_MIX_NODE = "BName Raster Mix"
RASTER_OUTPUT_NODE = "BName Raster Output"
RASTER_BRUSH_INITIALIZED_PROP = "bname_raster_brush_initialized"


def raster_image_name(raster_id: str) -> str:
    return f"raster_{raster_id}"


def raster_plane_name(raster_id: str) -> str:
    return f"raster_plane_{raster_id}"


def raster_mesh_name(raster_id: str) -> str:
    return f"raster_mesh_{raster_id}"


def raster_material_name(raster_id: str) -> str:
    return f"raster_mat_{raster_id}"


def raster_filepath_rel(raster_id: str) -> str:
    return f"{paths.RASTER_DIR_NAME}/{raster_id}.png"


def _raster_collection(scene):
    return getattr(scene, "bname_raster_layers", None)


def find_raster_entry(scene, raster_id: str):
    coll = _raster_collection(scene)
    if coll is None:
        return None, -1
    for i, entry in enumerate(coll):
        if getattr(entry, "id", "") == raster_id:
            return entry, i
    return None, -1


def active_raster_entry(context):
    scene = getattr(context, "scene", None)
    if scene is None:
        return None, -1
    coll = _raster_collection(scene)
    idx = int(getattr(scene, "bname_active_raster_layer_index", -1))
    if coll is None or not (0 <= idx < len(coll)):
        return None, -1
    return coll[idx], idx


def _allocate_raster_id(scene, work_dir: Path) -> str:
    coll = _raster_collection(scene)
    used = {getattr(entry, "id", "") for entry in (coll or [])}
    for _ in range(128):
        candidate = uuid.uuid4().hex[:12]
        if candidate not in used and not paths.raster_png_path(work_dir, candidate).exists():
            return candidate
    raise RuntimeError("ラスターIDを採番できません")


def _raster_size_px(work, dpi: int) -> tuple[int, int]:
    paper = work.paper
    return (
        max(1, int(round(mm_to_px(float(paper.canvas_width_mm), dpi)))),
        max(1, int(round(mm_to_px(float(paper.canvas_height_mm), dpi)))),
    )


def _abs_png_path(work_dir: Path, entry) -> Path:
    raster_id = str(getattr(entry, "id", "") or "")
    rel = str(getattr(entry, "filepath_rel", "") or raster_filepath_rel(raster_id))
    return Path(work_dir) / rel


def _set_image_relative_path(image, raster_id: str, abs_path: Path) -> None:
    try:
        image.file_format = "PNG"
        image.filepath_raw = str(abs_path)
        blend_path = Path(str(getattr(bpy.data, "filepath", "") or ""))
        work_dir = abs_path.parent.parent
        if blend_path.name == paths.WORK_BLEND_NAME and blend_path.parent.resolve() == work_dir.resolve():
            image.filepath = f"//{paths.RASTER_DIR_NAME}/{raster_id}.png"
        else:
            image.filepath = str(abs_path)
    except Exception:  # noqa: BLE001
        _logger.exception("raster image filepath setup failed: %s", raster_id)


def _image_path_is_current(image, abs_path: Path) -> bool:
    for attr in ("filepath_raw", "filepath"):
        raw = str(getattr(image, attr, "") or "")
        if not raw:
            continue
        try:
            current = Path(bpy.path.abspath(raw)).resolve()
        except Exception:  # noqa: BLE001
            continue
        try:
            if current == abs_path.resolve():
                return True
        except Exception:  # noqa: BLE001
            if str(current) == str(abs_path):
                return True
    return False


def _entry_has_unsaved_pixels(entry, image) -> bool:
    try:
        if bool(entry.get("bname_raster_dirty", False)):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(getattr(image, "is_dirty", False))
    except Exception:  # noqa: BLE001
        return False


def ensure_raster_image(context, entry, *, create_missing: bool = True, mark_missing: bool = False):
    work = get_work(context)
    if work is None or not getattr(work, "work_dir", ""):
        return None
    work_dir = Path(work.work_dir)
    raster_id = str(getattr(entry, "id", "") or "")
    if not raster_id:
        return None
    name = str(getattr(entry, "image_name", "") or raster_image_name(raster_id))
    abs_path = _abs_png_path(work_dir, entry)
    image = bpy.data.images.get(name)
    if image is not None:
        if not _image_path_is_current(image, abs_path) and not _entry_has_unsaved_pixels(entry, image):
            _set_image_relative_path(image, raster_id, abs_path)
        return image
    if abs_path.is_file():
        try:
            image = bpy.data.images.load(str(abs_path), check_existing=True)
            image.name = name
            _set_image_relative_path(image, raster_id, abs_path)
            return image
        except Exception:  # noqa: BLE001
            _logger.exception("raster image load failed: %s", abs_path)
    if not create_missing:
        return None
    width, height = _raster_size_px(work, int(getattr(entry, "dpi", 300)))
    image = bpy.data.images.new(name, width=width, height=height, alpha=True, float_buffer=False)
    try:
        image.generated_color = (0.0, 0.0, 0.0, 0.0)
    except Exception:  # noqa: BLE001
        _logger.exception("raster image transparent initialization failed: %s", name)
    try:
        image.colorspace_settings.name = "Non-Color"
    except Exception:  # noqa: BLE001
        pass
    if (
        mark_missing
        and abs_path.exists() is False
        and getattr(entry, "title", "")
        and abs_path.parent.exists()
    ):
        if "(欠落)" not in entry.title and getattr(entry, "filepath_rel", ""):
            entry.title = f"(欠落) {entry.title}"
    _set_image_relative_path(image, raster_id, abs_path)
    return image


def _clear_material_nodes(mat) -> None:
    nodes = mat.node_tree.nodes
    for node in list(nodes):
        nodes.remove(node)


def _node_by_name_and_type(tree, name: str, bl_idname: str):
    node = tree.nodes.get(name)
    if node is not None and getattr(node, "bl_idname", "") == bl_idname:
        return node
    return None


def _build_raster_material_nodes(mat) -> dict[str, object]:
    tree = mat.node_tree
    _clear_material_nodes(mat)
    nodes = tree.nodes
    links = tree.links

    tex = nodes.new("ShaderNodeTexImage")
    tex.name = RASTER_IMAGE_NODE
    emission = nodes.new("ShaderNodeEmission")
    emission.name = RASTER_EMISSION_NODE
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    transparent.name = RASTER_TRANSPARENT_NODE
    alpha_scale = nodes.new("ShaderNodeValue")
    alpha_scale.name = RASTER_ALPHA_SCALE_NODE
    alpha_mul = nodes.new("ShaderNodeMath")
    alpha_mul.name = RASTER_ALPHA_MULTIPLY_NODE
    alpha_mul.operation = "MULTIPLY"
    mix = nodes.new("ShaderNodeMixShader")
    mix.name = RASTER_MIX_NODE
    out = nodes.new("ShaderNodeOutputMaterial")
    out.name = RASTER_OUTPUT_NODE

    if tex.outputs.get("Alpha") is not None:
        links.new(tex.outputs["Alpha"], alpha_mul.inputs[0])
        links.new(alpha_scale.outputs[0], alpha_mul.inputs[1])
        links.new(alpha_mul.outputs[0], mix.inputs[0])
    links.new(transparent.outputs["BSDF"], mix.inputs[1])
    links.new(emission.outputs["Emission"], mix.inputs[2])
    links.new(mix.outputs["Shader"], out.inputs["Surface"])
    nodes.active = tex
    mat[RASTER_MATERIAL_VERSION_PROP] = RASTER_MATERIAL_VERSION
    return {
        "tex": tex,
        "emission": emission,
        "alpha_scale": alpha_scale,
    }


def _ensure_raster_material_nodes(mat) -> dict[str, object]:
    tree = mat.node_tree
    try:
        version = int(mat.get(RASTER_MATERIAL_VERSION_PROP, 0))
    except Exception:  # noqa: BLE001
        version = 0
    if version != RASTER_MATERIAL_VERSION:
        return _build_raster_material_nodes(mat)

    required_nodes = {
        "tex": (RASTER_IMAGE_NODE, "ShaderNodeTexImage"),
        "emission": (RASTER_EMISSION_NODE, "ShaderNodeEmission"),
        "transparent": (RASTER_TRANSPARENT_NODE, "ShaderNodeBsdfTransparent"),
        "alpha_scale": (RASTER_ALPHA_SCALE_NODE, "ShaderNodeValue"),
        "alpha_mul": (RASTER_ALPHA_MULTIPLY_NODE, "ShaderNodeMath"),
        "mix": (RASTER_MIX_NODE, "ShaderNodeMixShader"),
        "output": (RASTER_OUTPUT_NODE, "ShaderNodeOutputMaterial"),
    }
    resolved = {
        key: _node_by_name_and_type(tree, name, bl_idname)
        for key, (name, bl_idname) in required_nodes.items()
    }
    if any(node is None for node in resolved.values()):
        return _build_raster_material_nodes(mat)
    tree.nodes.active = resolved["tex"]
    return {
        "tex": resolved["tex"],
        "emission": resolved["emission"],
        "alpha_scale": resolved["alpha_scale"],
    }


def ensure_raster_material(entry, image):
    raster_id = str(getattr(entry, "id", "") or "")
    mat = bpy.data.materials.get(raster_material_name(raster_id))
    if mat is None:
        mat = bpy.data.materials.new(raster_material_name(raster_id))
    mat.use_nodes = True
    line_color = getattr(entry, "line_color", (0.0, 0.0, 0.0, 1.0))
    try:
        mat.diffuse_color = (
            float(line_color[0]),
            float(line_color[1]),
            float(line_color[2]),
            0.0,
        )
    except Exception:  # noqa: BLE001
        mat.diffuse_color = (0.0, 0.0, 0.0, 0.0)
    try:
        # ラスターの alpha は連続的 (筆圧/エッジ AA) なので BLENDED が正解。
        # DITHERED は alpha-clip + dither pattern を使うためズームすると
        # pattern がジラジラ動く副作用がある。
        mat.blend_method = "BLEND"
        mat.use_screen_refraction = False
        mat.show_transparent_back = True
    except Exception:  # noqa: BLE001
        pass
    # Blender 5.1 EEVEE Next: BLENDED 材質は alpha 合成 (滑らか)。
    # depth buffer には書込まないため、用紙背景は overlay GPU 描画ではなく
    # 実 Mesh (paper_bg_object.py) で表現する。実 Mesh は opaque 材質で
    # depth を書き、BLENDED ラスター (z=0.005) が正しく alpha 合成される。
    try:
        mat.surface_render_method = "BLENDED"
    except (AttributeError, TypeError):
        pass
    nodes = _ensure_raster_material_nodes(mat)
    tex = nodes["tex"]
    tex.image = image
    emission = nodes["emission"]
    try:
        emission.inputs["Color"].default_value = (
            float(line_color[0]),
            float(line_color[1]),
            float(line_color[2]),
            1.0,
        )
    except Exception:  # noqa: BLE001
        pass
    alpha_scale = nodes["alpha_scale"]
    try:
        alpha_scale.outputs[0].default_value = (
            max(0.0, min(1.0, float(getattr(entry, "opacity", 1.0))))
            * max(0.0, min(1.0, float(line_color[3]) if len(line_color) > 3 else 1.0))
        )
    except Exception:  # noqa: BLE001
        alpha_scale.outputs[0].default_value = 1.0
    mat.node_tree.nodes.active = tex
    return mat


def _assign_raster_material(obj, mat) -> None:
    if obj is None or mat is None or getattr(obj, "data", None) is None:
        return
    materials = getattr(obj.data, "materials", None)
    if materials is None:
        return
    if len(materials) == 1 and materials[0] is mat:
        return
    materials.clear()
    materials.append(mat)


def sync_raster_runtime_display(context, entry) -> None:
    raster_id = str(getattr(entry, "id", "") or "")
    if not raster_id:
        return
    obj = bpy.data.objects.get(raster_plane_name(raster_id))
    if obj is not None:
        visible = bool(getattr(entry, "visible", True))
        obj.hide_viewport = not visible
        obj.hide_render = not visible
    image = ensure_raster_image(context, entry, create_missing=False)
    if obj is not None and image is not None:
        mat = ensure_raster_material(entry, image)
        _assign_raster_material(obj, mat)
    layer_stack_utils.tag_view3d_redraw(context)


def _ensure_raster_mesh(work, raster_id: str):
    """ラスター plane Mesh を ensure。常にページキャンバス全体を覆う矩形 (mm).

    paper.canvas_width_mm × canvas_height_mm のページピッタリ。頂点は Mesh
    ローカル座標で z=RASTER_Z_LIFT_M。Object.location.z の z_index リフトと
    合算されて Material Preview でも他レイヤーより確実に手前に表示される。
    """
    mesh = bpy.data.meshes.get(raster_mesh_name(raster_id))
    if mesh is None:
        mesh = bpy.data.meshes.new(raster_mesh_name(raster_id))
    w = mm_to_m(float(work.paper.canvas_width_mm))
    h = mm_to_m(float(work.paper.canvas_height_mm))
    z = RASTER_Z_LIFT_M
    verts = (
        (0.0, 0.0, z),
        (w, 0.0, z),
        (w, h, z),
        (0.0, h, z),
    )
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()
    uv_layer = mesh.uv_layers.new(name="UVMap") if not mesh.uv_layers else mesh.uv_layers[0]
    for loop, uv in zip(uv_layer.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = uv
    return mesh


def _link_object_to_collection_only(obj, collection) -> None:
    for coll in tuple(obj.users_collection):
        if coll != collection:
            try:
                coll.objects.unlink(obj)
            except Exception:  # noqa: BLE001
                pass
    if obj.name not in collection.objects:
        collection.objects.link(obj)


def ensure_raster_plane(context, entry, *, mark_missing: bool = False):
    work = get_work(context)
    if work is None:
        return None
    page = None
    parent_key = str(getattr(entry, "parent_key", "") or "")
    # parent_key は "pNNNN" (page) または "pNNNN:cNN" (coma) 形式。コマ配下では
    # ":" の手前を page_id として扱い、ページ検索する。
    page_id_part = parent_key.split(":", 1)[0] if parent_key else ""
    if page_id_part:
        for candidate in getattr(work, "pages", []):
            if getattr(candidate, "id", "") == page_id_part:
                page = candidate
                break
    if page is None:
        page = get_active_page(context)
    if page is None:
        return None
    from ..utils import gpencil as gp_utils

    raster_id = str(getattr(entry, "id", "") or "")
    image = ensure_raster_image(context, entry, mark_missing=mark_missing)
    if image is None:
        return None
    mesh = _ensure_raster_mesh(work, raster_id)
    # bname_id (= raster_id) で既存 Object を逆引き。stamp_layer_object 経由で
    # canonical 名 (L0010__raster__title) にリネームされた後でも同 Object を
    # 再利用できる。これがないと毎回 raster_plane_<id> 名で新規 Object を
    # 作ってしまい、Object と Image の二重存在 + 描画対象のズレを招く。
    from ..utils import object_naming as _on

    obj = _on.find_object_by_bname_id(raster_id, kind="raster")
    if obj is None:
        # 旧名で残置されている可能性 (mirror 前のレガシー) を救出
        obj = bpy.data.objects.get(raster_plane_name(raster_id))
    if obj is None:
        obj = bpy.data.objects.new(raster_plane_name(raster_id), mesh)
    else:
        obj.data = mesh
    mat = ensure_raster_material(entry, image)
    _assign_raster_material(obj, mat)
    obj["bname_raster_id"] = raster_id
    obj["bname_raster_parent_page"] = getattr(page, "id", "")
    obj.hide_viewport = not bool(getattr(entry, "visible", True))
    obj.hide_render = not bool(getattr(entry, "visible", True))
    coll = gp_utils.ensure_page_collection(context.scene, page.id)
    _link_object_to_collection_only(obj, coll)
    # 注意: page world offset の location 設定は stamp_layer_object 側で
    # apply_page_offset=True により自動適用される (この後 stamp が呼ばれる)。

    # Phase 3a: raster Object に B-Name 安定 ID と parent を stamp し、
    # Outliner mirror の管理下に取り込む。Phase 1 で実装した
    # stamp_layer_object 経由で Outliner Collection 階層にも link 同期する。
    try:
        from ..utils import layer_object_sync as _los

        # raster の親キーは entry.parent_kind / entry.parent_key を採用。
        # PARENT_KIND_ITEMS = ("none", "page", "coma") のいずれか。"none" は
        # outside (ページ外) を意味する (ユーザーが意図的に outside に置いた
        # raster を尊重する)。
        entry_parent_key = str(getattr(entry, "parent_key", "") or "")
        entry_parent_kind = str(getattr(entry, "parent_kind", "") or "page")
        if entry_parent_kind == "none":
            stamp_parent_kind = "outside"
            stamp_parent_key = ""
        elif entry_parent_kind == "coma" and entry_parent_key:
            stamp_parent_kind = "coma"
            stamp_parent_key = entry_parent_key
        else:
            # page or 不正値の fallback
            stamp_parent_kind = "page"
            stamp_parent_key = entry_parent_key or str(getattr(page, "id", "") or "")

        # BNameRasterLayer には z_index フィールドが無いため、scene 内の
        # raster 配列での index に 10 を掛けて sequential な z_index を採番。
        # これにより複数 raster が異なる prefix を持ち、Outliner alpha sort で
        # 順序破綻しない。
        z_index = 0
        coll = getattr(context.scene, "bname_raster_layers", None)
        if coll is not None:
            for i, e in enumerate(coll):
                if str(getattr(e, "id", "") or "") == raster_id:
                    z_index = (i + 1) * 10
                    break

        _los.stamp_layer_object(
            obj,
            kind="raster",
            bname_id=str(raster_id),
            title=str(getattr(entry, "title", "") or raster_id),
            z_index=z_index,
            parent_kind=stamp_parent_kind,
            parent_key=stamp_parent_key,
            scene=context.scene,
        )
        # コマ/ページマスクを Boolean Intersect で適用 (枠外を視覚的に切抜き)
        try:
            from ..utils import mask_apply

            mask_apply.apply_mask_to_layer_object(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("raster: mask_apply failed")
    except Exception:  # noqa: BLE001
        _logger.exception("raster: stamp_layer_object failed")
    return obj


def save_raster_png(context, entry, *, force: bool = False) -> bool:
    work = get_work(context)
    if work is None or not getattr(work, "work_dir", ""):
        return False
    image = ensure_raster_image(context, entry, create_missing=False)
    if image is None:
        return False
    custom_dirty = False
    try:
        custom_dirty = bool(entry.get("bname_raster_dirty", False))
    except Exception:  # noqa: BLE001
        custom_dirty = False
    if not force and not bool(getattr(image, "is_dirty", False)) and not custom_dirty:
        return False
    work_dir = Path(work.work_dir)
    abs_path = _abs_png_path(work_dir, entry)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        image.file_format = "PNG"
        image.filepath_raw = str(abs_path)
        image.save()
    except Exception:
        image.save_render(str(abs_path))
    _set_image_relative_path(image, entry.id, abs_path)
    try:
        entry["bname_raster_dirty"] = False
    except Exception:  # noqa: BLE001
        pass
    return True


def mark_raster_dirty(entry) -> None:
    try:
        entry["bname_raster_dirty"] = True
    except Exception:  # noqa: BLE001
        pass


def translate_raster_layer_pixels(context, entry, dx_mm: float, dy_mm: float) -> bool:
    """コマ移動時に、親コマ配下のラスター画素をページ座標上で平行移動する."""
    image = ensure_raster_image(context, entry, create_missing=False)
    if image is None:
        return False
    dpi = int(getattr(entry, "dpi", 300))
    dx_px = int(round(mm_to_px(float(dx_mm), dpi)))
    dy_px = int(round(mm_to_px(float(dy_mm), dpi)))
    if dx_px == 0 and dy_px == 0:
        return False
    try:
        width, height = int(image.size[0]), int(image.size[1])
    except Exception:  # noqa: BLE001
        return False
    if width <= 0 or height <= 0:
        return False
    channels = 4
    total = width * height * channels
    source = array("f", image.pixels[:])
    if len(source) != total:
        return False
    dest = array("f", [0.0]) * total
    src_x0 = max(0, -dx_px)
    src_x1 = min(width, width - dx_px)
    src_y0 = max(0, -dy_px)
    src_y1 = min(height, height - dy_px)
    if src_x0 >= src_x1 or src_y0 >= src_y1:
        try:
            image.pixels[:] = dest
            image.update()
        except Exception:  # noqa: BLE001
            return False
        mark_raster_dirty(entry)
        return True
    row_values = (src_x1 - src_x0) * channels
    for src_y in range(src_y0, src_y1):
        dst_y = src_y + dy_px
        src_start = (src_y * width + src_x0) * channels
        dst_start = (dst_y * width + src_x0 + dx_px) * channels
        dest[dst_start:dst_start + row_values] = source[src_start:src_start + row_values]
    try:
        image.pixels[:] = dest
        image.update()
    except Exception:  # noqa: BLE001
        return False
    mark_raster_dirty(entry)
    return True


def save_dirty_raster_layers(context) -> int:
    scene = getattr(context, "scene", None)
    coll = _raster_collection(scene) if scene is not None else None
    if coll is None:
        return 0
    saved = 0
    for entry in coll:
        try:
            if save_raster_png(context, entry, force=False):
                saved += 1
        except Exception:  # noqa: BLE001
            _logger.exception("dirty raster save failed: %s", getattr(entry, "id", ""))
    return saved


def ensure_all_raster_runtime(context) -> int:
    scene = getattr(context, "scene", None)
    coll = _raster_collection(scene) if scene is not None else None
    if coll is None:
        return 0
    count = 0
    for entry in coll:
        if ensure_raster_plane(context, entry, mark_missing=True) is not None:
            count += 1
    return count


def purge_raster_runtime(entry) -> None:
    raster_id = str(getattr(entry, "id", "") or "")
    obj = bpy.data.objects.get(raster_plane_name(raster_id))
    if obj is not None:
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and getattr(mesh, "users", 0) == 0:
            bpy.data.meshes.remove(mesh)
    mat = bpy.data.materials.get(raster_material_name(raster_id))
    if mat is not None:
        bpy.data.materials.remove(mat, do_unlink=True)
    image = bpy.data.images.get(str(getattr(entry, "image_name", "") or raster_image_name(raster_id)))
    if image is not None:
        bpy.data.images.remove(image, do_unlink=True)


def purge_all_raster_runtime(scene) -> int:
    coll = _raster_collection(scene)
    if coll is None:
        return 0
    count = 0
    for entry in list(coll):
        purge_raster_runtime(entry)
        count += 1
    return count


def remove_raster_by_index(context, index: int) -> bool:
    scene = context.scene
    coll = _raster_collection(scene)
    if coll is None or not (0 <= index < len(coll)):
        return False
    entry = coll[index]
    raster_id = str(getattr(entry, "id", "") or "")
    work = get_work(context)
    if work is not None and getattr(work, "work_dir", ""):
        src = _abs_png_path(Path(work.work_dir), entry)
        if src.exists():
            trash = paths.raster_trash_dir(Path(work.work_dir))
            trash.mkdir(parents=True, exist_ok=True)
            dst = trash / src.name
            suffix = 1
            while dst.exists():
                dst = trash / f"{src.stem}_{suffix}{src.suffix}"
                suffix += 1
            try:
                shutil.move(str(src), str(dst))
            except Exception:  # noqa: BLE001
                _logger.exception("raster png trash move failed: %s", src)
    purge_raster_runtime(entry)
    coll.remove(index)
    scene.bname_active_raster_layer_index = min(index, len(coll) - 1) if len(coll) else -1
    return True


def _active_image_paint_brush(context):
    paint = getattr(getattr(context, "tool_settings", None), "image_paint", None)
    brush = getattr(paint, "brush", None) if paint is not None else None
    if brush is None:
        try:
            brush = bpy.data.brushes.new("B-Name Raster Brush", mode="TEXTURE_PAINT")
            paint.brush = brush
        except Exception:  # noqa: BLE001
            brush = None
    if brush is not None:
        try:
            if not bool(brush.get(RASTER_BRUSH_INITIALIZED_PROP, False)):
                brush.color = (0.0, 0.0, 0.0)
                brush[RASTER_BRUSH_INITIALIZED_PROP] = True
        except Exception:  # noqa: BLE001
            pass
    return brush


def force_active_brush_grayscale(context) -> bool:
    brush = _active_image_paint_brush(context)
    if brush is None or not hasattr(brush, "color"):
        return False
    try:
        color = tuple(float(c) for c in brush.color[:3])
        gray = max(0.0, min(1.0, sum(color) / 3.0))
        if any(abs(c - gray) > 1.0e-5 for c in color):
            brush.color = (gray, gray, gray)
        return True
    except Exception:  # noqa: BLE001
        return False


_brush_timer_running = False


def _brush_grayscale_timer():
    global _brush_timer_running
    try:
        context = bpy.context
        scene = getattr(context, "scene", None)
        active_kind = getattr(scene, "bname_active_layer_kind", "") if scene is not None else ""
        obj = getattr(getattr(context, "view_layer", None), "objects", None)
        active = getattr(obj, "active", None) if obj is not None else None
        if active_kind != "raster" or getattr(active, "mode", "") != "TEXTURE_PAINT":
            _brush_timer_running = False
            return None
        force_active_brush_grayscale(context)
        entry, _idx = active_raster_entry(context)
        image = ensure_raster_image(context, entry, create_missing=False) if entry is not None else None
        if image is not None and bool(getattr(image, "is_dirty", False)):
            mark_raster_dirty(entry)
        return 0.2
    except Exception:  # noqa: BLE001
        _brush_timer_running = False
        return None


def _start_brush_grayscale_timer() -> None:
    global _brush_timer_running
    if _brush_timer_running:
        return
    _brush_timer_running = True
    bpy.app.timers.register(_brush_grayscale_timer, first_interval=0.05)


class BNAME_OT_raster_layer_add(Operator):
    bl_idname = "bname.raster_layer_add"
    bl_label = "ラスター描画レイヤーを追加"
    bl_options = {"REGISTER", "UNDO"}

    dpi_preset: EnumProperty(  # type: ignore[valid-type]
        name="DPI",
        items=(
            ("150", "150 dpi", "下描き / 確認用 (軽量)"),
            ("300", "300 dpi", "標準 (推奨)"),
            ("600", "600 dpi", "印刷向け (高解像度)"),
            ("custom", "カスタム", "カスタム値を直接指定"),
        ),
        default="300",
    )
    dpi: IntProperty(name="カスタム DPI", default=300, min=30, soft_max=1200)  # type: ignore[valid-type]
    bit_depth: EnumProperty(  # type: ignore[valid-type]
        name="階調",
        items=(("gray8", "グレー 8bit", ""), ("gray1", "1bit", "")),
        default="gray8",
    )
    enter_paint: BoolProperty(  # type: ignore[valid-type]
        name="作成後すぐ描画開始",
        description="生成完了後、自動的に Texture Paint モードへ切替えます。",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return _raster_collection(context.scene) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "dpi_preset")
        if self.dpi_preset == "custom":
            layout.prop(self, "dpi")
        layout.prop(self, "bit_depth")
        layout.separator()
        layout.prop(self, "enter_paint")

    def _resolved_dpi(self) -> int:
        if self.dpi_preset == "custom":
            return int(self.dpi)
        try:
            return int(self.dpi_preset)
        except ValueError:
            return 300

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        coll = _raster_collection(context.scene)
        if work is None or not getattr(work, "loaded", False) or not getattr(work, "work_dir", ""):
            self.report({"ERROR"}, "作品が開かれていません")
            return {"CANCELLED"}
        if page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        raster_id = _allocate_raster_id(context.scene, Path(work.work_dir))
        entry = coll.add()
        entry.id = raster_id
        entry.title = f"ラスター {len(coll)}"
        entry.image_name = raster_image_name(raster_id)
        entry.filepath_rel = raster_filepath_rel(raster_id)
        entry.dpi = self._resolved_dpi()
        entry.bit_depth = self.bit_depth
        entry.scope = "page"
        # アクティブな階層 (コマ選択中ならコマ、そうでなければページ) を反映
        from ..utils import active_target as _at

        parent_kind, parent_key, _resolved_page = _at.resolve_active_target(
            context, prefer_page=page
        )
        entry.parent_kind = parent_kind
        entry.parent_key = parent_key or page.id
        context.scene.bname_active_raster_layer_index = len(coll) - 1
        context.scene.bname_active_layer_kind = "raster"
        if ensure_raster_plane(context, entry) is None:
            coll.remove(len(coll) - 1)
            self.report({"ERROR"}, "ラスター実体の作成に失敗しました")
            return {"CANCELLED"}
        save_raster_png(context, entry, force=True)
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        stack = getattr(context.scene, "bname_layer_stack", None)
        uid = layer_stack_utils.target_uid("raster", raster_id)
        if stack is not None:
            for i, item in enumerate(stack):
                if layer_stack_utils.stack_item_uid(item) == uid:
                    layer_stack_utils.select_stack_index(context, i)
                    break
        # 作成完了後に自動的に Texture Paint モードへ入る (enter_paint=True 時)
        if bool(self.enter_paint):
            try:
                bpy.ops.bname.raster_layer_paint_enter(
                    "INVOKE_DEFAULT", raster_id=raster_id
                )
            except Exception:  # noqa: BLE001
                _logger.exception("raster_layer_add: paint_enter 自動切替失敗")
        return {"FINISHED"}


class BNAME_OT_raster_layer_remove(Operator):
    bl_idname = "bname.raster_layer_remove"
    bl_label = "ラスター描画レイヤーを削除"
    bl_options = {"REGISTER", "UNDO"}

    raster_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        if self.raster_id:
            _entry, idx = find_raster_entry(context.scene, self.raster_id)
        else:
            _entry, idx = active_raster_entry(context)
        if idx < 0 or not remove_raster_by_index(context, idx):
            return {"CANCELLED"}
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return {"FINISHED"}


class BNAME_OT_raster_layer_select(Operator):
    bl_idname = "bname.raster_layer_select"
    bl_label = "ラスター描画レイヤーを選択"
    bl_options = {"REGISTER"}

    raster_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    index: IntProperty(default=-1)  # type: ignore[valid-type]

    def execute(self, context):
        idx = self.index
        if self.raster_id:
            _entry, idx = find_raster_entry(context.scene, self.raster_id)
        coll = _raster_collection(context.scene)
        if coll is None or not (0 <= idx < len(coll)):
            return {"CANCELLED"}
        context.scene.bname_active_raster_layer_index = idx
        context.scene.bname_active_layer_kind = "raster"
        return {"FINISHED"}


class BNAME_OT_raster_layer_paint_enter(Operator):
    bl_idname = "bname.raster_layer_paint_enter"
    bl_label = "Texture Paint へ入る"
    bl_options = {"REGISTER"}

    raster_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        entry, idx = (
            find_raster_entry(context.scene, self.raster_id)
            if self.raster_id else active_raster_entry(context)
        )
        if entry is None or idx < 0:
            self.report({"WARNING"}, "ラスター描画レイヤーを選択してください")
            return {"CANCELLED"}
        if bool(getattr(entry, "locked", False)):
            self.report({"WARNING"}, "ロックされたラスターには描画できません")
            return {"CANCELLED"}
        if not bool(getattr(entry, "visible", True)):
            self.report({"WARNING"}, "非表示のラスターには描画できません")
            return {"CANCELLED"}
        try:
            from . import coma_modal_state

            coma_modal_state.finish_all(context)
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(context.object, "mode", "OBJECT") != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:  # noqa: BLE001
            pass
        obj = ensure_raster_plane(context, entry)
        image = ensure_raster_image(context, entry)
        if obj is None or image is None:
            return {"CANCELLED"}
        for selected in tuple(getattr(context, "selected_objects", []) or []):
            if selected is not obj:
                selected.select_set(False)
        context.view_layer.objects.active = obj
        obj.select_set(True)
        context.scene.bname_active_raster_layer_index = idx
        context.scene.bname_active_layer_kind = "raster"
        paint = getattr(context.tool_settings, "image_paint", None)
        if paint is not None:
            try:
                paint.canvas = image
            except Exception:  # noqa: BLE001
                pass
        force_active_brush_grayscale(context)
        # paper_bg Mesh を一時的に隠す: opaque な paper_bg が raycast に
        # 干渉して active raster mesh の UV 取得が失敗する (= 描けない) のを
        # 防ぐ。Texture Paint 中は raster mesh のみが ray 対象になる。
        try:
            from ..utils import paper_bg_object as _pbg

            _pbg.set_paper_bg_visible(False)
        except Exception:  # noqa: BLE001
            _logger.exception("paper_bg hide failed (paint enter)")
        try:
            bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
        except Exception as exc:  # noqa: BLE001
            self.report({"WARNING"}, f"Texture Paintへ切替できません: {exc}")
            return {"CANCELLED"}
        force_active_brush_grayscale(context)
        _start_brush_grayscale_timer()
        # 3D ビューをマテリアルプレビューに切替えて、Image Texture (= 描いた
        # ピクセル) が即座に見える状態にする。Solid モードでは Image Texture
        # が反映されず「描いても見えない」状態になるため。
        try:
            for area in context.screen.areas if context.screen else ():
                if area.type != "VIEW_3D":
                    continue
                space = area.spaces.active
                if space is None or space.type != "VIEW_3D":
                    continue
                shading = getattr(space, "shading", None)
                if shading is None:
                    continue
                if shading.type not in {"MATERIAL", "RENDERED"}:
                    try:
                        shading.type = "MATERIAL"
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        return {"FINISHED"}


class BNAME_OT_raster_layer_paint_exit(Operator):
    bl_idname = "bname.raster_layer_paint_exit"
    bl_label = "Texture Paint を終了"
    bl_options = {"REGISTER"}

    def execute(self, context):
        entry, _idx = active_raster_entry(context)
        if entry is not None:
            save_raster_png(context, entry, force=True)
        try:
            if getattr(context.object, "mode", "") != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception as exc:  # noqa: BLE001
            self.report({"WARNING"}, f"Objectモードへ戻せません: {exc}")
            return {"CANCELLED"}
        # paint 終了 → paper_bg を再表示して用紙白を復元
        try:
            from ..utils import paper_bg_object as _pbg

            _pbg.set_paper_bg_visible(True)
        except Exception:  # noqa: BLE001
            _logger.exception("paper_bg show failed (paint exit)")
        return {"FINISHED"}


class BNAME_OT_raster_layer_mode_set(Operator):
    bl_idname = "bname.raster_layer_mode_set"
    bl_label = "ラスター描画モード切替"
    bl_options = {"REGISTER", "INTERNAL"}

    mode: StringProperty(default="TEXTURE_PAINT")  # type: ignore[valid-type]

    def execute(self, context):
        if self.mode == "TEXTURE_PAINT":
            return bpy.ops.bname.raster_layer_paint_enter("EXEC_DEFAULT")
        if self.mode == "OBJECT":
            return bpy.ops.bname.raster_layer_paint_exit("EXEC_DEFAULT")
        return {"CANCELLED"}


class BNAME_OT_raster_layer_save_png(Operator):
    bl_idname = "bname.raster_layer_save_png"
    bl_label = "ラスターPNGを書き出し"
    bl_options = {"REGISTER"}

    raster_id: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    force: BoolProperty(default=False, options={"HIDDEN"})  # type: ignore[valid-type]

    def execute(self, context):
        entry = None
        if self.raster_id:
            entry, _idx = find_raster_entry(context.scene, self.raster_id)
        else:
            entry, _idx = active_raster_entry(context)
        if entry is None:
            return {"CANCELLED"}
        if save_raster_png(context, entry, force=bool(self.force)):
            return {"FINISHED"}
        return {"CANCELLED"}


class BNAME_OT_raster_layer_resample(Operator):
    bl_idname = "bname.raster_layer_resample"
    bl_label = "ラスターをリサンプル"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        self.report({"INFO"}, "DPIリサンプルは Phase 3 で実装します")
        return {"CANCELLED"}


class BNAME_OT_raster_layer_set_bit_depth(Operator):
    bl_idname = "bname.raster_layer_set_bit_depth"
    bl_label = "ラスター階調を変更"
    bl_options = {"REGISTER", "UNDO"}

    bit_depth: EnumProperty(  # type: ignore[valid-type]
        items=(("gray8", "グレー 8bit", ""), ("gray1", "1bit", "")),
        default="gray8",
    )

    def execute(self, context):
        entry, _idx = active_raster_entry(context)
        if entry is None:
            return {"CANCELLED"}
        entry.bit_depth = self.bit_depth
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_raster_layer_add,
    BNAME_OT_raster_layer_remove,
    BNAME_OT_raster_layer_select,
    BNAME_OT_raster_layer_paint_enter,
    BNAME_OT_raster_layer_paint_exit,
    BNAME_OT_raster_layer_mode_set,
    BNAME_OT_raster_layer_save_png,
    BNAME_OT_raster_layer_resample,
    BNAME_OT_raster_layer_set_bit_depth,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
