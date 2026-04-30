"""効果線 Object 化 operators (Phase 5b)."""

from __future__ import annotations

import uuid

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from ..utils import effect_line_object as elo
from ..utils import log
from ..utils import object_naming as on

_logger = log.get_logger(__name__)


def _make_effect_bname_id() -> str:
    for _ in range(10):
        candidate = f"effect_{uuid.uuid4().hex[:12]}"
        if on.find_object_by_bname_id(candidate, kind="effect") is None:
            return candidate
    full = f"effect_{uuid.uuid4().hex}"
    if on.find_object_by_bname_id(full, kind="effect") is None:
        return full
    raise RuntimeError("effect bname_id 生成に失敗しました (UUID 衝突)")


def _resolve_active_coma(context):
    """gp_layer_op._resolve_active_coma と同じ優先度でアクティブコマを解決.

    優先順位: scene.bname_current_coma_id > page.active_coma_index > 0。
    """
    scene = getattr(context, "scene", None)
    if scene is None:
        return None, None
    work = getattr(scene, "bname_work", None)
    if work is None or not getattr(work, "loaded", False):
        return None, None
    pages = getattr(work, "pages", None)
    if not pages:
        return None, None
    idx = int(getattr(work, "active_page_index", 0))
    if not (0 <= idx < len(pages)):
        return None, None
    page = pages[idx]
    page_id = str(getattr(page, "id", "") or "")
    comas = getattr(page, "comas", None)
    if not comas:
        return page_id, None

    # 1. scene.bname_current_coma_id (cNN.blend 編集中) 最優先
    current_coma_id = str(getattr(scene, "bname_current_coma_id", "") or "")
    if current_coma_id:
        for coma in comas:
            if str(getattr(coma, "id", "") or "") == current_coma_id:
                return page_id, current_coma_id

    # 2. page.active_coma_index
    coma_idx = int(getattr(page, "active_coma_index", 0))
    if not (0 <= coma_idx < len(comas)):
        coma_idx = 0
    return page_id, str(getattr(comas[coma_idx], "id", "") or "")


class BNAME_OT_effect_line_create_object(bpy.types.Operator):
    """新規効果線 GP Object を作成."""

    bl_idname = "bname.effect_line_create_object"
    bl_label = "新 効果線レイヤーを Object として作成"
    bl_description = (
        "アクティブコマ直下に新 effect GP Object を生成し、Outliner 階層に登録"
        "します (Phase 5b)。"
    )
    bl_options = {"REGISTER", "UNDO"}

    title: StringProperty(name="表示名", default="新規効果線")  # type: ignore[valid-type]
    z_index: IntProperty(name="z_index", default=200, min=0)  # type: ignore[valid-type]
    target_ref: StringProperty(name="参照対象", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        work = getattr(scene, "bname_work", None) if scene is not None else None
        return bool(work and getattr(work, "loaded", False))

    def execute(self, context):
        scene = context.scene
        page_id, coma_id = _resolve_active_coma(context)
        if not page_id:
            self.report({"WARNING"}, "アクティブページが見つかりません")
            return {"CANCELLED"}
        if coma_id:
            parent_kind, parent_key = "coma", f"{page_id}:{coma_id}"
        else:
            parent_kind, parent_key = "page", page_id
        bname_id = _make_effect_bname_id()
        obj = elo.create_effect_line_object(
            scene=scene,
            bname_id=bname_id,
            title=self.title,
            z_index=int(self.z_index),
            parent_kind=parent_kind,
            parent_key=parent_key,
            target_ref=self.target_ref,
        )
        if obj is None:
            self.report({"ERROR"}, "効果線 Object 生成失敗")
            return {"CANCELLED"}
        try:
            for o in bpy.data.objects:
                o.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
        except Exception:  # noqa: BLE001
            pass
        self.report({"INFO"}, f"効果線 Object 生成: {obj.name}")
        return {"FINISHED"}


class BNAME_OT_effect_line_migrate_master_dryrun(bpy.types.Operator):
    bl_idname = "bname.effect_line_migrate_master_dryrun"
    bl_label = "master 効果線移行プラン (dry-run)"
    bl_description = (
        "BName_EffectLines に含まれる layer を Object 化したときの計画を表示。"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        page_id, coma_id = _resolve_active_coma(context)
        if not page_id or not coma_id:
            self.report({"WARNING"}, "アクティブコマを選択してください")
            return {"CANCELLED"}
        plan = elo.migrate_master_effect_lines_to_objects(
            scene=context.scene,
            parent_kind="coma",
            parent_key=f"{page_id}:{coma_id}",
            dry_run=True,
        )
        self.report(
            {"INFO"},
            f"移行対象 {len(plan['would_migrate'])}, スキップ {len(plan['skipped'])} (dry-run)",
        )
        _logger.info("effect line migrate dry-run: %s", plan)
        return {"FINISHED"}


class BNAME_OT_effect_line_migrate_master(bpy.types.Operator):
    bl_idname = "bname.effect_line_migrate_master"
    bl_label = "master 効果線を Object 群へ移行"
    bl_description = "BName_EffectLines の各 layer を新 GP Object 群へ展開 (元 layer は残置)。"
    bl_options = {"REGISTER", "UNDO"}

    confirm: BoolProperty(name="確認済み", default=False)  # type: ignore[valid-type]

    def execute(self, context):
        if not self.confirm:
            self.report({"WARNING"}, "confirm=True で実行してください")
            return {"CANCELLED"}
        page_id, coma_id = _resolve_active_coma(context)
        if not page_id or not coma_id:
            self.report({"WARNING"}, "アクティブコマを選択してください")
            return {"CANCELLED"}
        plan = elo.migrate_master_effect_lines_to_objects(
            scene=context.scene,
            parent_kind="coma",
            parent_key=f"{page_id}:{coma_id}",
            dry_run=False,
        )
        self.report(
            {"INFO"},
            f"効果線 layer を {len(plan['migrated'])} 個の Object に移行 (skip {len(plan['skipped'])})",
        )
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_effect_line_create_object,
    BNAME_OT_effect_line_migrate_master_dryrun,
    BNAME_OT_effect_line_migrate_master,
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
