"""選択中レイヤー Object の詳細設定ダイアログを開く operator.

Outliner / 3D ビュー / 各種ツールから右クリックで呼べる単一エントリポイント。
active_object の ``bname_kind`` / ``bname_id`` から対応 entry を逆引きし、
kind ごとのフィールドを ``invoke_props_dialog`` で編集可能に表示する。
"""

from __future__ import annotations

from typing import Optional

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..utils import log
from ..utils import object_naming as on

_logger = log.get_logger(__name__)


def _resolve_active_managed_object(context) -> Optional[bpy.types.Object]:
    """B-Name 管理下のレイヤー Object を解決する.

    優先順位: active_object → selected_objects → selected_ids (Outliner) →
    view_layer.active。Outliner で選択中の Object も拾えるよう全経路を確認する。
    """
    obj = getattr(context, "active_object", None)
    if obj is not None and on.is_managed(obj):
        return obj
    selected = getattr(context, "selected_objects", None) or ()
    for o in selected:
        if on.is_managed(o):
            return o
    selected_ids = getattr(context, "selected_ids", None) or ()
    for sid in selected_ids:
        if isinstance(sid, bpy.types.Object) and on.is_managed(sid):
            return sid
    view_layer = getattr(context, "view_layer", None)
    if view_layer is not None:
        active = getattr(view_layer, "active", None)
        if active is not None and on.is_managed(active):
            return active
    return None


def _find_image_entry(scene, bid: str):
    coll = getattr(scene, "bname_image_layers", None)
    if coll is None:
        return None
    for e in coll:
        if str(getattr(e, "id", "") or "") == bid:
            return e
    return None


def _find_raster_entry(scene, bid: str):
    coll = getattr(scene, "bname_raster_layers", None)
    if coll is None:
        return None
    for e in coll:
        if str(getattr(e, "id", "") or "") == bid:
            return e
    return None


def _find_balloon_entry(scene, bid: str):
    work = getattr(scene, "bname_work", None)
    if work is None:
        return None, None
    for page in getattr(work, "pages", []):
        for e in getattr(page, "balloons", []):
            if str(getattr(e, "id", "") or "") == bid:
                return page, e
    return None, None


def _find_text_entry(scene, bid: str):
    work = getattr(scene, "bname_work", None)
    if work is None:
        return None, None
    for page in getattr(work, "pages", []):
        for e in getattr(page, "texts", []):
            if str(getattr(e, "id", "") or "") == bid:
                return page, e
    return None, None


def _draw_image_detail(layout, entry) -> None:
    layout.prop(entry, "title", text="表示名")
    layout.prop(entry, "filepath", text="画像パス")
    box = layout.box()
    box.label(text="配置 (mm)")
    row = box.row(align=True)
    row.prop(entry, "x_mm")
    row.prop(entry, "y_mm")
    row = box.row(align=True)
    row.prop(entry, "width_mm")
    row.prop(entry, "height_mm")
    box.prop(entry, "rotation_deg")
    row = box.row(align=True)
    row.prop(entry, "flip_x")
    row.prop(entry, "flip_y")
    box = layout.box()
    box.label(text="表示")
    box.prop(entry, "visible")
    box.prop(entry, "locked")
    box.prop(entry, "opacity")
    box.prop(entry, "blend_mode")
    box.prop(entry, "tint_color")
    box = layout.box()
    box.label(text="補正")
    box.prop(entry, "brightness")
    box.prop(entry, "contrast")
    box.prop(entry, "binarize_enabled")
    if getattr(entry, "binarize_enabled", False):
        box.prop(entry, "binarize_threshold")
    box = layout.box()
    box.label(text="所属")
    box.prop(entry, "parent_kind")
    box.prop(entry, "parent_key")
    box.prop(entry, "folder_key")


def _draw_raster_detail(layout, entry) -> None:
    layout.prop(entry, "title", text="表示名")
    layout.prop(entry, "image_name", text="Image 名")
    layout.prop(entry, "filepath_rel", text="PNG 相対パス")
    layout.prop(entry, "dpi")
    layout.prop(entry, "bit_depth")
    layout.prop(entry, "line_color")
    layout.prop(entry, "opacity")
    layout.prop(entry, "visible")
    layout.prop(entry, "locked")
    layout.prop(entry, "scope")
    layout.prop(entry, "parent_kind")
    layout.prop(entry, "parent_key")
    layout.prop(entry, "folder_key")


def _draw_balloon_detail(layout, entry) -> None:
    layout.prop(entry, "title", text="表示名") if hasattr(entry, "title") else None
    layout.prop(entry, "shape")
    box = layout.box()
    box.label(text="配置 (mm)")
    row = box.row(align=True)
    row.prop(entry, "x_mm")
    row.prop(entry, "y_mm")
    row = box.row(align=True)
    row.prop(entry, "width_mm")
    row.prop(entry, "height_mm")
    layout.prop(entry, "line_width_mm")
    layout.prop(entry, "visible")
    layout.prop(entry, "parent_kind")
    layout.prop(entry, "parent_key")
    layout.prop(entry, "folder_key")


def _draw_text_detail(layout, entry) -> None:
    layout.prop(entry, "body", text="本文")
    box = layout.box()
    box.label(text="配置 (mm)")
    row = box.row(align=True)
    row.prop(entry, "x_mm")
    row.prop(entry, "y_mm")
    row = box.row(align=True)
    row.prop(entry, "width_mm")
    row.prop(entry, "height_mm")
    layout.prop(entry, "parent_balloon_id")
    layout.prop(entry, "visible")
    layout.prop(entry, "parent_kind")
    layout.prop(entry, "parent_key")
    layout.prop(entry, "folder_key")


def _draw_object_meta(layout, obj) -> None:
    """Object 自身の B-Name メタを表示 (Custom Property 直接編集)."""
    box = layout.box()
    box.label(text="Outliner メタ", icon="OUTLINER")
    row = box.row(align=True)
    row.label(text=f"kind: {on.get_kind(obj)}")
    row.label(text=f"id: {on.get_bname_id(obj)}")
    row = box.row(align=True)
    row.label(text=f"親: {obj.get('bname_parent_key', '')}")
    if obj.get("bname_folder_id"):
        row.label(text=f"フォルダ: {obj['bname_folder_id']}")
    box.label(text=f"z_index: {obj.get('bname_z_index', 0)}")


class BNAME_OT_layer_detail_open(Operator):
    """選択中の B-Name レイヤー Object の詳細設定ダイアログを開く."""

    bl_idname = "bname.layer_detail_open"
    bl_label = "詳細設定"
    bl_description = (
        "選択中のレイヤー Object (画像/ラスター/フキダシ/テキスト/GP/効果線) "
        "の詳細設定ダイアログを開きます。Outliner / 3D ビュー / 各ツールの "
        "右クリックメニューから呼び出せます。"
    )
    bl_options = {"REGISTER", "UNDO"}

    bname_id: StringProperty(name="bname_id", default="", options={"HIDDEN"})  # type: ignore[valid-type]
    kind: StringProperty(name="kind", default="", options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _resolve_active_managed_object(context) is not None

    def invoke(self, context, event):
        obj = _resolve_active_managed_object(context)
        if obj is None:
            self.report({"WARNING"}, "B-Name 管理レイヤー Object を選択してください")
            return {"CANCELLED"}
        self.bname_id = on.get_bname_id(obj)
        self.kind = on.get_kind(obj)
        if not self.bname_id or not self.kind:
            self.report({"WARNING"}, "選択 Object に B-Name ID / kind がありません")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = on.find_object_by_bname_id(self.bname_id, kind=self.kind)
        if obj is None:
            layout.label(text="対応する Object が見つかりません", icon="ERROR")
            return
        _draw_object_meta(layout, obj)
        layout.separator()

        kind = self.kind
        entry = None
        page = None
        if kind == "image":
            entry = _find_image_entry(scene, self.bname_id)
        elif kind == "raster":
            entry = _find_raster_entry(scene, self.bname_id)
        elif kind == "balloon":
            page, entry = _find_balloon_entry(scene, self.bname_id)
        elif kind == "text":
            page, entry = _find_text_entry(scene, self.bname_id)
        elif kind in {"gp", "effect", "effect_legacy"}:
            # GP / 効果線は entry を持たない (Object 自体が正)
            box = layout.box()
            box.prop(obj, '["bname_title"]', text="表示名")
            box.prop(obj, '["bname_z_index"]', text="z_index")
            if "bname_effect_target" in obj.keys():
                box.prop(obj, '["bname_effect_target"]', text="参照対象")
            return
        else:
            layout.label(text=f"kind={kind} の詳細表示は未対応", icon="INFO")
            return

        if entry is None:
            layout.label(text="対応 entry が見つかりません", icon="ERROR")
            return

        if kind == "image":
            _draw_image_detail(layout, entry)
        elif kind == "raster":
            _draw_raster_detail(layout, entry)
        elif kind == "balloon":
            _draw_balloon_detail(layout, entry)
        elif kind == "text":
            _draw_text_detail(layout, entry)

    def execute(self, context):
        # ダイアログ側で prop を直接編集するので execute では何もしない
        try:
            for area in context.screen.areas if context.screen else ():
                if area.type in {"VIEW_3D", "PROPERTIES", "OUTLINER"}:
                    area.tag_redraw()
        except Exception:  # noqa: BLE001
            pass
        return {"FINISHED"}


_CLASSES = (BNAME_OT_layer_detail_open,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
