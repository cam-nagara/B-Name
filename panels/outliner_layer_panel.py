"""Outliner 中心レイヤー操作パネル."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_work

B_NAME_CATEGORY = "B-Name"


class BNAME_PT_outliner_layers(Panel):
    """Outliner ベースのレイヤー操作パネル."""

    bl_idname = "BNAME_PT_outliner_layers"
    bl_label = "Outliner レイヤー"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 12

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        if not (work and getattr(work, "loaded", False)):
            return False
        return get_mode(context) != MODE_COMA

    def draw(self, context):
        layout = self.layout

        # Outliner 表示切替
        box = layout.box()
        box.label(text="Outliner 表示", icon="OUTLINER")
        row = box.row(align=True)
        row.operator("bname.outliner_apply_view", text="B-Name 表示へ", icon="VIS_SEL_11")
        row.operator("bname.outliner_restore_view", text="復元", icon="LOOP_BACK")

        # 新規レイヤー作成
        box = layout.box()
        box.label(text="新規レイヤー作成", icon="ADD")
        col = box.column(align=True)
        col.operator("bname.gp_layer_create_per_object", icon="GREASEPENCIL")
        col.operator("bname.effect_line_create_object", icon="LIGHT")
        col.operator("bname.balloons_to_curve_all", icon="MESH_CIRCLE")
        col.operator("bname.texts_to_plane_all", icon="FONT_DATA")

        # オーバーレイ表示切替 (Phase 3c)
        box = layout.box()
        scene = context.scene
        enabled = bool(getattr(scene, "bname_overlay_enabled", True))
        box.label(text="オーバーレイ表示", icon="OVERLAY")
        row = box.row()
        row.operator(
            "bname.overlay_toggle",
            text="ON" if enabled else "OFF",
            icon="HIDE_OFF" if enabled else "HIDE_ON",
            depress=enabled,
        )

        # マスク
        box = layout.box()
        box.label(text="マスク", icon="MOD_MASK")
        col = box.column(align=True)
        col.operator("bname.mask_regenerate_all", icon="FILE_REFRESH")
        col.operator("bname.mask_remove_orphans", icon="TRASH")

        # 整合性
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
