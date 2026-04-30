"""Phase 6: B-Name 階層整合性 修復 operator.

計画書 Phase 6 完了条件「修復 operator」を提供。

責務:
    - ルート / outside / 各ページ / 各コマ Collection を mirror_work_to_outliner
      経由で再生成
    - bname_managed=True だが bname_id が空の Object を警告
    - bname_id が重複する Object を警告
    - 全 mask Object を再生成
"""

from __future__ import annotations

import uuid

import bpy

from ..utils import layer_object_sync as los
from ..utils import log
from ..utils import mask_object as mask_obj
from ..utils import object_naming as on

_logger = log.get_logger(__name__)


class BNAME_OT_repair_hierarchy(bpy.types.Operator):
    bl_idname = "bname.repair_hierarchy"
    bl_label = "B-Name 階層を修復"
    bl_description = (
        "Outliner Collection 階層を再生成し、bname_id 空白/重複を修正し、"
        "マスク Object を再生成します (Phase 6)。"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work

        work = get_work(context)
        return bool(work and getattr(work, "loaded", False))

    def execute(self, context):
        from ..core.work import get_work

        scene = context.scene
        work = get_work(context)

        # 1. mirror 再走 (失敗したら ERROR で打ち切り、二次破壊を防ぐ)
        try:
            los.mirror_work_to_outliner(scene, work)
        except Exception as exc:
            _logger.exception("repair: mirror failed")
            self.report({"ERROR"}, f"mirror 失敗: {exc}")
            return {"CANCELLED"}

        # 2. bname_id 空白を新 uuid で修復
        empty_fixed = 0
        for obj in bpy.data.objects:
            if not on.is_managed(obj):
                continue
            if not on.get_bname_id(obj):
                kind = on.get_kind(obj) or "unknown"
                obj[on.PROP_ID] = f"{kind}_repaired_{uuid.uuid4().hex[:8]}"
                empty_fixed += 1

        # 3. bname_id 重複を片方降格 (managed=False)
        seen: dict[str, bpy.types.Object] = {}
        dup_demoted = 0
        for obj in bpy.data.objects:
            if not on.is_managed(obj):
                continue
            bid = on.get_bname_id(obj)
            if not bid:
                continue
            if bid in seen:
                # 後発の方を managed=False に降格 (ID は維持: ユーザー追跡可能)
                obj[on.PROP_MANAGED] = False
                dup_demoted += 1
                _logger.warning(
                    "repair: bname_id %s 重複 → %s を managed=False へ降格",
                    bid, obj.name,
                )
            else:
                seen[bid] = obj

        # 4. 全マスク再生成 + orphan 削除 + 全レイヤーへ mask 適用 (Edit Mode は skip)
        if context.mode == "OBJECT":
            mask_result = mask_obj.regenerate_all_masks(scene, work)
            orphan_removed = mask_obj.remove_orphan_masks(scene, work)
            from ..utils import mask_apply

            mask_apply.apply_masks_to_all_managed(scene)
        else:
            mask_result = {"page_masks": 0, "coma_masks": 0}
            orphan_removed = 0
            self.report({"WARNING"}, "Edit Mode のためマスク再生成 skip")

        # 5. snapshot を最新化 (clear ではなく現状で再収集して重い再同期を避ける)
        try:
            los.clear_snapshots()
            for obj in on.iter_managed_objects():
                los.update_snapshot(obj)
        except Exception:
            pass

        msg = (
            f"修復: empty_fixed={empty_fixed}, dup_demoted={dup_demoted}, "
            f"page_mask={mask_result['page_masks']}, coma_mask={mask_result['coma_masks']}, "
            f"orphan_removed={orphan_removed}"
        )
        self.report({"INFO"}, msg)
        return {"FINISHED"}


_CLASSES = (BNAME_OT_repair_hierarchy,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
