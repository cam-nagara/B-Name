"""コマ/ページマスクをレイヤーに適用する.

`utils/mask_object.py` で生成した mask Mesh Object を実際にレイヤー
Object 側で参照して、コマ枠/ページ枠の外をクリップする。

実装方針:
    - Mesh 系レイヤー (raster / image plane / balloon plane / text plane):
      Boolean Modifier (Intersect, FLOAT solver) で実形状クリップ。
    - GP 系レイヤー (gp / effect): Blender 5.1 GP v3 では外部 Mesh Object
      をマスク source にする一般 Modifier が無いため、現状は no-op。
      Phase 5d で `__bname_mask` 内蔵 layer 方式で実装予定。

mask Object 自体は ``hide_render=True`` + ``hide_viewport=True`` で 3D ビュー
にも描画されない。Modifier の target として参照するだけなら hidden でも
有効。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import log
from . import mask_object as mo
from . import object_naming as on

_logger = log.get_logger(__name__)

MOD_NAME_COMA_MASK = "BName Coma Mask"
MOD_NAME_PAGE_MASK = "BName Page Mask"


def _resolve_coma_mask_object(parent_key: str) -> Optional[bpy.types.Object]:
    """parent_key (例 "p0001:c01") からコママスク Object を取得."""
    if not parent_key or ":" not in parent_key:
        return None
    page_id, coma_id = parent_key.split(":", 1)
    name = f"{mo.COMA_MASK_NAME_PREFIX}{page_id}_{coma_id}"
    return bpy.data.objects.get(name)


def _resolve_page_mask_object(parent_key: str) -> Optional[bpy.types.Object]:
    """parent_key (page_id) からページマスク Object を取得."""
    page_id = parent_key.split(":", 1)[0] if parent_key else ""
    if not page_id:
        return None
    name = f"{mo.PAGE_MASK_NAME_PREFIX}{page_id}"
    return bpy.data.objects.get(name)


def _ensure_boolean_intersect_modifier(
    obj: bpy.types.Object, mod_name: str, target: bpy.types.Object
) -> None:
    """Mesh / Curve Object に Boolean Intersect Modifier を ensure.

    Curve は Blender 5.1 では Boolean Modifier 非対応のため、Mesh のみ
    付与する。Curve のマスクは別経路 (overlay 側 scissor or shape 制御)。
    """
    if obj is None or target is None:
        return
    if obj.type != "MESH":
        return
    mod = obj.modifiers.get(mod_name)
    if mod is None:
        try:
            mod = obj.modifiers.new(name=mod_name, type="BOOLEAN")
        except Exception:  # noqa: BLE001
            _logger.exception("mask_apply: boolean modifier create failed")
            return
    try:
        mod.operation = "INTERSECT"
        # Blender 5.1 EEVEE Next では solver enum が変更され、 "FAST" は
        # 廃止されて "FLOAT" / "EXACT" / "MANIFOLD" に。"FLOAT" が旧 FAST
        # 相当の高速版なのでこれを採用。enum 値非対応で例外なら無視
        # (default solver で続行)。
        try:
            mod.solver = "FLOAT"
        except (TypeError, AttributeError):
            try:
                mod.solver = "FAST"
            except (TypeError, AttributeError):
                pass
        mod.object = target
        # 反映を確実にするため depsgraph を更新
        try:
            view_layer = bpy.context.view_layer
            if view_layer is not None:
                view_layer.update()
        except Exception:  # noqa: BLE001
            pass
        # 万一 object pointer が None のままなら再代入 + name 経由
        try:
            mod_re = obj.modifiers.get(mod_name)
            if mod_re is not None and getattr(mod_re, "object", None) is None:
                mod_re.object = target
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        _logger.exception("mask_apply: boolean modifier setup failed")


_GP_MASK_LAYER_NAME = "__bname_mask"


def _build_polygon_strokes_from_mesh(
    drawing, mesh_obj: bpy.types.Object, material_index: int = 0
) -> None:
    """``mesh_obj`` の各 Face を GP drawing に閉じ stroke として描き込む.

    マスク用の塗り潰しレイヤーに mask Mesh の形状を再現する。Blender 5.1 GP v3
    の ``GreasePencilDrawing.strokes`` API を使う。
    """
    if drawing is None or mesh_obj is None:
        return
    mesh = getattr(mesh_obj, "data", None)
    if mesh is None or len(mesh.vertices) == 0:
        return
    # mesh ローカル座標 → GP world 座標 (mesh_obj の transform を考慮)
    matrix_world = mesh_obj.matrix_world
    # 既存 strokes をクリア (再生成のたびに前回 stroke を捨てる)
    try:
        if hasattr(drawing, "strokes"):
            n = len(drawing.strokes)
            for _ in range(n):
                try:
                    drawing.remove(drawing.strokes[0])
                except Exception:  # noqa: BLE001
                    break
    except Exception:  # noqa: BLE001
        pass
    # 各 Face を 1 stroke として追加
    try:
        from . import gpencil as gp_utils

        for face in mesh.polygons:
            verts = [matrix_world @ mesh.vertices[v].co for v in face.vertices]
            # 閉じ stroke にするため最初の点を末尾にも追加
            points = [(v.x, v.y, v.z) for v in verts]
            if len(points) < 3:
                continue
            points.append(points[0])
            try:
                gp_utils.add_stroke_to_drawing(
                    drawing, points,
                    material_index=material_index,
                    cyclic=True,
                )
            except Exception:  # noqa: BLE001
                _logger.exception("GP mask stroke add failed")
    except Exception:  # noqa: BLE001
        _logger.exception("GP mask polygon→stroke failed")


def _ensure_gp_fill_material(obj) -> int:
    """マスク塗り潰し用の Fill-only マテリアルを ensure し slot index を返す."""
    if obj is None or getattr(obj, "type", "") != "GREASEPENCIL":
        return 0
    name = "BName_Mask_Fill"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
        try:
            bpy.data.materials.create_gpencil_data(mat)
        except (AttributeError, RuntimeError):
            pass
    gp_style = getattr(mat, "grease_pencil", None)
    if gp_style is not None:
        try:
            gp_style.show_stroke = False
            gp_style.show_fill = True
            gp_style.fill_color = (1.0, 1.0, 1.0, 1.0)
        except Exception:  # noqa: BLE001
            pass
    # slot 確保
    try:
        existing_names = [m.name for m in obj.data.materials if m is not None]
        if mat.name not in existing_names:
            obj.data.materials.append(mat)
            existing_names.append(mat.name)
        return existing_names.index(mat.name)
    except Exception:  # noqa: BLE001
        return 0


def _ensure_gp_internal_mask(
    obj: bpy.types.Object, target: bpy.types.Object
) -> None:
    """GP Object に ``__bname_mask`` 内蔵レイヤーを生成し、target Mesh の
    形状をその layer の stroke として描いて、コンテンツレイヤーから mask 参照
    する (Blender 5.1 GP v3 の `GreasePencilLayer.use_masks` + `mask_layers`).
    """
    if obj is None or target is None:
        return
    if getattr(obj, "type", "") != "GREASEPENCIL":
        return
    gp_data = obj.data
    if gp_data is None:
        return
    layers = getattr(gp_data, "layers", None)
    if layers is None:
        return

    from . import gpencil as gp_utils

    # マスクレイヤー ensure
    mask_layer = layers.get(_GP_MASK_LAYER_NAME)
    if mask_layer is None:
        try:
            mask_layer = gp_utils.ensure_layer(gp_data, _GP_MASK_LAYER_NAME)
        except Exception:  # noqa: BLE001
            _logger.exception("GP __bname_mask layer create failed")
            return

    # マスクレイヤーは描画上は非表示 (stroke 自体は配置するが、mask 専用として
    # use_masks 参照される側はレイヤー自身の hide で消せる)
    try:
        mask_layer.hide = True
    except Exception:  # noqa: BLE001
        pass

    # 塗り潰し material slot を確保し、index を取得
    mat_index = _ensure_gp_fill_material(obj)

    # 現在のシーンフレームに対するフレームを ensure
    try:
        frame_num = bpy.context.scene.frame_current if bpy.context.scene else 1
        gp_utils.ensure_active_frame(mask_layer, frame_number=frame_num)
    except Exception:  # noqa: BLE001
        _logger.exception("GP mask frame ensure failed")
        return

    # 現在フレームに stroke を再生成
    try:
        frame = mask_layer.frames[0] if len(mask_layer.frames) else None
        drawing = getattr(frame, "drawing", None) if frame else None
        if drawing is not None:
            _build_polygon_strokes_from_mesh(drawing, target, material_index=mat_index)
    except Exception:  # noqa: BLE001
        _logger.exception("GP mask drawing build failed")

    # 全コンテンツレイヤー (= __bname_mask 以外) で use_masks を有効にし、
    # mask_layers コレクションに mask layer を登録する
    for layer in layers:
        if getattr(layer, "name", "") == _GP_MASK_LAYER_NAME:
            continue
        try:
            layer.use_masks = True
        except Exception:  # noqa: BLE001
            pass
        try:
            mask_coll = getattr(layer, "mask_layers", None)
            if mask_coll is None:
                continue
            # 既登録なら no-op
            already = False
            try:
                for ml in mask_coll:
                    if getattr(ml, "name", "") == _GP_MASK_LAYER_NAME:
                        already = True
                        break
            except Exception:  # noqa: BLE001
                pass
            if not already:
                try:
                    mask_coll.add(_GP_MASK_LAYER_NAME)
                except Exception:  # noqa: BLE001
                    # API バリエーション fallback: name を直接渡す形と Layer
                    # オブジェクトを渡す形があり得る
                    try:
                        mask_coll.add(mask_layer)
                    except Exception:  # noqa: BLE001
                        _logger.exception("GP mask_layers add failed")
        except Exception:  # noqa: BLE001
            _logger.exception("GP layer mask setup failed")


def _remove_gp_internal_mask(obj: bpy.types.Object) -> None:
    """GP Object から ``__bname_mask`` 内蔵レイヤーと参照を取り除く."""
    if obj is None or getattr(obj, "type", "") != "GREASEPENCIL":
        return
    gp_data = obj.data
    if gp_data is None:
        return
    layers = getattr(gp_data, "layers", None)
    if layers is None:
        return
    # 各コンテンツレイヤーの mask_layers から __bname_mask を外す
    for layer in layers:
        if getattr(layer, "name", "") == _GP_MASK_LAYER_NAME:
            continue
        mask_coll = getattr(layer, "mask_layers", None)
        if mask_coll is None:
            continue
        try:
            to_remove = []
            for ml in mask_coll:
                if getattr(ml, "name", "") == _GP_MASK_LAYER_NAME:
                    to_remove.append(ml)
            for ml in to_remove:
                try:
                    mask_coll.remove(ml)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
    # __bname_mask layer 自体を削除
    mask_layer = layers.get(_GP_MASK_LAYER_NAME)
    if mask_layer is not None:
        try:
            layers.remove(mask_layer)
        except Exception:  # noqa: BLE001
            pass


def _ensure_gp_mask_modifier(
    obj: bpy.types.Object, mod_name: str, target: bpy.types.Object
) -> None:
    """GP Object のマスク適用 (Phase 5d: 内蔵 layer mask 方式).

    Blender 5.1 GP v3 では ``GreasePencilLayer.use_masks`` と
    ``mask_layers`` を使う。同じ GP Object 内のマスクレイヤーを参照する
    仕組みなので、target Mesh の形状を ``__bname_mask`` レイヤーの stroke
    として描き写してから mask 参照を立てる。
    """
    if obj is None:
        return
    if getattr(obj, "type", "") != "GREASEPENCIL":
        return
    if target is None:
        _remove_gp_internal_mask(obj)
        return
    _ensure_gp_internal_mask(obj, target)


def _remove_modifier_if_present(obj: bpy.types.Object, mod_name: str) -> None:
    if obj is None:
        return
    mod = obj.modifiers.get(mod_name)
    if mod is None:
        return
    try:
        obj.modifiers.remove(mod)
    except Exception:  # noqa: BLE001
        pass


def apply_mask_to_layer_object(obj: bpy.types.Object) -> None:
    """1 つのレイヤー Object にコマ/ページマスクを適用する.

    parent_key を見て:
        - "<page>:<coma>" 形式 → コママスク Modifier を ensure (ページマスクは外す)
        - "<page>" 形式 → ページマスク Modifier を ensure (コママスクは外す)
        - 空 / outside → どちらも外す

    対応するマスク Object がまだ生成されていない場合は何もせず黙って return。
    後で ``regenerate_all_masks`` + ``apply_masks_to_all_managed`` を呼べば
    回復する。
    """
    if obj is None or not on.is_managed(obj):
        return
    parent_key = str(obj.get(on.PROP_PARENT_KEY, "") or "")
    coma_target = _resolve_coma_mask_object(parent_key)
    page_target = _resolve_page_mask_object(parent_key)

    obj_type = getattr(obj, "type", "")
    if obj_type == "MESH":
        if ":" in parent_key:
            # コマ配下: コマスマスクのみ適用
            if coma_target is not None:
                _ensure_boolean_intersect_modifier(obj, MOD_NAME_COMA_MASK, coma_target)
            else:
                _remove_modifier_if_present(obj, MOD_NAME_COMA_MASK)
            _remove_modifier_if_present(obj, MOD_NAME_PAGE_MASK)
        elif parent_key:
            # ページ直下: ページマスクのみ適用
            if page_target is not None:
                _ensure_boolean_intersect_modifier(obj, MOD_NAME_PAGE_MASK, page_target)
            else:
                _remove_modifier_if_present(obj, MOD_NAME_PAGE_MASK)
            _remove_modifier_if_present(obj, MOD_NAME_COMA_MASK)
        else:
            # outside / 空 parent: どちらも外す
            _remove_modifier_if_present(obj, MOD_NAME_COMA_MASK)
            _remove_modifier_if_present(obj, MOD_NAME_PAGE_MASK)
    elif obj_type == "GREASEPENCIL":
        # GP は Phase 5d で実装。現状は modifier クリーンアップのみ。
        _ensure_gp_mask_modifier(obj, MOD_NAME_COMA_MASK, coma_target)
        _ensure_gp_mask_modifier(obj, MOD_NAME_PAGE_MASK, page_target)


def apply_masks_to_all_managed(scene: bpy.types.Scene) -> int:
    """全 B-Name 管理 Object にマスクを適用する。適用件数を返す."""
    if scene is None:
        return 0
    n = 0
    for obj in on.iter_managed_objects():
        apply_mask_to_layer_object(obj)
        n += 1
    return n
