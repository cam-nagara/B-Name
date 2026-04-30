"""カスタム右クリックコンテキストメニュー.

Outliner / 3D ビュー / 各ツール (フキダシ/テキスト/効果線/Object/枠線) で
右クリック時に B-Name レイヤーの「詳細設定」ダイアログを開けるようにする。
active_object の ``bname_kind`` / ``bname_id`` を見て kind ごとの詳細を
``bname.layer_detail_open`` operator で表示する。
"""

from __future__ import annotations

import bpy
from bpy.types import Menu

from ..utils import object_naming as on


def _active_managed_object(context):
    """B-Name 管理下のレイヤー Object を解決する.

    優先順位:
        1. ``context.active_object`` (3D ビューでの選択)
        2. ``context.selected_objects`` の最初の管理 Object
        3. ``context.selected_ids`` (Outliner 選択) の最初の管理 Object
        4. ``view_layer.active`` (Outliner の active)
    """
    # 1. 3D ビューの active_object
    obj = getattr(context, "active_object", None)
    if obj is not None and on.is_managed(obj):
        return obj
    # 2. selected_objects (3D ビューや Outliner で選択中)
    selected = getattr(context, "selected_objects", None) or ()
    for o in selected:
        if on.is_managed(o):
            return o
    # 3. selected_ids (Outliner の context で利用可能)
    selected_ids = getattr(context, "selected_ids", None) or ()
    for sid in selected_ids:
        if isinstance(sid, bpy.types.Object) and on.is_managed(sid):
            return sid
    # 4. view_layer.active (Outliner の active)
    view_layer = getattr(context, "view_layer", None)
    if view_layer is not None:
        active = getattr(view_layer, "active", None)
        if active is not None and on.is_managed(active):
            return active
    return None


def _draw_layer_commands(layout, context) -> None:
    """選択中レイヤー Object に対して詳細/複製/削除 等のコマンドを描画."""
    obj = _active_managed_object(context)
    if obj is None:
        layout.label(text="B-Name レイヤー Object を選択してください", icon="INFO")
        return
    kind = on.get_kind(obj)
    bid = on.get_bname_id(obj)
    layout.label(text=f"{kind}: {bid}", icon="OBJECT_DATA")
    layout.separator()

    detail_row = layout.row()
    detail_row.operator_context = "INVOKE_DEFAULT"
    detail_row.operator(
        "bname.layer_detail_open", text="詳細設定", icon="PREFERENCES"
    )

    # 効果線の場合はリンク複製も
    if kind in {"effect", "effect_legacy"}:
        link_op = getattr(bpy.ops.bname, "effect_line_create_linked", None)
        if link_op is not None:
            layout.operator(
                "bname.effect_line_create_linked",
                text="リンク複製",
                icon="LINKED",
            )

    # Outliner D&D で親変更可能であることの案内
    layout.separator()
    layout.label(
        text="親変更は Outliner で D&D してください",
        icon="OUTLINER",
    )


class BNAME_MT_layer_context(Menu):
    """B-Name レイヤー Object 用サブメニュー (3D ビュー / Outliner 共通)."""

    bl_idname = "BNAME_MT_layer_context"
    bl_label = "B-Name"

    def draw(self, context):
        _draw_layer_commands(self.layout, context)


def open_layer_context_menu() -> bool:
    """ツール側の modal operator から呼び出すヘルパ."""
    try:
        bpy.ops.wm.call_menu(name=BNAME_MT_layer_context.bl_idname)
        return True
    except Exception:  # noqa: BLE001
        return False


# 旧 idname を維持して既存ツール側の呼出を壊さない (内容は新メニューと同じ)
class BNAME_MT_selection_context(Menu):
    bl_idname = "BNAME_MT_selection_context"
    bl_label = "B-Name"

    def draw(self, context):
        _draw_layer_commands(self.layout, context)


class BNAME_MT_object_context(Menu):
    """3D ビューの Object 右クリックに append されるサブメニュー."""

    bl_idname = "BNAME_MT_object_context"
    bl_label = "B-Name"

    def draw(self, context):
        layout = self.layout
        _draw_layer_commands(layout, context)
        layout.separator()
        op_link = getattr(bpy.ops.bname, "open_link_source", None)
        if op_link is not None:
            layout.operator("bname.open_link_source", icon="FILE_BLEND")
        op_record = getattr(bpy.ops.bname, "record_asset_link", None)
        if op_record is not None:
            layout.operator("bname.record_asset_link", icon="LINKED")
        op_thumb = getattr(bpy.ops.bname, "coma_update_thumb", None)
        if op_thumb is not None:
            layout.separator()
            layout.operator("bname.coma_update_thumb", icon="IMAGE")
        op_prev = getattr(bpy.ops.bname, "coma_generate_preview", None)
        if op_prev is not None:
            layout.operator("bname.coma_generate_preview", icon="RESTRICT_RENDER_OFF")


def _draw_in_object_context(self, context):
    """3D ビュー Object 右クリックメニューに B-Name サブメニューを差し込む."""
    obj = _active_managed_object(context)
    if obj is None:
        return
    self.layout.separator()
    self.layout.menu(
        BNAME_MT_object_context.bl_idname,
        icon="OUTLINER_OB_GROUP_INSTANCE",
    )


def _draw_in_outliner_context(self, context):
    """Outliner 右クリックメニューに B-Name サブメニューを差し込む.

    Outliner では Object 未選択でも常にサブメニューを出す (選択中の場合は
    詳細設定が有効、未選択は案内ラベルのみ)。
    """
    self.layout.separator()
    self.layout.menu(
        BNAME_MT_object_context.bl_idname,
        icon="OUTLINER_OB_GROUP_INSTANCE",
    )


_CLASSES = (
    BNAME_MT_layer_context,
    BNAME_MT_selection_context,
    BNAME_MT_object_context,
)


# Outliner の append 候補メニュー (Blender 5.1 で存在するもののみ append される)
_OUTLINER_MENUS = (
    "OUTLINER_MT_object",
    "OUTLINER_MT_collection",
    "OUTLINER_MT_context_menu",
    "OUTLINER_MT_asset",
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_object_context_menu.append(_draw_in_object_context)
    # Outliner の各種右クリックメニューにも同じサブメニューを差し込む
    for menu_name in _OUTLINER_MENUS:
        menu = getattr(bpy.types, menu_name, None)
        if menu is not None:
            try:
                menu.append(_draw_in_outliner_context)
            except (AttributeError, TypeError):
                pass


def unregister() -> None:
    try:
        bpy.types.VIEW3D_MT_object_context_menu.remove(_draw_in_object_context)
    except (ValueError, AttributeError):
        pass
    for menu_name in _OUTLINER_MENUS:
        menu = getattr(bpy.types, menu_name, None)
        if menu is None:
            continue
        try:
            menu.remove(_draw_in_outliner_context)
        except (ValueError, AttributeError):
            pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
