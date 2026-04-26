"""コマ編集モード用カメラ操作パネル."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..core.mode import MODE_PANEL, get_mode
from ..utils import panel_camera

B_NAME_CATEGORY = "B-Name"


def _settings(context):
    return getattr(context.scene, "bname_panel_camera_settings", None)


def _camera(context):
    cam = getattr(context.scene, "camera", None)
    return cam if cam is not None and getattr(cam, "type", "") == "CAMERA" else None


def _is_camera_view(context) -> bool:
    area = getattr(context, "area", None)
    space = getattr(context, "space_data", None)
    rv3d = getattr(space, "region_3d", None)
    if area is None or area.type != "VIEW_3D" or rv3d is None:
        return False
    return rv3d.view_perspective == "CAMERA"


def _draw_camera_settings(layout, context, cam) -> None:
    scene = context.scene
    row = layout.row()
    row.prop(scene, "camera", text="")

    if bool(getattr(scene, "bname_panel_camera_fisheye_layout_mode", False)):
        split = layout.split(factor=0.4)
        split.label(text="魚眼FOV")
        split.prop(cam.data, "fisheye_fov", text="")
    else:
        split = layout.split(factor=0.4)
        split.label(text="焦点距離")
        split.prop(cam.data, "lens", text="")

    box = layout.box()
    box.label(text="奥行き表示範囲")
    row = box.row(align=True)
    row.prop(cam.data, "clip_start", text="開始")
    row.prop(cam.data, "clip_end", text="終了")

    box = layout.box()
    box.label(text="カメラのシフト")
    row = box.row(align=True)
    row.prop(cam.data, "shift_x", text="X")
    row.prop(cam.data, "shift_y", text="Y")
    row = box.row()
    row.enabled = _is_camera_view(context)
    row.operator("bname.panel_camera_shift_drag", text="ビューで調整")

    split = layout.split(factor=0.4)
    split.label(text="カメラの回転")
    split.prop(cam, "rotation_euler", index=1, text="")


def _draw_angle_list(layout, context, settings) -> None:
    box = layout.box()
    box.label(text="カメラアングル一覧")
    row = box.row()
    row.template_list(
        "UI_UL_list",
        "bname_panel_camera_angles",
        settings,
        "camera_angles",
        settings,
        "camera_angles_index",
        rows=3,
    )
    col = row.column(align=True)
    col.operator("bname.panel_camera_angle_add", icon="ADD", text="")
    col.operator("bname.panel_camera_angle_remove", icon="REMOVE", text="")
    box.operator("bname.panel_camera_angle_apply", text="適用")


def _draw_resolution_settings(layout, context) -> None:
    scene = context.scene
    box = layout.box()
    box.label(text="出力解像度")
    row = box.row()
    row.template_list(
        "UI_UL_list",
        "bname_panel_camera_resolution",
        scene,
        "bname_panel_camera_resolution_settings",
        scene,
        "bname_panel_camera_resolution_settings_index",
        rows=2,
    )
    col = row.column(align=True)
    col.operator("bname.panel_camera_resolution_add", icon="ADD", text="")
    col.operator("bname.panel_camera_resolution_remove", icon="REMOVE", text="")
    coll = scene.bname_panel_camera_resolution_settings
    idx = int(scene.bname_panel_camera_resolution_settings_index)
    if 0 <= idx < len(coll):
        item = coll[idx]
        box.prop(item, "name")
        row = box.row(align=True)
        row.prop(item, "resolution_x")
        row.prop(item, "resolution_y")
    box.prop(scene, "bname_panel_camera_fisheye_layout_mode", text="魚眼モード")
    row = box.row(align=True)
    row.prop(scene, "bname_panel_camera_reduction_mode", text="縮小モード")
    sub = row.row(align=True)
    sub.enabled = bool(scene.bname_panel_camera_reduction_mode)
    sub.prop(scene, "bname_panel_camera_preview_scale_percentage", text="縮小率")
    box.label(text=f"現在: {scene.render.resolution_x} x {scene.render.resolution_y}")


def _draw_background_section(layout, context, settings, label: str, kind: str, opacity_prop: str) -> None:
    box = layout.box()
    row = box.row(align=True)
    row.label(text=label)
    row.prop(settings, opacity_prop, text="")
    visible = bool(getattr(settings, f"{kind}_visible", False))
    icon = "HIDE_OFF" if visible else "HIDE_ON"
    row.operator(f"bname.panel_camera_toggle_{kind}_backgrounds", text="", icon=icon)
    if kind == "name":
        box.prop(settings, "name_show_all_pages", text="全ページも表示")


class BNAME_PT_panel_camera(Panel):
    bl_idname = "BNAME_PT_panel_camera"
    bl_label = "コマ編集: カメラ"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 6
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return get_mode(context) == MODE_PANEL

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = _settings(context)
        if settings is None:
            layout.operator("bname.panel_camera_ensure", text="コマ編集カメラを用意")
            return

        row = layout.row(align=True)
        row.operator("bname.panel_camera_ensure", text="カメラを整備", icon="CAMERA_DATA")
        row.operator("bname.panel_camera_sync_references", text="下絵同期", icon="IMAGE_DATA")

        cam = _camera(context)
        if cam is None:
            layout.label(text="カメラがありません", icon="ERROR")
            return

        _draw_camera_settings(layout, context, cam)
        _draw_angle_list(layout, context, settings)

        box = layout.box()
        box.prop(settings, "white_background", text="背景を透過")
        box.prop(settings, "world_background_camera_only", text="ワールド背景色を被写体に影響させない")
        row = box.row(align=True)
        row.prop(settings, "use_solid_background_color", text="ソリッド背景色")
        sub = row.row(align=True)
        sub.enabled = bool(settings.use_solid_background_color)
        sub.prop(settings, "solid_background_color", text="")
        box.prop(settings, "subsurf_realtime", text="細分割曲面")
        box.prop(settings, "koma_depth", text="コマを後ろにする")
        box.prop(settings, "hatching_visible", text="ハッチング間隔を表示")
        row = box.row()
        row.enabled = bool(settings.hatching_visible)
        row.prop(settings, "hatching_rotation", text="ハッチング回転")
        box.operator("bname.panel_camera_update_view", text="ビューを更新")

        _draw_resolution_settings(layout, context)

        box = layout.box()
        row = box.row()
        row.enabled = bool(getattr(scene, "bname_panel_camera_fisheye_layout_mode", False))
        row.prop(settings, "bg_images_scale", text="下絵のスケール")
        box.operator("bname.panel_camera_toggle_all_backgrounds", text="全下絵を表示/非表示")

        _draw_background_section(layout, context, settings, "下絵_ネーム", "name", "name_bg_images_opacity")
        _draw_background_section(layout, context, settings, "下絵_コマ", "koma", "koma_bg_images_opacity")
        layout.operator("bname.panel_camera_reload_backgrounds", text="すべての下絵を再読込")

        count = panel_camera.camera_background_count(context)
        layout.label(text=f"背景画像: {count}件")


_CLASSES = (BNAME_PT_panel_camera,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
