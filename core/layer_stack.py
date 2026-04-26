"""統合レイヤーリスト用の軽量 PropertyGroup."""

from __future__ import annotations

import bpy
from bpy.props import CollectionProperty, EnumProperty, IntProperty, StringProperty

from ..utils import log

_logger = log.get_logger(__name__)

LAYER_KIND_ITEMS = (
    ("gp", "グリースペンシル", ""),
    ("gp_folder", "フォルダ", ""),
    ("image", "画像", ""),
    ("balloon", "フキダシ", ""),
    ("text", "テキスト", ""),
    ("effect", "効果線", ""),
)

ACTIVE_LAYER_KIND_ITEMS = (
    ("gp", "グリースペンシル", ""),
    ("gp_folder", "フォルダ", ""),
    ("image", "画像", ""),
    ("balloon", "フキダシ", ""),
    ("text", "テキスト", ""),
    ("effect", "効果線", ""),
)


class BNameLayerStackItem(bpy.types.PropertyGroup):
    """統合レイヤーリストの 1 行。

    実データは GP / 画像 / ページ要素側に保持し、この行は参照キーと
    表示階層だけを持つ。前面→背面の表示順はこの CollectionProperty の
    並びで管理する。
    """

    kind: EnumProperty(name="種別", items=LAYER_KIND_ITEMS, default="gp")  # type: ignore[valid-type]
    key: StringProperty(name="参照キー", default="")  # type: ignore[valid-type]
    label: StringProperty(name="表示名", default="")  # type: ignore[valid-type]
    parent_key: StringProperty(name="親キー", default="")  # type: ignore[valid-type]
    depth: IntProperty(name="階層", default=0, min=0)  # type: ignore[valid-type]


_CLASSES = (BNameLayerStackItem,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bname_layer_stack = CollectionProperty(type=BNameLayerStackItem)
    bpy.types.Scene.bname_active_layer_stack_index = IntProperty(default=-1, min=-1)
    bpy.types.Scene.bname_active_layer_kind = EnumProperty(
        name="アクティブレイヤー種別",
        items=ACTIVE_LAYER_KIND_ITEMS,
        default="gp",
    )
    bpy.types.Scene.bname_active_gp_folder_key = StringProperty(default="")
    bpy.types.Scene.bname_active_effect_layer_name = StringProperty(default="")
    _logger.debug("layer_stack registered")


def unregister() -> None:
    for attr in (
        "bname_active_effect_layer_name",
        "bname_active_gp_folder_key",
        "bname_active_layer_kind",
        "bname_active_layer_stack_index",
        "bname_layer_stack",
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
