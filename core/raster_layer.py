"""ラスター描画レイヤーの PropertyGroup."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)

from ..utils import log

_logger = log.get_logger(__name__)

BIT_DEPTH_ITEMS = (
    ("gray8", "グレー 8bit", ""),
    ("gray1", "1bit", ""),
)

SCOPE_ITEMS = (
    ("page", "ページ", ""),
    ("master", "マスター", ""),
)

PARENT_KIND_ITEMS = (
    ("none", "なし", ""),
    ("page", "ページ", ""),
    ("coma", "コマ", ""),
)


def _on_raster_runtime_display_changed(self, context) -> None:
    try:
        from ..operators import raster_layer_op

        raster_layer_op.sync_raster_runtime_display(context, self)
    except Exception:  # noqa: BLE001
        _logger.exception("raster runtime display update failed")
        screen = getattr(context, "screen", None) if context is not None else None
        if screen is not None:
            for area in screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()


class BNameRasterLayer(bpy.types.PropertyGroup):
    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    title: StringProperty(name="表示名", default="")  # type: ignore[valid-type]
    image_name: StringProperty(name="Image名", default="")  # type: ignore[valid-type]
    filepath_rel: StringProperty(name="PNG相対パス", default="")  # type: ignore[valid-type]
    dpi: IntProperty(  # type: ignore[valid-type]
        name="DPI",
        default=300,
        min=30,
        soft_max=1200,
    )
    bit_depth: EnumProperty(  # type: ignore[valid-type]
        name="階調",
        items=BIT_DEPTH_ITEMS,
        default="gray8",
    )
    line_color: FloatVectorProperty(  # type: ignore[valid-type]
        name="線色",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_raster_runtime_display_changed,
    )
    opacity: FloatProperty(  # type: ignore[valid-type]
        name="不透明度",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_on_raster_runtime_display_changed,
    )
    visible: BoolProperty(name="表示", default=True, update=_on_raster_runtime_display_changed)  # type: ignore[valid-type]
    selected: BoolProperty(name="マルチ選択", default=False, options={"SKIP_SAVE"})  # type: ignore[valid-type]
    locked: BoolProperty(name="ロック", default=False)  # type: ignore[valid-type]
    scope: EnumProperty(  # type: ignore[valid-type]
        name="所属",
        items=SCOPE_ITEMS,
        default="page",
    )
    parent_kind: EnumProperty(  # type: ignore[valid-type]
        name="親",
        items=PARENT_KIND_ITEMS,
        default="page",
    )
    parent_key: StringProperty(name="親キー", default="")  # type: ignore[valid-type]


_CLASSES = (BNameRasterLayer,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bname_raster_layers = CollectionProperty(type=BNameRasterLayer)
    bpy.types.Scene.bname_active_raster_layer_index = IntProperty(default=-1, min=-1)
    _logger.debug("raster_layer registered")


def unregister() -> None:
    for attr in (
        "bname_active_raster_layer_index",
        "bname_raster_layers",
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
