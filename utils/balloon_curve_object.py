"""フキダシ Curve Object ヘルパ (Phase 4c).

`utils/balloon_shapes.outline_for_entry` から得られる輪郭点列を Bezier Curve
として生成し、Outliner mirror に登録する。

Curve は ``bevel_depth`` で線幅を持たせ、``fill_mode="BOTH"`` で内側塗り
潰しを行う。Phase 4c では基本形状 (rect/ellipse/cloud/octagon 等) のみ対応。
尻尾 (tail) は後段で実装。
"""

from __future__ import annotations

from typing import Optional, Sequence

import bpy

from . import balloon_shapes as bs
from . import layer_object_sync as los
from . import log
from . import object_naming as on
from .geom import mm_to_m

_logger = log.get_logger(__name__)

BALLOON_CURVE_NAME_PREFIX = "balloon_"
BALLOON_CURVE_DATA_PREFIX = "balloon_curve_"


def _ensure_balloon_curve_data(
    name: str, points_mm: Sequence[tuple[float, float]]
) -> bpy.types.Curve:
    """点列 (mm) から Bezier Curve データブロックを ensure (再構築)."""
    curve = bpy.data.curves.get(name)
    if curve is None:
        curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "2D"
    # 既存スプライン全削除
    while len(curve.splines):
        try:
            curve.splines.remove(curve.splines[0])
        except Exception:  # noqa: BLE001
            break
    if not points_mm or len(points_mm) < 3:
        return curve
    spline = curve.splines.new(type="BEZIER")
    spline.bezier_points.add(len(points_mm) - 1)
    for i, (x_mm, y_mm) in enumerate(points_mm):
        bp = spline.bezier_points[i]
        bp.co = (mm_to_m(x_mm), mm_to_m(y_mm), 0.0)
        # ハンドルを AUTO にして自然な曲線
        bp.handle_left_type = "AUTO"
        bp.handle_right_type = "AUTO"
    spline.use_cyclic_u = True
    # フキダシ内側を塗り潰す (透明色は呼出側 material 任せ)
    try:
        curve.fill_mode = "BOTH"
    except Exception:  # noqa: BLE001
        pass
    # 線幅 (ベベル) は呼出側で設定。data 側のデフォルトは 0。
    return curve


def _ensure_balloon_curve_material(curve: bpy.types.Curve) -> bpy.types.Material:
    """フキダシ用の薄い material を ensure (黒線 + 白塗り、透明 mix)."""
    name = "BName_Balloon_Curve"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
    try:
        mat.use_nodes = True
        nt = mat.node_tree
        # 既存ノード全削除して再構築
        for n in list(nt.nodes):
            nt.nodes.remove(n)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (200, 0)
        principled = nt.nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (-100, 0)
        try:
            principled.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            principled.inputs["Alpha"].default_value = 1.0
        except Exception:  # noqa: BLE001
            pass
        nt.links.new(principled.outputs["BSDF"], out.inputs["Surface"])
        try:
            mat.blend_method = "BLEND"
        except (AttributeError, TypeError):
            pass
    except Exception:  # noqa: BLE001
        _logger.exception("balloon curve material setup failed")
    if not curve.materials:
        curve.materials.append(mat)
    elif curve.materials[0] is not mat:
        curve.materials[0] = mat
    return mat


def _outline_points_for_entry(entry) -> list[tuple[float, float]]:
    """entry から輪郭点列 (mm, ローカル左下 origin) を取得."""
    width = float(getattr(entry, "width_mm", 40.0) or 40.0)
    height = float(getattr(entry, "height_mm", 20.0) or 20.0)
    rect = bs.Rect(0.0, 0.0, width, height)
    try:
        pts = bs.outline_for_entry(entry, rect)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon outline_for_entry failed")
        pts = []
    if not pts or len(pts) < 3:
        # フォールバック: 矩形
        pts = [
            (0.0, 0.0),
            (width, 0.0),
            (width, height),
            (0.0, height),
        ]
    return pts


def ensure_balloon_curve_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    """``BNameBalloonEntry`` から balloon Curve Object を生成・更新する.

    Phase 4c: rect/ellipse/cloud/fluffy/thorn 等の Meldex 共通形状を Bezier
    Curve として描画する。尻尾 (tail) は後段で追加予定。
    """
    if scene is None or entry is None or page is None:
        return None
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None

    # 1. Curve データ生成
    points_mm = _outline_points_for_entry(entry)
    curve_data_name = f"{BALLOON_CURVE_DATA_PREFIX}{balloon_id}"
    curve_data = _ensure_balloon_curve_data(curve_data_name, points_mm)
    _ensure_balloon_curve_material(curve_data)
    # ベベルでフキダシの線幅を再現 (entry.line_width_mm)
    line_width_mm = float(getattr(entry, "line_width_mm", 0.6) or 0.6)
    try:
        curve_data.bevel_depth = mm_to_m(line_width_mm) * 0.5
        curve_data.bevel_resolution = 0
    except Exception:  # noqa: BLE001
        pass

    # 2. Curve Object 生成 or 再利用
    obj_name = f"{BALLOON_CURVE_NAME_PREFIX}{balloon_id}"
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, curve_data)
    else:
        # 既存 Object のデータを Curve に切替 (旧 Mesh balloon plane が
        # 残っているケースの自動移行)
        if obj.data is not curve_data:
            obj.data = curve_data

    # 3. ページローカル座標 mm → m
    obj.location.x = mm_to_m(float(getattr(entry, "x_mm", 0.0) or 0.0))
    obj.location.y = mm_to_m(float(getattr(entry, "y_mm", 0.0) or 0.0))

    # 4. parent 解決 (balloon_text_plane と同方針)
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

    # 5. z_index は page.balloons 配列 index に基づく (kind 別 base offset)
    BALLOON_Z_BASE = 1000
    z_index = BALLOON_Z_BASE
    balloons = getattr(page, "balloons", None)
    if balloons is not None:
        for i, e in enumerate(balloons):
            if str(getattr(e, "id", "") or "") == balloon_id:
                z_index = BALLOON_Z_BASE + (i + 1) * 10
                break

    los.stamp_layer_object(
        obj,
        kind="balloon",
        bname_id=balloon_id,
        title=str(getattr(entry, "title", "") or balloon_id),
        z_index=z_index,
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
