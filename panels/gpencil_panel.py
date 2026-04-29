"""Grease Pencil パネル — master GP (作品全ページ共通) のレイヤー管理 UI.

新仕様:
- 作品全体で 1 つの master GP オブジェクト (bname_master_sketch)
- 各レイヤーは複数ページに横断的に存在 (CSP のレイヤーパネル感覚)
- 「ページ GP 一覧」は廃止 (master GP 1 つだけなので不要)
- レイヤー行の種類アイコンから各種設定ダイアログを開く
- マテリアルは内部実装として隠し、ユーザーにはレイヤー設定だけを見せる
"""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_work
from ..utils import gpencil as gp_utils
from ..utils import layer_stack as layer_stack_utils
from ..utils import log
from . import layer_stack_detail_ui

B_NAME_CATEGORY = "B-Name"
_GP_OBJECT_TYPE = "GREASEPENCIL"
_GP_PAINT_MODE = "PAINT_GREASE_PENCIL"
_GP_EDIT_MODE = "EDIT"
_GP_OBJECT_MODE = "OBJECT"
_logger = log.get_logger(__name__)


def _master_gp_object():
    """master GP オブジェクト (なければ None)."""
    return gp_utils.get_master_gpencil()


def _active_gp_layer_target(context):
    scene = getattr(context, "scene", None)
    if scene is None or getattr(scene, "bname_active_layer_kind", "") != "gp":
        return None, None
    item = layer_stack_utils.active_stack_item(context)
    if item is None or getattr(item, "kind", "") != "gp":
        return None, None
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    if resolved is None:
        return None, None
    obj = resolved.get("object")
    layer = resolved.get("target")
    if obj is None or layer is None:
        return None, None
    if gp_utils.layer_effectively_hidden(layer) or gp_utils.layer_effectively_locked(layer):
        return None, None
    return obj, layer


def _activate_gp_layer_for_tool(context):
    obj, layer = _active_gp_layer_target(context)
    if obj is None or layer is None:
        return None
    try:
        context.view_layer.objects.active = obj
        obj.select_set(True)
        obj.data.layers.active = layer
        gp_utils.ensure_active_frame(layer)
        gp_utils.ensure_layer_material(obj, layer, activate=True, assign_existing=True)
    except Exception:  # noqa: BLE001
        _logger.exception("activate gp layer for tool failed")
        return None
    return obj


def _get_prefs():
    try:
        from ..preferences import get_preferences

        return get_preferences()
    except Exception:  # noqa: BLE001
        return None


def _indent(row, depth: int) -> None:
    """階層インデント。1 階層あたり約 1 文字分 (factor 2.0) ずらす.

    `row.separator(factor=N)` は ``N * 0.5 ui-unit`` の幅を空けるため、factor=2.0
    でおよそ 1 文字分。旧実装は 1.25 で半文字弱しかインデントせず、階層が
    視認しづらかった。
    """
    if depth > 0:
        row.separator(factor=2.0 * depth)


def _kind_icon(kind: str) -> str:
    return {
        "page": "FILE_BLANK",
        "coma": "MOD_WIREFRAME",
        "gp": "OUTLINER_OB_GREASEPENCIL",
        "gp_folder": "FILE_FOLDER",
        "image": "IMAGE_DATA",
        "raster": "BRUSH_DATA",
        "balloon_group": "FILE_FOLDER",
        "balloon": "MOD_FLUID",
        "text": "FONT_DATA",
        "effect": "STROKE",
    }.get(kind, "RENDERLAYERS")


def _hide_icon(hidden: bool) -> str:
    return "HIDE_ON" if hidden else "HIDE_OFF"


def _gp_hidden(target) -> bool:
    try:
        return bool(gp_utils.layer_effectively_hidden(target))
    except Exception:  # noqa: BLE001
        return bool(getattr(target, "hide", False))


def _select_icon(row, index: int, icon: str) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    cell.label(text="", icon=icon)


def _select_name(row, index: int, text: str) -> None:
    """名前ラベルを描画 (クリックは template_list 既定の選択動作)。

    Blender の UIList にはカスタムクラス向けのドラッグ並び替え API が無く、
    ボタン widget では drag-out を検出できないため、レイヤーの並び替えは
    パネル右列の TRIA_UP/TRIA_DOWN ボタン経由で行う。
    """
    cell = row.row(align=True)
    cell.alignment = "LEFT"
    cell.label(text=text or "")


def _select_icon_name(row, index: int, text: str, icon: str) -> None:
    _draw_type_icon(row, index, icon)
    _select_name(row, index, text)


def _visibility_button(row, index: int, hidden: bool) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    op = cell.operator(
        "bname.layer_stack_toggle_visibility",
        text="",
        icon=_hide_icon(hidden),
        emboss=False,
    )
    op.index = index


def _draw_square_label(row, text: str = "", icon: str = "BLANK1") -> None:
    """1 ui-unit 幅の placeholder ラベル.

    旧実装は ``text`` も ``icon`` も無いケースで `cell.label(text="")` を呼んで
    いたが、空ラベルは描画幅がオペレーターボタンより僅かに小さくなり、同じ
    depth の行同士が左右にズレて見える原因になっていた。常に BLANK1 アイコンを
    指定して `cell.label(text=text, icon=icon)` を通すことで、可視ボタン (例:
    visibility/expand toggle) と同じ幅を保証する。
    """
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    cell.label(text=text, icon=icon)


def _draw_visibility_slot(row, item, target, index: int) -> None:
    if target is None:
        _draw_square_label(row)
    elif item.kind in {"page", "coma"} and hasattr(target, "visible"):
        _visibility_button(row, index, not bool(target.visible))
    elif item.kind in {"image", "raster"} and hasattr(target, "visible"):
        _visibility_button(row, index, not bool(target.visible))
    elif item.kind in {"gp", "gp_folder", "effect"} and hasattr(target, "hide"):
        _visibility_button(row, index, _gp_hidden(target))
    else:
        _draw_square_label(row)


def _draw_selection_slot(row, index: int, active: bool) -> None:
    _select_icon(row, index, "RADIOBUT_ON" if active else "RADIOBUT_OFF")


def _draw_drag_handle(row, index: int) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.35
    cell.operator_context = "INVOKE_DEFAULT"
    op = cell.operator(
        "bname.layer_stack_drag",
        text="",
        icon="GRIP",
        emboss=False,
    )
    op.index = index


def _draw_hierarchy_slot(row, item, target, index: int) -> None:
    _indent(row, int(getattr(item, "depth", 0)))
    if target is None:
        _draw_square_label(row)
        return
    if item.kind == "page":
        expanded = bool(getattr(target, "stack_expanded", True))
        cell = row.row(align=True)
        cell.ui_units_x = 1.0
        op = cell.operator(
            "bname.layer_stack_toggle_expanded",
            text="",
            emboss=False,
            icon="DISCLOSURE_TRI_DOWN" if expanded else "DISCLOSURE_TRI_RIGHT",
        )
        op.index = index
    elif item.kind == "gp_folder":
        expanded = bool(getattr(target, "is_expanded", True))
        cell = row.row(align=True)
        cell.ui_units_x = 1.0
        op = cell.operator(
            "bname.layer_stack_toggle_expanded",
            text="",
            emboss=False,
            icon="DISCLOSURE_TRI_DOWN" if expanded else "DISCLOSURE_TRI_RIGHT",
        )
        op.index = index
    else:
        _draw_square_label(row)


def _draw_type_icon(row, index: int, icon: str) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    cell.operator_context = "INVOKE_DEFAULT"
    op = cell.operator(
        "bname.layer_stack_detail",
        text="",
        icon=icon,
        emboss=False,
    )
    op.index = index


def _gp_color_style(layer):
    mat = None
    try:
        mat = bpy.data.materials.get(gp_utils._layer_material_name(layer))
    except Exception:  # noqa: BLE001
        mat = None
    return getattr(mat, "grease_pencil", None) if mat is not None else None


def _draw_square_color_prop(row, owner, prop_name: str | None = None) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    if owner is None or prop_name is None or not hasattr(owner, prop_name):
        cell.label(text="")
        return
    cell.prop(owner, prop_name, text="", icon_only=True)


def _draw_square_placeholder(row) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    cell.label(text="")


def _draw_right_aux_lock(row, target, prop_name: str = "lock") -> None:
    if target is None or not hasattr(target, prop_name):
        _draw_square_placeholder(row)
        return
    locked = bool(getattr(target, prop_name))
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    cell.prop(
        target,
        prop_name,
        text="",
        emboss=False,
        icon="LOCKED" if locked else "UNLOCKED",
    )


def _draw_right_aux_coma_enter(row, index: int) -> None:
    cell = row.row(align=True)
    cell.ui_units_x = 1.0
    op = cell.operator(
        "bname.layer_stack_enter_coma",
        text="",
        icon="PLAY",
        emboss=False,
    )
    op.stack_index = index


def _draw_right_controls(row, controls, index: int) -> None:
    slots = row.row(align=True)
    slots.alignment = "RIGHT"
    slots.ui_units_x = 3.0

    gp_style = controls.get("gp_style")
    if gp_style is not None:
        _draw_square_color_prop(slots, gp_style, "color")
        _draw_square_color_prop(slots, gp_style, "fill_color")
    else:
        _draw_square_placeholder(slots)
        _draw_square_placeholder(slots)

    aux = controls.get("aux")
    if aux == "coma_enter":
        _draw_right_aux_coma_enter(slots, index)
    elif aux == "lock":
        _draw_right_aux_lock(slots, controls.get("lock_target"), controls.get("lock_prop", "lock"))
    else:
        _draw_square_placeholder(slots)


def _draw_stack_gp_row(row, controls, item, resolved, index: int) -> None:
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        _draw_type_icon(row, index, _kind_icon(item.kind))
        _select_name(row, index, item.label)
        return
    _draw_type_icon(row, index, _kind_icon(item.kind))
    _select_name(row, index, target.name)
    if item.kind == "gp":
        controls["gp_style"] = _gp_color_style(target)
    if hasattr(target, "lock"):
        controls["aux"] = "lock"
        controls["lock_target"] = target
        controls["lock_prop"] = "lock"


def _draw_stack_page_row(row, item, resolved, index: int, work=None) -> None:
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        _select_icon_name(row, index, item.label, _kind_icon(item.kind))
        return
    icon = "DOCUMENTS" if target.spread else "FILE_BLANK"
    label = layer_stack_detail_ui.page_layer_name(target, work)
    title = str(getattr(target, "title", "") or "").strip()
    _select_icon_name(row, index, f"{label} {title}" if title else label, icon)


def _draw_stack_coma_row(row, controls, item, resolved, index: int) -> None:
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        _select_icon_name(row, index, item.label, _kind_icon(item.kind))
        return
    label = layer_stack_detail_ui.coma_layer_name(target)
    title = str(getattr(target, "title", "") or "").strip()
    _select_icon_name(row, index, f"{label} {title}" if title else label, "MOD_WIREFRAME")
    controls["aux"] = "coma_enter"


def _draw_stack_data_row(row, controls, item, resolved, index: int) -> None:
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        _draw_type_icon(row, index, _kind_icon(item.kind))
        _select_name(row, index, item.label)
        return
    if item.kind == "image":
        _draw_type_icon(row, index, "IMAGE_DATA")
        _select_name(row, index, getattr(target, "title", "") or item.label)
        controls["aux"] = "lock"
        controls["lock_target"] = target
        controls["lock_prop"] = "locked"
    elif item.kind == "raster":
        _draw_type_icon(row, index, "BRUSH_DATA")
        _select_name(row, index, getattr(target, "title", "") or item.label)
        controls["aux"] = "lock"
        controls["lock_target"] = target
        controls["lock_prop"] = "locked"
    elif item.kind == "balloon":
        _draw_type_icon(row, index, "MOD_FLUID")
        _select_name(row, index, target.id)
        row.label(text=getattr(target, "shape", ""))
    elif item.kind == "text":
        _draw_type_icon(row, index, "FONT_DATA")
        _select_name(row, index, getattr(target, "body", "") or item.label)
    elif item.kind == "effect":
        _draw_stack_gp_row(row, controls, item, resolved, index)
    else:
        _draw_type_icon(row, index, _kind_icon(item.kind))
        _select_name(row, index, item.label)


class BNAME_UL_layer_stack(UIList):
    """統合レイヤーリスト。UIList の実CollectionをD&D並び替え対象にする."""

    bl_idname = "BNAME_UL_layer_stack"

    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_propname,
        index,
        flt_flag=0,
    ):
        if self.layout_type not in {"DEFAULT", "COMPACT"}:
            layout.label(text=item.label, icon=_kind_icon(item.kind))
            return
        row = layout.row(align=True)
        active = int(getattr(context.scene, "bname_active_layer_stack_index", -1)) == index
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        target = resolved.get("target") if resolved is not None else None
        _draw_visibility_slot(row, item, target, index)
        _draw_selection_slot(row, index, active)
        _draw_hierarchy_slot(row, item, target, index)
        # 旧実装は ``row.split(factor=0.60)`` で右側に常に 40% を確保しており、
        # 右コントロール (3 ui-units) と左コンテンツの間に大きな空白が出ていた。
        # 右側を ``ui_units_x=3.0`` の固定幅にすることで、左 (型アイコン + 名前)
        # が残り全幅へ拡張され、レイヤー名の表示領域が広がる。
        left = row.row(align=True)
        left.alignment = "LEFT"
        right = row.row(align=True)
        right.alignment = "RIGHT"
        right.ui_units_x = 3.0
        controls = {}
        if item.kind == "page":
            _draw_stack_page_row(left, item, resolved, index, get_work(context))
        elif item.kind == "coma":
            _draw_stack_coma_row(left, controls, item, resolved, index)
        elif item.kind in {"gp", "gp_folder", "effect"}:
            _draw_stack_gp_row(left, controls, item, resolved, index)
        else:
            _draw_stack_data_row(left, controls, item, resolved, index)
        _draw_right_controls(right, controls, index)


def draw_stack_item_detail(layout, context, item, resolved) -> bool:
    return layer_stack_detail_ui.draw_stack_item_detail(layout, context, item, resolved)


def _draw_layer_stack_box(layout, context) -> None:
    scene = context.scene
    box = layout.box()
    box.label(text="レイヤー", icon="RENDERLAYERS")
    try:
        layer_stack_utils.schedule_layer_stack_draw_maintenance(context)
    except Exception as exc:  # noqa: BLE001
        _logger.exception("layer stack panel draw failed")
        box.label(text="レイヤー一覧を更新できません", icon="ERROR")
        box.label(text=str(exc)[:80])
        return
    stack = getattr(scene, "bname_layer_stack", None)
    if stack is None:
        box.label(text="(レイヤーがありません)")
    else:
        row = box.row()
        row.template_list(
            BNAME_UL_layer_stack.bl_idname,
            "",
            scene,
            "bname_layer_stack",
            scene,
            "bname_active_layer_stack_index",
            rows=8,
            sort_lock=False,
        )
        col = row.column(align=True)
        add_menu = col.operator("wm.call_menu", text="", icon="ADD")
        add_menu.name = "BNAME_MT_layer_stack_add"
        col.operator("bname.layer_stack_duplicate", text="", icon="DUPLICATE")
        col.operator("bname.layer_stack_delete", text="", icon="REMOVE")
        col.separator()
        op = col.operator("bname.layer_stack_move", text="", icon="TRIA_UP_BAR")
        op.direction = "FRONT"
        op = col.operator("bname.layer_stack_move", text="", icon="TRIA_UP")
        op.direction = "UP"
        op = col.operator("bname.layer_stack_move", text="", icon="TRIA_DOWN")
        op.direction = "DOWN"
        op = col.operator("bname.layer_stack_move", text="", icon="TRIA_DOWN_BAR")
        op.direction = "BACK"


class BNAME_PT_layer_stack(Panel):
    """統合レイヤーリスト。画像/GP/フキダシ/テキスト/効果線をここに集約する."""

    bl_idname = "BNAME_PT_layer_stack"
    bl_label = "レイヤー"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 12

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work and work.loaded and get_mode(context) != MODE_COMA)

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        if work is None or not work.loaded:
            layout.label(text="作品を開いてください", icon="INFO")
            return
        _draw_layer_stack_box(layout, context)


class BNAME_PT_gpencil(Panel):
    """master GP のモード / 描画色管理 UI."""

    bl_idname = "BNAME_PT_gpencil"
    bl_label = "Grease Pencil"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = B_NAME_CATEGORY
    bl_order = 13
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return get_mode(context) != MODE_COMA

    def draw(self, context):
        layout = self.layout
        work = get_work(context)

        # --- カーソル追従トグル (active_page_index 追従用) ---
        prefs = _get_prefs()
        if prefs is not None:
            box = layout.box()
            row = box.row(align=True)
            row.label(text="カーソル追従", icon="RESTRICT_SELECT_OFF")
            row.prop(prefs, "gpencil_follow_cursor", text="")
            row.operator("bname.gpencil_follow_cursor", text="切替")

        if work is None or not work.loaded:
            layout.label(text="作品を開いてください", icon="INFO")
            return

        # master GP の確保ボタン
        layout.operator(
            "bname.gpencil_master_ensure",
            text="マスター GP を用意",
            icon="OUTLINER_OB_GREASEPENCIL",
        )

        obj = _master_gp_object()
        if obj is None:
            layout.label(text="(マスター GP が未生成です)", icon="INFO")
            return

        row = layout.row(align=True)
        row.label(text=obj.name, icon="OUTLINER_OB_GREASEPENCIL")

        # ブラシ (描画モード時のみ)
        if obj.mode == _GP_PAINT_MODE:
            ts = context.tool_settings
            paint = None
            for attr in (
                "gpencil_paint",
                "grease_pencil_paint",
                "gpencil_v3_paint",
            ):
                paint = getattr(ts, attr, None)
                if paint is not None:
                    break
            if paint is not None:
                brush_box = layout.box()
                brush_box.label(text="ブラシ", icon="BRUSH_DATA")
                try:
                    brush_box.template_ID(paint, "brush")
                except Exception:  # noqa: BLE001
                    if getattr(paint, "brush", None) is not None:
                        brush_box.label(text=paint.brush.name)
                brush = getattr(paint, "brush", None)
                if brush is not None:
                    if hasattr(brush, "size"):
                        brush_box.prop(brush, "size")
                    if hasattr(brush, "strength"):
                        brush_box.prop(brush, "strength")


class BNAME_OT_gpencil_master_ensure(bpy.types.Operator):
    """master GP オブジェクトを ensure (生成 or 既存取得) して active 化."""

    bl_idname = "bname.gpencil_master_ensure"
    bl_label = "マスター GP を用意"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        if scene is None:
            return {"CANCELLED"}
        try:
            obj = gp_utils.ensure_master_gpencil(scene)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"master GP 生成失敗: {exc}")
            return {"CANCELLED"}
        vl = context.view_layer
        if vl is not None and obj is not None:
            try:
                vl.objects.active = obj
                obj.select_set(True)
            except Exception:  # noqa: BLE001
                pass
        return {"FINISHED"}


class BNAME_OT_gpencil_master_mode_set(bpy.types.Operator):
    """master GP を必ず active 化してからツールを切り替える wrapper.

    UI のモード切替ボタンは ``bpy.ops.object.mode_set`` を直接呼ぶと、
    view_layer.objects.active が master GP でない場合に意図しない
    オブジェクトのモードが切り替わる。この wrapper で必ず master GP を
    active 化してから mode_set を呼ぶ。
    """

    bl_idname = "bname.gpencil_master_mode_set"
    bl_label = "B-Nameツール切替"
    bl_options = {"REGISTER", "INTERNAL"}

    mode: bpy.props.StringProperty(default="OBJECT")  # type: ignore[valid-type]

    @classmethod
    def description(cls, _context, properties):
        mode = getattr(properties, "mode", "OBJECT")
        if mode == "OBJECT":
            return "オブジェクトツールに切り替えます"
        if mode == "PAINT_GREASE_PENCIL":
            return "描画ツールに切り替えます"
        if mode == "EDIT":
            return "線編集ツールに切り替えます"
        return "B-Nameツールを切り替えます"

    def execute(self, context):
        try:
            from ..operators import coma_modal_state

            coma_modal_state.finish_all(context)
        except Exception:  # noqa: BLE001
            pass
        if self.mode in {_GP_PAINT_MODE, _GP_EDIT_MODE}:
            obj = _activate_gp_layer_for_tool(context)
            if obj is None:
                self.report({"WARNING"}, "グリースペンシルレイヤーを選択してください")
                return {"CANCELLED"}
        else:
            obj = gp_utils.get_master_gpencil()
        if obj is None and self.mode == _GP_OBJECT_MODE:
            try:
                obj = gp_utils.ensure_master_gpencil(context.scene)
            except Exception:  # noqa: BLE001
                return {"CANCELLED"}
        vl = context.view_layer
        if vl is not None:
            try:
                vl.objects.active = obj
                obj.select_set(True)
            except Exception:  # noqa: BLE001
                pass
        try:
            bpy.ops.object.mode_set(mode=self.mode)
        except Exception as exc:  # noqa: BLE001
            self.report({"WARNING"}, f"モード切替失敗: {exc}")
            return {"CANCELLED"}
        if self.mode == _GP_OBJECT_MODE:
            try:
                bpy.ops.bname.object_tool("INVOKE_DEFAULT")
            except Exception:  # noqa: BLE001
                pass
        return {"FINISHED"}


_CLASSES = (
    BNAME_UL_layer_stack,
    BNAME_PT_layer_stack,
    BNAME_OT_gpencil_master_ensure,
    BNAME_OT_gpencil_master_mode_set,
    BNAME_PT_gpencil,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
