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
        "Outliner Collection 階層を再生成し、bname_id の整合性をチェックし、"
        "マスク Object を再生成します。問題があれば Info にレポートします (Phase 6)。"
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

        # 1. mirror 再走 (root / outside / page / coma / folder Collection)
        try:
            los.mirror_work_to_outliner(scene, work)
        except Exception:
            _logger.exception("repair: mirror failed")

        # 2. bname_id の整合性チェック
        empty_id_count = 0
        duplicate_ids: dict[str, int] = {}
        for obj in bpy.data.objects:
            if not on.is_managed(obj):
                continue
            bid = on.get_bname_id(obj)
            if not bid:
                empty_id_count += 1
                continue
            duplicate_ids[bid] = duplicate_ids.get(bid, 0) + 1
        dup_summary = [bid for bid, n in duplicate_ids.items() if n > 1]

        # 3. 全マスク再生成
        mask_result = mask_obj.regenerate_all_masks(scene, work)
        orphan_removed = mask_obj.remove_orphan_masks(scene, work)

        # 4. snapshot をクリアして watch を新規状態にする
        try:
            los.clear_snapshots()
        except Exception:
            pass

        msg = (
            f"修復: empty_id={empty_id_count}, dup_ids={len(dup_summary)}, "
            f"page_mask={mask_result['page_masks']}, coma_mask={mask_result['coma_masks']}, "
            f"orphan_removed={orphan_removed}"
        )
        if dup_summary:
            _logger.warning("repair: duplicate bname_ids: %s", dup_summary[:5])
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
