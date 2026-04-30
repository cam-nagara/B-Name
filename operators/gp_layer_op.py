"""1 GP Object = 1 B-Name レイヤー operators.

提供 operator:
    - ``bname.gp_layer_create_per_object``: 現在のアクティブコマ直下に新規
      GP Object を生成し、Outliner Collection 階層に正規 link する。
    - ``bname.gp_layer_draw_enter`` / ``bname.gp_layer_draw_exit``: 選択中
      の GP Object を Paint モード (PAINT_GREASE_PENCIL) へ切替/退出。
"""

from __future__ import annotations

import uuid

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Operator

from ..utils import gp_object_layer as gpol
from ..utils import log
from ..utils import object_naming as on

_logger = log.get_logger(__name__)


def _make_gp_bname_id() -> str:
    """``gp_<uuid12>`` 形式の安定 ID を生成 (衝突時はフル UUID へ拡張)."""
    for _ in range(10):
        candidate = f"gp_{uuid.uuid4().hex[:12]}"
        if on.find_object_by_bname_id(candidate, kind="gp") is None:
            return candidate
    # 10 回連続衝突 (天文学的低確率) はフル uuid hex で再試行
    full = f"gp_{uuid.uuid4().hex}"
    if on.find_object_by_bname_id(full, kind="gp") is None:
        return full
    raise RuntimeError("gp bname_id 生成に失敗しました (UUID 衝突)")


def _resolve_active_coma(context):
    """アクティブページとコマを (page_id, coma_id, title) で返す.

    優先順位:
        1. ``scene.bname_current_coma_id`` が設定されていれば、そのコマ ID を
           採用 (cNN.blend 編集中などコマ単独編集モード)。
        2. ``page.active_coma_index`` (BNamePageEntry の IntProperty) を使う。
        3. それ以外は最初のコマ (0 番目)。

    アクティブコマが特定できない場合 ``coma_id=None`` を返し、呼出側で
    ページ直下を採用するか警告するかを判断する。
    """
    scene = getattr(context, "scene", None)
    if scene is None:
        return None, None, None
    work = getattr(scene, "bname_work", None)
    if work is None or not getattr(work, "loaded", False):
        return None, None, None
    pages = getattr(work, "pages", None)
    if not pages:
        return None, None, None
    idx = int(getattr(work, "active_page_index", 0))
    if not (0 <= idx < len(pages)):
        return None, None, None
    page = pages[idx]
    page_id = str(getattr(page, "id", "") or "")
    comas = getattr(page, "comas", None)
    if not comas:
        return page_id, None, None

    # 1. scene.bname_current_coma_id (cNN.blend 編集中) を最優先
    current_coma_id = str(getattr(scene, "bname_current_coma_id", "") or "")
    if current_coma_id:
        for coma in comas:
            if str(getattr(coma, "id", "") or "") == current_coma_id:
                title = str(getattr(coma, "title", "") or current_coma_id)
                return page_id, current_coma_id, title

    # 2. page.active_coma_index (BNamePageEntry.active_coma_index)
    coma_idx = int(getattr(page, "active_coma_index", 0))
    if not (0 <= coma_idx < len(comas)):
        coma_idx = 0
    coma = comas[coma_idx]
    coma_id = str(getattr(coma, "id", "") or "")
    coma_title = str(getattr(coma, "title", "") or coma_id)
    return page_id, coma_id, coma_title


class BNAME_OT_gp_layer_create_per_object(bpy.types.Operator):
    """新規 GP Object を 1 レイヤーとしてアクティブコマに作成 (Phase 2)."""

    bl_idname = "bname.gp_layer_create_per_object"
    bl_label = "新 GP レイヤーを作成"
    bl_description = (
        "アクティブコマ直下に新規 GP Object を生成し、B-Name 安定 ID を付与"
        "して Outliner 階層に登録します (Phase 2: 1 GP Object = 1 B-Name "
        "レイヤー モデル)。"
    )
    bl_options = {"REGISTER", "UNDO"}

    title: StringProperty(  # type: ignore[valid-type]
        name="表示名",
        default="新規GPレイヤー",
    )
    z_index: IntProperty(  # type: ignore[valid-type]
        name="z_index",
        default=100,
        min=0,
    )

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        work = getattr(scene, "bname_work", None) if scene is not None else None
        return bool(work and getattr(work, "loaded", False))

    def execute(self, context):
        scene = context.scene
        from ..utils import active_target as _at

        parent_kind, parent_key, page = _at.resolve_active_target(context)
        if page is None:
            self.report({"WARNING"}, "アクティブページが見つかりません")
            return {"CANCELLED"}
        bname_id = _make_gp_bname_id()
        obj = gpol.create_layer_gp_object(
            scene=scene,
            bname_id=bname_id,
            title=self.title,
            z_index=int(self.z_index),
            parent_kind=parent_kind,
            parent_key=parent_key,
        )
        if obj is None:
            self.report({"ERROR"}, "GP Object 生成に失敗しました")
            return {"CANCELLED"}
        # アクティブ選択にも反映
        try:
            for o in bpy.data.objects:
                o.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
        except Exception:  # noqa: BLE001
            pass
        self.report({"INFO"}, f"GP レイヤー Object 生成: {obj.name}")
        return {"FINISHED"}


def _resolve_active_gp_object(context):
    """アクティブな GP Object (kind=gp) を解決する.

    優先順位:
        1. ``context.view_layer.objects.active`` が GP Object かつ
           ``bname_kind == "gp"`` ならそれ。
        2. ``context.selected_objects`` の中で B-Name 管理 GP Object 1 つ目。
        3. ``scene.bname_active_layer_kind == "gp"`` の場合のみ、最初に
           見つかった B-Name 管理 GP Object (フォールバック)。
    見つからなければ None。
    """
    def _is_bname_gp(obj):
        return (
            obj is not None
            and getattr(obj, "type", "") == "GREASEPENCIL"
            and bool(obj.get("bname_managed", False))
            and str(obj.get("bname_kind", "")) == "gp"
        )

    view_layer = getattr(context, "view_layer", None)
    if view_layer is not None:
        active = getattr(view_layer.objects, "active", None)
        if _is_bname_gp(active):
            return active

    for sel in tuple(getattr(context, "selected_objects", []) or []):
        if _is_bname_gp(sel):
            return sel

    scene = getattr(context, "scene", None)
    if scene is not None and getattr(scene, "bname_active_layer_kind", "") == "gp":
        for obj in bpy.data.objects:
            if _is_bname_gp(obj):
                return obj
    return None


class BNAME_OT_gp_layer_draw_enter(Operator):
    """選択中の GP レイヤーを描画モード (PAINT_GREASE_PENCIL) へ切替."""

    bl_idname = "bname.gp_layer_draw_enter"
    bl_label = "GP 描画モードへ"
    bl_description = (
        "選択中の B-Name GP レイヤーを描画モード (PAINT_GREASE_PENCIL) に"
        "切替えます。3D ビューを Material Preview に切替えて即座に描画内容を"
        "確認できる状態にします。"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        work = getattr(scene, "bname_work", None) if scene is not None else None
        return bool(work and getattr(work, "loaded", False))

    def execute(self, context):
        obj = _resolve_active_gp_object(context)
        if obj is None:
            self.report({"WARNING"}, "GP レイヤーを選択してください")
            return {"CANCELLED"}
        # 他選択を解除して GP Object を active にする
        try:
            for selected in tuple(getattr(context, "selected_objects", []) or []):
                if selected is not obj:
                    selected.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
        except Exception:  # noqa: BLE001
            _logger.exception("gp_layer_draw_enter: 選択切替失敗")
        # 既存モーダル (コマカット等) を片付ける
        try:
            from . import coma_modal_state

            coma_modal_state.finish_all(context)
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(obj, "mode", "OBJECT") != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:  # noqa: BLE001
            pass
        try:
            bpy.ops.object.mode_set(mode="PAINT_GREASE_PENCIL")
        except Exception as exc:  # noqa: BLE001
            self.report({"WARNING"}, f"描画モードへ切替できません: {exc}")
            return {"CANCELLED"}
        # 3D ビューを Material Preview に切替えて GP の塗りも見えるようにする
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
        try:
            context.scene.bname_active_layer_kind = "gp"
        except Exception:  # noqa: BLE001
            pass
        return {"FINISHED"}


class BNAME_OT_gp_layer_draw_exit(Operator):
    """GP 描画モードから Object モードへ戻る."""

    bl_idname = "bname.gp_layer_draw_exit"
    bl_label = "GP 描画モードを終了"
    bl_options = {"REGISTER"}

    def execute(self, context):
        obj = getattr(context.view_layer.objects, "active", None)
        if obj is None:
            return {"CANCELLED"}
        try:
            if getattr(obj, "mode", "") != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception as exc:  # noqa: BLE001
            self.report({"WARNING"}, f"Object モードへ戻せません: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_gp_layer_create_per_object,
    BNAME_OT_gp_layer_draw_enter,
    BNAME_OT_gp_layer_draw_exit,
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
