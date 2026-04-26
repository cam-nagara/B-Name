"""コマ編集モード用カメラ操作の PropertyGroup."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)


def _update_all_bg_opacity(self, context) -> None:
    from ..utils import panel_camera

    panel_camera.set_background_images_opacity(context, float(self.bg_images_opacity))


def _update_all_bg_scale(self, context) -> None:
    from ..utils import panel_camera

    panel_camera.set_background_images_scale(context, float(self.bg_images_scale))
    panel_camera.update_render_border_from_current_panel(context)


def _update_name_bg_opacity(self, context) -> None:
    from ..utils import panel_camera

    panel_camera.set_background_images_properties(
        context, "ネーム", opacity=float(self.name_bg_images_opacity)
    )


def _update_koma_bg_opacity(self, context) -> None:
    from ..utils import panel_camera

    panel_camera.set_background_images_properties(
        context, "コマ", opacity=float(self.koma_bg_images_opacity)
    )


def _update_name_show_all_pages(self, context) -> None:
    from ..utils import panel_camera

    panel_camera.set_page_reference_visibility(
        context,
        show_all=bool(self.name_show_all_pages),
    )


def _update_white_background(self, context) -> None:
    scene = getattr(context, "scene", None)
    if scene is not None:
        scene.render.film_transparent = bool(self.white_background)


def _update_subsurf_realtime(self, _context) -> None:
    for obj in bpy.data.objects:
        for mod in getattr(obj, "modifiers", []):
            if getattr(mod, "type", "") == "SUBSURF":
                mod.show_viewport = bool(self.subsurf_realtime)


def _update_hatching_visible(self, context) -> None:
    from ..utils import panel_camera

    panel_camera.set_background_image_visibility(
        context, "ハッチング間隔.png", bool(self.hatching_visible)
    )


def _update_hatching_rotation(self, context) -> None:
    from ..utils import panel_camera

    panel_camera.set_background_image_rotation(
        context, "ハッチング間隔.png", float(self.hatching_rotation)
    )


def _update_koma_depth(self, context) -> None:
    from ..utils import panel_camera

    panel_camera.set_koma_background_depth(context, back=bool(self.koma_depth))


def _update_fisheye_mode(self, context) -> None:
    from ..utils import panel_camera

    panel_camera.apply_fisheye_mode(context)


def _update_reduction_mode(self, context) -> None:
    from ..utils import panel_camera

    panel_camera.apply_reduction_mode(context)


def _update_preview_scale(self, context) -> None:
    from ..utils import panel_camera

    panel_camera.apply_reduction_mode(context)


def _update_resolution_index(self, context) -> None:
    from ..utils import panel_camera

    panel_camera.apply_selected_resolution_setting(context)


class BNamePanelCameraAngleItem(bpy.types.PropertyGroup):
    """カメラ位置・画角・下絵スケールを保存するアングルプリセット."""

    name: StringProperty(name="アングル名", default="Angle")  # type: ignore[valid-type]
    location: FloatVectorProperty(name="位置", size=3, default=(0.0, -6.0, 0.0))  # type: ignore[valid-type]
    rotation: FloatVectorProperty(name="回転", size=3, default=(1.5707963, 0.0, 0.0))  # type: ignore[valid-type]
    lens: FloatProperty(name="焦点距離", default=35.0, min=1.0, max=1000.0)  # type: ignore[valid-type]
    shift_x: FloatProperty(name="シフトX", default=0.0)  # type: ignore[valid-type]
    shift_y: FloatProperty(name="シフトY", default=0.0)  # type: ignore[valid-type]
    fisheye_layout_mode: BoolProperty(name="魚眼モード", default=False)  # type: ignore[valid-type]
    fisheye_fov: FloatProperty(name="魚眼FOV", default=3.1415927, min=1.7453293, max=6.2831855)  # type: ignore[valid-type]
    bg_images_scale: FloatProperty(name="下絵スケール", default=1.0, min=0.1, max=10.0)  # type: ignore[valid-type]


class BNamePanelCameraResolutionSetting(bpy.types.PropertyGroup):
    """カメラ出力解像度プリセット."""

    name: StringProperty(name="名前", default="新規原稿サイズ")  # type: ignore[valid-type]
    resolution_x: IntProperty(name="幅", default=1920, min=1)  # type: ignore[valid-type]
    resolution_y: IntProperty(name="高さ", default=1080, min=1)  # type: ignore[valid-type]


class BNamePanelCameraSettings(bpy.types.PropertyGroup):
    """参照スクリプトのカメラ操作パネル相当の Scene 設定."""

    camera_angles: CollectionProperty(type=BNamePanelCameraAngleItem)  # type: ignore[valid-type]
    camera_angles_index: IntProperty(name="アングルIndex", default=0, min=0)  # type: ignore[valid-type]

    bg_images_opacity: FloatProperty(
        name="下絵の不透明度",
        min=0.0,
        max=1.0,
        default=0.5,
        update=_update_all_bg_opacity,
    )  # type: ignore[valid-type]
    bg_images_scale: FloatProperty(
        name="下絵のスケール",
        min=0.1,
        max=10.0,
        default=1.0,
        update=_update_all_bg_scale,
    )  # type: ignore[valid-type]
    name_bg_images_opacity: FloatProperty(
        name="ネームの不透明度",
        min=0.0,
        max=1.0,
        default=0.5,
        update=_update_name_bg_opacity,
    )  # type: ignore[valid-type]
    koma_bg_images_opacity: FloatProperty(
        name="コマの不透明度",
        min=0.0,
        max=1.0,
        default=1.0,
        update=_update_koma_bg_opacity,
    )  # type: ignore[valid-type]
    name_visible: BoolProperty(name="ネーム下絵表示", default=True)  # type: ignore[valid-type]
    name_show_all_pages: BoolProperty(
        name="全ページのネーム下絵を表示",
        default=False,
        update=_update_name_show_all_pages,
    )  # type: ignore[valid-type]
    koma_visible: BoolProperty(name="コマ下絵表示", default=True)  # type: ignore[valid-type]
    white_background: BoolProperty(
        name="背景を透過",
        default=True,
        update=_update_white_background,
    )  # type: ignore[valid-type]
    subsurf_realtime: BoolProperty(
        name="細分割曲面",
        default=False,
        update=_update_subsurf_realtime,
    )  # type: ignore[valid-type]
    hatching_visible: BoolProperty(
        name="ハッチング間隔を表示",
        default=True,
        update=_update_hatching_visible,
    )  # type: ignore[valid-type]
    hatching_rotation: FloatProperty(
        name="ハッチング回転",
        default=0.0,
        soft_min=-3.1415927,
        soft_max=3.1415927,
        update=_update_hatching_rotation,
    )  # type: ignore[valid-type]
    koma_depth: BoolProperty(
        name="コマを後ろにする",
        default=False,
        update=_update_koma_depth,
    )  # type: ignore[valid-type]
    prev_render_engine: StringProperty(name="前回レンダーエンジン", default="")  # type: ignore[valid-type]


_CLASSES = (
    BNamePanelCameraAngleItem,
    BNamePanelCameraResolutionSetting,
    BNamePanelCameraSettings,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bname_panel_camera_settings = PointerProperty(type=BNamePanelCameraSettings)
    bpy.types.Scene.bname_panel_camera_resolution_settings = CollectionProperty(
        type=BNamePanelCameraResolutionSetting
    )
    bpy.types.Scene.bname_panel_camera_resolution_settings_index = IntProperty(
        name="Index",
        default=0,
        min=0,
        update=_update_resolution_index,
    )
    bpy.types.Scene.bname_panel_camera_fisheye_layout_mode = BoolProperty(
        name="魚眼モード",
        default=False,
        update=_update_fisheye_mode,
    )
    bpy.types.Scene.bname_panel_camera_reduction_mode = BoolProperty(
        name="縮小モード",
        default=False,
        update=_update_reduction_mode,
    )
    bpy.types.Scene.bname_panel_camera_original_resolution_x = IntProperty(
        name="Original Resolution X",
        default=0,
        min=0,
    )
    bpy.types.Scene.bname_panel_camera_original_resolution_y = IntProperty(
        name="Original Resolution Y",
        default=0,
        min=0,
    )
    bpy.types.Scene.bname_panel_camera_preview_scale_percentage = FloatProperty(
        name="縮小率",
        default=12.5,
        min=1.0,
        max=100.0,
        subtype="PERCENTAGE",
        update=_update_preview_scale,
    )
    bpy.types.Scene.bname_panel_camera_lens = FloatProperty(
        name="透視投影の焦点距離",
        default=35.0,
        min=1.0,
        max=1000.0,
    )
    bpy.types.Scene.bname_panel_camera_fisheye_fov = FloatProperty(
        name="魚眼視野角",
        default=3.1415927,
        min=1.7453293,
        max=6.2831855,
    )
    _logger.debug("panel_camera registered")


def unregister() -> None:
    for attr in (
        "bname_panel_camera_fisheye_fov",
        "bname_panel_camera_lens",
        "bname_panel_camera_preview_scale_percentage",
        "bname_panel_camera_original_resolution_y",
        "bname_panel_camera_original_resolution_x",
        "bname_panel_camera_reduction_mode",
        "bname_panel_camera_fisheye_layout_mode",
        "bname_panel_camera_resolution_settings_index",
        "bname_panel_camera_resolution_settings",
        "bname_panel_camera_settings",
    ):
        try:
            delattr(bpy.types.Scene, attr)
        except AttributeError:
            pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
