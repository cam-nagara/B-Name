"""汎用レイヤーフォルダの PropertyGroup."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, StringProperty

from ..utils import log

_logger = log.get_logger(__name__)


class BNameLayerFolder(bpy.types.PropertyGroup):
    """画像/ラスター/フキダシ/テキストをまとめる UI 用フォルダ."""

    id: StringProperty(name="ID", default="")  # type: ignore[valid-type]
    title: StringProperty(name="表示名", default="フォルダ")  # type: ignore[valid-type]
    parent_key: StringProperty(name="親キー", default="")  # type: ignore[valid-type]
    expanded: BoolProperty(name="展開", default=True)  # type: ignore[valid-type]
    selected: BoolProperty(name="マルチ選択", default=False, options={"SKIP_SAVE"})  # type: ignore[valid-type]


_CLASSES = (BNameLayerFolder,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _logger.debug("layer_folder registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
