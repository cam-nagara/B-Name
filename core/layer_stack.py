"""統合レイヤーリスト用の軽量 PropertyGroup."""

from __future__ import annotations

import bpy
from bpy.props import CollectionProperty, EnumProperty, IntProperty, StringProperty

from ..utils import log

_logger = log.get_logger(__name__)
_active_index_update_depth = 0

LAYER_KIND_ITEMS = (
    ("page", "ページ", ""),
    ("panel", "コマ", ""),
    ("gp", "グリースペンシル", ""),
    ("gp_folder", "フォルダ", ""),
    ("image", "画像", ""),
    ("raster", "ラスター", ""),
    ("balloon_group", "フキダシフォルダ", ""),
    ("balloon", "フキダシ", ""),
    ("text", "テキスト", ""),
    ("effect", "効果線", ""),
)

ACTIVE_LAYER_KIND_ITEMS = (
    ("page", "ページ", ""),
    ("panel", "コマ", ""),
    ("gp", "グリースペンシル", ""),
    ("gp_folder", "フォルダ", ""),
    ("image", "画像", ""),
    ("raster", "ラスター", ""),
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
    name: StringProperty(name="名前", default="")  # type: ignore[valid-type]
    key: StringProperty(name="参照キー", default="")  # type: ignore[valid-type]
    label: StringProperty(name="表示名", default="")  # type: ignore[valid-type]
    parent_key: StringProperty(name="親キー", default="")  # type: ignore[valid-type]
    depth: IntProperty(name="階層", default=0, min=0)  # type: ignore[valid-type]


_CLASSES = (BNameLayerStackItem,)


def _on_active_layer_stack_index_changed(_self, context) -> None:
    """UIList の通常クリック/D&D選択を実データの選択状態へ反映する."""
    global _active_index_update_depth

    if _active_index_update_depth > 0:
        return
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    stack = getattr(scene, "bname_layer_stack", None)
    idx = int(getattr(scene, "bname_active_layer_stack_index", -1))
    if stack is None or not (0 <= idx < len(stack)):
        return
    try:
        from ..utils import layer_stack as layer_stack_utils
    except Exception:  # noqa: BLE001
        _logger.exception("layer stack utils import failed")
        return
    active_uid = ""
    try:
        active_uid = layer_stack_utils.stack_item_uid(stack[idx])
    except Exception:  # noqa: BLE001
        active_uid = ""
    _active_index_update_depth += 1
    try:
        order_changed = layer_stack_utils.apply_stack_order_if_ui_changed(
            context,
            moved_uid=active_uid,
        )
        if order_changed and active_uid:
            stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
            if stack is not None:
                for i, item in enumerate(stack):
                    if layer_stack_utils.stack_item_uid(item) == active_uid:
                        layer_stack_utils.select_stack_index(context, i)
                        return
        layer_stack_utils.select_stack_index(context, idx)
    except Exception:  # noqa: BLE001
        _logger.exception("active layer stack index update failed")
    finally:
        _active_index_update_depth -= 1


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bname_layer_stack = CollectionProperty(type=BNameLayerStackItem)
    bpy.types.Scene.bname_active_layer_stack_index = IntProperty(
        default=-1,
        min=-1,
        update=_on_active_layer_stack_index_changed,
    )
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
