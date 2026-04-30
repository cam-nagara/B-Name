"""ページ用紙背景の opaque な Mesh Plane.

ラスター材質は BLENDED (滑らかな alpha 合成) であるため depth buffer に
書込まない。一方、ビューポートで「用紙の白」を見せるためには:

- 旧実装 (NG): GPU overlay (POST_VIEW) で用紙塗りを描いていた
  → 3D シーン描画後に上から塗るため、BLENDED ラスター paint を覆い隠す
- 旧実装 (NG): ラスターを DITHERED に切替えて depth を書かせる
  → dither pattern がズームでジラジラ動く

正解: ページごとに **opaque な Mesh Plane** を z=0 に置く。
- opaque 材質は depth を書く → ラスター Mesh (z=0.005, BLENDED) は
  その上に正しく alpha 合成される
- ズームしてもパターン揺らぎなし
- Blender 標準のレンダリングフローに乗るため副作用が少ない

Mesh は ``__papers__`` Collection に集約し、selectable=False で配置して
ユーザーの誤選択を防ぐ。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import log
from . import object_naming as on
from . import outliner_model as om
from .geom import mm_to_m

_logger = log.get_logger(__name__)

PAPERS_COLLECTION_NAME = "__papers__"
PAPERS_COLLECTION_BNAME_ID = "__papers_root__"
PAPER_BG_NAME_PREFIX = "page_paper_bg_"
PAPER_BG_MESH_PREFIX = "paper_bg_mesh_"
PAPER_BG_MATERIAL_NAME = "BName_PaperBackground"

PROP_BG_KIND = "bname_paper_bg_kind"
PROP_BG_OWNER_ID = "bname_paper_bg_page_id"


def _ensure_papers_collection(scene: bpy.types.Scene) -> Optional[bpy.types.Collection]:
    """``__papers__`` Collection を確保 (B-Name root 直下)."""
    if scene is None:
        return None
    coll = on.find_collection_by_bname_id(PAPERS_COLLECTION_BNAME_ID, kind="papers_root")
    if coll is None:
        existing = bpy.data.collections.get(PAPERS_COLLECTION_NAME)
        if existing is not None:
            coll = existing
        else:
            coll = bpy.data.collections.new(PAPERS_COLLECTION_NAME)
    on.stamp_identity(
        coll,
        kind="papers_root",
        bname_id=PAPERS_COLLECTION_BNAME_ID,
        title=PAPERS_COLLECTION_NAME,
        z_index=-1,
        managed=False,
    )
    root = om.ensure_root_collection(scene)
    if not any(c is coll for c in root.children):
        try:
            root.children.link(coll)
        except Exception:  # noqa: BLE001
            _logger.exception("link __papers__ to root failed")
    return coll


def _ensure_paper_material(paper) -> bpy.types.Material:
    """用紙背景用の opaque 材質 (paper_color に追従).

    ``paper.paper_color`` は scene-linear。Principled BSDF の Base Color に
    そのまま流して、Emission も同色にして solid 表示でも陰影が乗らない
    フラット白の見た目にする。
    """
    mat = bpy.data.materials.get(PAPER_BG_MATERIAL_NAME)
    if mat is None:
        mat = bpy.data.materials.new(PAPER_BG_MATERIAL_NAME)
    mat.use_nodes = True
    try:
        # opaque (= depth を書く)
        mat.blend_method = "OPAQUE"
        mat.surface_render_method = "DITHERED"  # opaque-equivalent
    except (AttributeError, TypeError):
        pass

    color_rgba = (1.0, 1.0, 1.0, 1.0)
    if paper is not None:
        try:
            r, g, b = paper.paper_color[:3]
            color_rgba = (float(r), float(g), float(b), 1.0)
        except Exception:  # noqa: BLE001
            pass

    nt = mat.node_tree
    # 既存ノードを全削除して再構築 (paper_color 変更追従)
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (200, 0)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (-100, 0)
    try:
        emission.inputs["Color"].default_value = color_rgba
        emission.inputs["Strength"].default_value = 1.0
    except Exception:  # noqa: BLE001
        pass
    nt.links.new(emission.outputs["Emission"], out.inputs["Surface"])

    # Solid 表示でも色が出るよう viewport_color (diffuse_color) も同期
    try:
        mat.diffuse_color = color_rgba
    except Exception:  # noqa: BLE001
        pass
    return mat


def _ensure_paper_mesh(width_m: float, height_m: float) -> bpy.types.Mesh:
    """用紙サイズの Mesh Plane を ensure (各ページで共用可能だが、サイズが
    変わる可能性は低いので 1 つを使い回す)."""
    mesh_name = f"{PAPER_BG_MESH_PREFIX}main"
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
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
    if not mesh.materials:
        mesh.materials.append(_ensure_paper_material(None))
    return mesh


def ensure_paper_bg_for_page(
    scene: bpy.types.Scene, work, page_index: int
) -> Optional[bpy.types.Object]:
    """1 ページ分の用紙背景 Mesh を ensure し、page_grid 位置に配置."""
    if scene is None or work is None or not (0 <= page_index < len(work.pages)):
        return None
    page = work.pages[page_index]
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return None
    paper = work.paper
    width_mm = float(getattr(paper, "canvas_width_mm", 257.0) or 257.0)
    height_mm = float(getattr(paper, "canvas_height_mm", 364.0) or 364.0)
    mesh = _ensure_paper_mesh(mm_to_m(width_mm), mm_to_m(height_mm))
    # 材質は paper.paper_color に追従させる
    mat = _ensure_paper_material(paper)
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat

    obj_name = f"{PAPER_BG_NAME_PREFIX}{page_id}"
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    else:
        obj.data = mesh
    obj[PROP_BG_KIND] = "page"
    obj[PROP_BG_OWNER_ID] = page_id
    obj[on.PROP_MANAGED] = False  # Outliner mirror 正規化対象外
    obj.hide_select = True  # ユーザーが触って動かさないように
    obj.hide_render = True  # B-Name の export は別 path で行うため render off
    # display_type = TEXTURED で普通に塗りが見える状態に
    try:
        obj.display_type = "TEXTURED"
    except Exception:  # noqa: BLE001
        pass

    # ページの world オフセットに配置 (page_grid)
    try:
        from . import page_grid as _pg

        ox_mm, oy_mm = _pg.page_total_offset_mm(work, scene, page_index)
        obj.location.x = mm_to_m(ox_mm)
        obj.location.y = mm_to_m(oy_mm)
        # z は 0 に固定 (raster は z=0.005 の上)
        obj.location.z = 0.0
    except Exception:  # noqa: BLE001
        _logger.exception("paper_bg page offset 失敗")

    # __papers__ Collection に link (他から外す)
    papers_coll = _ensure_papers_collection(scene)
    if papers_coll is not None and not any(o is obj for o in papers_coll.objects):
        try:
            papers_coll.objects.link(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("link paper_bg to __papers__ failed")
    # 他 Collection からの link は外す (Outliner ヒエラルキ汚染防止)
    for coll in tuple(obj.users_collection):
        if coll is papers_coll:
            continue
        try:
            coll.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass
    return obj


def regenerate_all_paper_bgs(scene: bpy.types.Scene, work) -> int:
    """全ページの用紙背景 Mesh を ensure。戻り値: 生成/更新したページ数."""
    if scene is None or work is None:
        return 0
    if not getattr(work, "loaded", False):
        return 0
    valid_ids: set[str] = set()
    count = 0
    for i, page in enumerate(work.pages):
        if ensure_paper_bg_for_page(scene, work, i) is not None:
            count += 1
            page_id = str(getattr(page, "id", "") or "")
            if page_id:
                valid_ids.add(page_id)
    # 削除済ページの paper_bg Object を掃除
    for obj in list(bpy.data.objects):
        if obj.get(PROP_BG_KIND) != "page":
            continue
        owner = str(obj.get(PROP_BG_OWNER_ID, "") or "")
        if owner not in valid_ids:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:  # noqa: BLE001
                pass
    return count
