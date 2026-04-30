"""Phase 6: Outliner 中心レイヤー操作パネル.

計画書 Phase 6 完了条件「B-Name パネルに作成/削除/前面/背面/詳細/修復
ボタンを配置」「通常のレイヤー親子管理は Outliner で完結する」を提供。

既存の ``BNAME_PT_layer_stack`` (UIList ベース) は残置 (Phase 6 保守的範囲)。
ユーザーは新パネルから Outliner 操作中心のフローへ移行できる。
"""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_work

B_NAME_CATEGORY = "B-Name"


class BNAME_PT_outliner_layers(Panel):
    """Outliner ベースのレイヤー操作パネル (Phase 6)."""

    bl_idname = "BNAME_PT_outliner_layers"
    bl_label = "Outliner レイヤー"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 13  # BNAME_PT_layer_stack (12) の直下

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        if not (work and getattr(work, "loaded", False)):
            return False
        # コマ編集モードでは作品レベルレイヤーを作らない
        return get_mode(context) != MODE_COMA

    def draw(self, context):
        layout = self.layout

        # Outliner 表示切替
        box = layout.box()
        box.label(text="Outliner 表示", icon="OUTLINER")
        row = box.row(align=True)
        row.operator("bname.outliner_apply_view", text="B-Name 表示へ", icon="VIS_SEL_11")
        row.operator("bname.outliner_restore_view", text="復元", icon="LOOP_BACK")

        # 作成
        box = layout.box()
        box.label(text="新規レイヤー作成", icon="ADD")
        col = box.column(align=True)
        col.operator("bname.gp_layer_create_per_object", icon="GREASEPENCIL")
        col.operator("bname.image_layers_all_to_object", icon="IMAGE_DATA")
        col.operator("bname.balloons_all_to_object", icon="MESH_CIRCLE")
        col.operator("bname.texts_all_to_object", icon="FONT_DATA")
        col.operator("bname.effect_line_create_object", icon="LIGHT")

        # 移行 (master GP / 効果線)
        box = layout.box()
        box.label(text="既存データ移行", icon="MODIFIER")
        # 破壊的操作警告
        warn = box.row()
        warn.alert = True
        warn.label(
            text="先に dry-run で計画を確認してから実行してください",
            icon="ERROR",
        )
        col = box.column(align=True)
        row = col.row(align=True)
        row.operator(
            "bname.gp_layer_migrate_master_dryrun", text="GP dry-run"
        )
        # confirm=True を operator props に渡しつつ、UI で警告済を明示
        row.operator("bname.gp_layer_migrate_master", text="GP 実行").confirm = True
        row = col.row(align=True)
        row.operator(
            "bname.effect_line_migrate_master_dryrun", text="効果線 dry-run"
        )
        row.operator(
            "bname.effect_line_migrate_master", text="効果線 実行"
        ).confirm = True

        # マスク
        box = layout.box()
        box.label(text="マスク", icon="MOD_MASK")
        col = box.column(align=True)
        col.operator("bname.mask_regenerate_all", icon="FILE_REFRESH")
        col.operator("bname.mask_remove_orphans", icon="TRASH")

        # 整合性修復
        box = layout.box()
        box.label(text="整合性", icon="CHECKMARK")
        col = box.column(align=True)
        col.operator("bname.repair_hierarchy", icon="MODIFIER_DATA")
        col.operator(
            "bname.coma_renumber_active_page", icon="LINENUMBERS_ON"
        )


_CLASSES = (BNAME_PT_outliner_layers,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
