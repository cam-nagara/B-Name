"""ページマスク / コママスク Object ヘルパ (Phase 5).

計画書 ``docs/outliner_object_layer_plan_2026-04-30.md`` Phase 5 を実装。
保守的範囲では「mask Mesh Object をデータとして生成」までを担当。
material clip / GP layer mask の機能発動は Phase 5c 以降。

mask Object は B-Name root 配下の専用 Collection ``__masks__`` に集めて
Outliner で D&D 対象外になるよう ``bname_managed=False`` で stamp する。
形状の正は B-Name のページ/コマデータなので、操作で常に再生成可能。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import log
from . import object_naming as on
from . import outliner_model as om
from .geom import mm_to_m

_logger = log.get_logger(__name__)

MASKS_COLLECTION_NAME = "__masks__"
MASKS_COLLECTION_BNAME_ID = "__masks_root__"

PAGE_MASK_NAME_PREFIX = "page_mask_"
COMA_MASK_NAME_PREFIX = "coma_mask_"
PAGE_MASK_MESH_PREFIX = "page_mask_mesh_"
COMA_MASK_MESH_PREFIX = "coma_mask_mesh_"

# mask Object に立てる識別フラグ (Outliner mirror では unmanaged)
PROP_MASK_KIND = "bname_mask_kind"  # "page" | "coma"
PROP_MASK_OWNER_ID = "bname_mask_owner_id"  # page_id or "page_id:coma_id"


def ensure_masks_collection(scene: bpy.types.Scene) -> Optional[bpy.types.Collection]:
    """``__masks__`` Collection を確保。B-Name root 直下に置く."""
    if scene is None:
        return None
    coll = on.find_collection_by_bname_id(MASKS_COLLECTION_BNAME_ID, kind="masks_root")
    if coll is None:
        existing = bpy.data.collections.get(MASKS_COLLECTION_NAME)
        if existing is not None:
            coll = existing
        else:
            coll = bpy.data.collections.new(MASKS_COLLECTION_NAME)
    on.stamp_identity(
        coll,
        kind="masks_root",
        bname_id=MASKS_COLLECTION_BNAME_ID,
        title="マスク",
        z_index=0,
        managed=False,  # Outliner D&D 正規化対象外
    )
    root = om.ensure_root_collection(scene)
    # identity 比較で重複 link 回避 (.001 自動付加リネームに耐える)
    if not any(c is coll for c in root.children):
        try:
            root.children.link(coll)
        except Exception:  # noqa: BLE001
            _logger.exception("link masks collection to root failed")
    # マスク Mesh が viewport に黒い面として描画されると本体レイヤーが見えなく
    # なるため、Collection ごと viewport から非表示にする。Modifier の target
    # 参照は hidden でも有効なのでクリッピング機能には影響しない。
    if scene is not None:
        try:
            view_layer = bpy.context.view_layer
            layer_coll = _find_layer_collection(view_layer.layer_collection, coll)
            if layer_coll is not None:
                layer_coll.exclude = False  # Outliner には残す
                layer_coll.hide_viewport = True
        except Exception:  # noqa: BLE001
            _logger.exception("hide masks collection in viewport failed")
    return coll


def _find_layer_collection(root_lc, target_coll):
    """LayerCollection ツリーから target Collection を探す."""
    if root_lc is None or target_coll is None:
        return None
    if root_lc.collection is target_coll:
        return root_lc
    for child in root_lc.children:
        found = _find_layer_collection(child, target_coll)
        if found is not None:
            return found
    return None


def _ensure_rect_mesh(name: str, width_m: float, height_m: float) -> bpy.types.Mesh:
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
    return mesh


def _ensure_polygon_mesh(name: str, points_mm: list[tuple[float, float]]) -> bpy.types.Mesh:
    """頂点群 (mm) から多角形 Mesh を ensure."""
    mesh = bpy.data.meshes.get(name)
    if mesh is None:
        mesh = bpy.data.meshes.new(name)
    mesh.clear_geometry()
    if len(points_mm) < 3:
        return mesh
    verts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in points_mm]
    faces = [tuple(range(len(verts)))]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def ensure_page_mask_object(
    scene: bpy.types.Scene, paper, page
) -> Optional[bpy.types.Object]:
    """ページマスク Object を ensure (paper の canvas サイズに従う)."""
    if scene is None or paper is None or page is None:
        return None
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return None
    width_m = mm_to_m(float(getattr(paper, "canvas_width_mm", 257.0) or 257.0))
    height_m = mm_to_m(float(getattr(paper, "canvas_height_mm", 364.0) or 364.0))
    mesh = _ensure_rect_mesh(f"{PAGE_MASK_MESH_PREFIX}{page_id}", width_m, height_m)
    obj_name = f"{PAGE_MASK_NAME_PREFIX}{page_id}"
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    else:
        obj.data = mesh
    # mask Object 識別
    obj[PROP_MASK_KIND] = "page"
    obj[PROP_MASK_OWNER_ID] = page_id
    obj[on.PROP_MANAGED] = False  # Outliner mirror 正規化対象外
    obj.hide_render = True  # render には出さない (clip 用 reference のみ)
    obj.hide_viewport = True  # 3D ビューにも描画しない (Modifier target としてのみ機能)
    # display_type を BOUNDS にして万一表示されたときも目立たないように
    try:
        obj.display_type = "BOUNDS"
    except Exception:  # noqa: BLE001
        pass
    # __masks__ Collection に link (他から外す)
    masks_coll = ensure_masks_collection(scene)
    if masks_coll is not None and not any(o is obj for o in masks_coll.objects):
        try:
            masks_coll.objects.link(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("link mask object to masks failed")
    return obj


def ensure_coma_mask_object(
    scene: bpy.types.Scene, page, coma
) -> Optional[bpy.types.Object]:
    """コマスマスク Object を ensure (shape_type に従う).

    shape_type:
        - rect: rect_x_mm / rect_y_mm / rect_width_mm / rect_height_mm
        - polygon / bezier / freeform: vertices CollectionProperty
    """
    if scene is None or page is None or coma is None:
        return None
    page_id = str(getattr(page, "id", "") or "")
    coma_id = str(getattr(coma, "id", "") or "")
    if not page_id or not coma_id:
        return None
    owner_id = f"{page_id}:{coma_id}"
    mesh_name = f"{COMA_MASK_MESH_PREFIX}{page_id}_{coma_id}"
    obj_name = f"{COMA_MASK_NAME_PREFIX}{page_id}_{coma_id}"

    shape_type = str(getattr(coma, "shape_type", "rect") or "rect")
    if shape_type == "rect":
        rect_x = float(getattr(coma, "rect_x_mm", 0.0) or 0.0)
        rect_y = float(getattr(coma, "rect_y_mm", 0.0) or 0.0)
        width = float(getattr(coma, "rect_width_mm", 50.0) or 50.0)
        height = float(getattr(coma, "rect_height_mm", 50.0) or 50.0)
        mesh = _ensure_rect_mesh(mesh_name, mm_to_m(width), mm_to_m(height))
        offset_x_m = mm_to_m(rect_x)
        offset_y_m = mm_to_m(rect_y)
    else:
        vertices = getattr(coma, "vertices", None)
        if vertices is None or len(vertices) < 3:
            # 不正な shape は rect fallback
            mesh = _ensure_rect_mesh(mesh_name, mm_to_m(50.0), mm_to_m(50.0))
            offset_x_m = 0.0
            offset_y_m = 0.0
        else:
            points = [(float(v.x_mm), float(v.y_mm)) for v in vertices]
            mesh = _ensure_polygon_mesh(mesh_name, points)
            offset_x_m = 0.0
            offset_y_m = 0.0

    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    else:
        obj.data = mesh
    obj.location.x = offset_x_m
    obj.location.y = offset_y_m
    obj[PROP_MASK_KIND] = "coma"
    obj[PROP_MASK_OWNER_ID] = owner_id
    obj[on.PROP_MANAGED] = False
    obj.hide_render = True
    obj.hide_viewport = True
    try:
        obj.display_type = "BOUNDS"
    except Exception:  # noqa: BLE001
        pass
    masks_coll = ensure_masks_collection(scene)
    if masks_coll is not None and not any(o is obj for o in masks_coll.objects):
        try:
            masks_coll.objects.link(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("link coma mask to masks failed")
    return obj


def regenerate_all_masks(scene: bpy.types.Scene, work) -> dict:
    """全 mask Object を再生成して orphan も削除. 冪等で安全."""
    result = {"page_masks": 0, "coma_masks": 0}
    if scene is None or work is None:
        return result
    paper = getattr(work, "paper", None)
    for page in getattr(work, "pages", []):
        if not getattr(page, "id", ""):
            continue
        if ensure_page_mask_object(scene, paper, page):
            result["page_masks"] += 1
        for coma in getattr(page, "comas", []):
            if not getattr(coma, "id", ""):
                continue
            if ensure_coma_mask_object(scene, page, coma):
                result["coma_masks"] += 1
    # orphan 掃除も同時に行う (冪等性)
    remove_orphan_masks(scene, work)
    return result


def remove_orphan_masks(scene: bpy.types.Scene, work) -> int:
    """work に対応 entry が無い mask Object と orphan Mesh を削除する.

    削除対象は **B-Name 標準名 prefix を持つもののみ** に限定し、ユーザーが
    手動で rename した mask Object は残す。
    """
    if scene is None or work is None:
        return 0
    valid_page_ids = set()
    valid_coma_ids = set()
    for page in getattr(work, "pages", []):
        pid = str(getattr(page, "id", "") or "")
        if pid:
            valid_page_ids.add(pid)
        for coma in getattr(page, "comas", []):
            cid = str(getattr(coma, "id", "") or "")
            if cid:
                valid_coma_ids.add(f"{pid}:{cid}")

    # 削除対象を一旦 list 化してから削除 (走査中削除の連鎖切れ回避)
    to_remove: list[bpy.types.Object] = []
    for obj in list(bpy.data.objects):
        kind = obj.get(PROP_MASK_KIND)
        if kind not in {"page", "coma"}:
            continue
        # ユーザー rename 検出: 標準 prefix を持たないものはスキップ (尊重)
        std_prefix = (
            PAGE_MASK_NAME_PREFIX if kind == "page" else COMA_MASK_NAME_PREFIX
        )
        if not obj.name.startswith(std_prefix):
            continue
        owner = str(obj.get(PROP_MASK_OWNER_ID, "") or "")
        if kind == "page" and owner not in valid_page_ids:
            to_remove.append(obj)
        elif kind == "coma" and owner not in valid_coma_ids:
            to_remove.append(obj)

    removed = 0
    for obj in to_remove:
        try:
            mesh_data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
            # Mesh datablock も他に user がなければ掃除 (orphan 残置防止)
            if mesh_data is not None and mesh_data.users == 0:
                try:
                    bpy.data.meshes.remove(mesh_data)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            _logger.exception("remove orphan mask failed: %s", obj.name)
    return removed
