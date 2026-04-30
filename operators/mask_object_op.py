"""Phase 5: ページ/コママスク Object 一括 operator."""

from __future__ import annotations

import bpy

from ..utils import log
from ..utils import mask_object as mask_obj

_logger = log.get_logger(__name__)


class BNAME_OT_mask_regenerate_all(bpy.types.Operator):
    bl_idname = "bname.mask_regenerate_all"
    bl_label = "全マスクを再生成"
    bl_description = (
        "全ページ・全コマのマスク Mesh Object を再生成します。形状は "
        "B-Name のページ/コマデータから派生します (Phase 5)。"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work

        work = get_work(context)
        # mask Mesh 再生成は Object Mode でのみ安全
        return bool(
            work
            and getattr(work, "loaded", False)
            and getattr(context, "mode", "OBJECT") == "OBJECT"
        )

    def execute(self, context):
        from ..core.work import get_work

        if context.mode != "OBJECT":
            self.report({"WARNING"}, "Object Mode で実行してください")
            return {"CANCELLED"}
        work = get_work(context)
        scene = context.scene
        result = mask_obj.regenerate_all_masks(scene, work)
        removed = mask_obj.remove_orphan_masks(scene, work)
        # 全レイヤーへマスクを適用 (枠外を視覚的に切抜き)
        from ..utils import mask_apply

        applied = mask_apply.apply_masks_to_all_managed(scene)
        self.report(
            {"INFO"},
            f"page mask {result['page_masks']} 再生成 / coma mask {result['coma_masks']} 再生成、"
            f"orphan {removed} 削除、{applied} レイヤーに適用",
        )
        return {"FINISHED"}


class BNAME_OT_mask_remove_orphans(bpy.types.Operator):
    bl_idname = "bname.mask_remove_orphans"
    bl_label = "孤立マスクを削除"
    bl_description = "対応する page/coma が消えた mask Object を削除します。"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work

        work = get_work(context)
        return bool(work and getattr(work, "loaded", False))

    def execute(self, context):
        from ..core.work import get_work

        work = get_work(context)
        removed = mask_obj.remove_orphan_masks(context.scene, work)
        self.report({"INFO"}, f"orphan mask {removed} 個を削除しました")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_mask_regenerate_all,
    BNAME_OT_mask_remove_orphans,
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
