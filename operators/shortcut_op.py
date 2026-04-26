"""キーボードショートカット用の小オペレータ群.

Preferences でキー割当を変更可能なショートカット:
- bname.set_mode_object  : O 既定 → アクティブを Object モードへ
- bname.set_mode_draw    : P 既定 → アクティブ GP を Draw モードへ
- bname.page_next        : COMMA 既定 → 次のページへフォーカス
- bname.page_prev        : PERIOD 既定 → 前のページへフォーカス
"""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..core.mode import MODE_PAGE, get_mode
from ..core.work import get_work
from ..utils import gpencil as gp_utils
from ..utils import log
from ..utils.geom import mm_to_m
from ..utils.page_grid import (
    _resolve_overview_params,
    page_grid_offset_mm,
)

_logger = log.get_logger(__name__)


# ---------- モード切替 ----------


class BNAME_OT_set_mode_object(Operator):
    """アクティブオブジェクトを Object モードへ切替."""

    bl_idname = "bname.set_mode_object"
    bl_label = "オブジェクトモード"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.view_layer is not None

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj is None:
            return {"CANCELLED"}
        try:
            if obj.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception as exc:  # noqa: BLE001
            _logger.exception("set_mode_object failed")
            self.report({"WARNING"}, f"切替不可: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_set_mode_draw(Operator):
    """アクティブ GP を Draw モード (PAINT_GREASE_PENCIL) へ切替.

    アクティブが GP でない場合は、現在ページの GP オブジェクトを active に
    してから切替える。GP が見つからない場合は no-op。
    """

    bl_idname = "bname.set_mode_draw"
    bl_label = "描画モード"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.view_layer is not None

    def execute(self, context):
        view_layer = context.view_layer
        obj = view_layer.objects.active

        # active が GP でなければ、現在ページの GP を探して active 化
        if obj is None or obj.type != "GREASEPENCIL":
            work = get_work(context)
            if work is not None and 0 <= work.active_page_index < len(work.pages):
                page = work.pages[work.active_page_index]
                gp_obj = gp_utils.get_page_gpencil(page.id)
                if gp_obj is not None:
                    try:
                        view_layer.objects.active = gp_obj
                        gp_obj.select_set(True)
                        obj = gp_obj
                    except Exception:  # noqa: BLE001
                        pass

        if obj is None or obj.type != "GREASEPENCIL":
            self.report({"WARNING"}, "アクティブな Grease Pencil がありません")
            return {"CANCELLED"}
        try:
            if obj.mode != "PAINT_GREASE_PENCIL":
                bpy.ops.object.mode_set(mode="PAINT_GREASE_PENCIL")
        except Exception as exc:  # noqa: BLE001
            _logger.exception("set_mode_draw failed")
            self.report({"WARNING"}, f"切替不可: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


# ---------- ページ移動 ----------


def _focus_view_to_page(context, work, page_index: int) -> None:
    """ビューを指定 page_index の grid 中心へ移動 (距離はキープ)."""
    scene = context.scene
    if scene is None:
        return
    cols, gap, cw, ch = _resolve_overview_params(scene, work)
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    ox_mm, oy_mm = page_grid_offset_mm(
        page_index, cols, gap, cw, ch, start_side, read_direction
    )
    cx = mm_to_m(ox_mm + cw / 2.0)
    cy = mm_to_m(oy_mm + ch / 2.0)

    moved = 0
    for area in context.screen.areas:
        if area.type != "VIEW_3D":
            continue
        space = area.spaces.active
        if space is None:
            continue
        rv3d = getattr(space, "region_3d", None)
        if rv3d is None:
            continue
        try:
            loc = rv3d.view_location.copy()
            loc.x = cx
            loc.y = cy
            rv3d.view_location = loc
            moved += 1
        except Exception:  # noqa: BLE001
            pass
        area.tag_redraw()
    if moved == 0:
        _logger.debug("focus_view_to_page: no VIEW_3D updated")


class BNAME_OT_page_next(Operator):
    """active_page_index を +1 してビューをそのページにフォーカス (循環なし)."""

    bl_idname = "bname.page_next"
    bl_label = "次のページ"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return (
            work is not None
            and work.loaded
            and len(work.pages) > 0
            and get_mode(context) == MODE_PAGE
        )

    def execute(self, context):
        work = get_work(context)
        if work is None or len(work.pages) == 0:
            return {"CANCELLED"}
        new_idx = min(len(work.pages) - 1, work.active_page_index + 1)
        if new_idx == work.active_page_index:
            return {"CANCELLED"}
        work.active_page_index = new_idx
        _focus_view_to_page(context, work, new_idx)
        return {"FINISHED"}


class BNAME_OT_page_prev(Operator):
    """active_page_index を -1 してビューをそのページにフォーカス (循環なし)."""

    bl_idname = "bname.page_prev"
    bl_label = "前のページ"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return (
            work is not None
            and work.loaded
            and len(work.pages) > 0
            and get_mode(context) == MODE_PAGE
        )

    def execute(self, context):
        work = get_work(context)
        if work is None or len(work.pages) == 0:
            return {"CANCELLED"}
        new_idx = max(0, work.active_page_index - 1)
        if new_idx == work.active_page_index:
            return {"CANCELLED"}
        work.active_page_index = new_idx
        _focus_view_to_page(context, work, new_idx)
        return {"FINISHED"}


class BNAME_OT_toggle_lasso_tool(Operator):
    """L キー: 選択ツールを Lasso ⇔ Box でトグル.

    B-Name 作品が開かれている時のみ動作。それ以外は PASS_THROUGH で
    Blender 標準 (Select Linked) に譲る。
    """

    bl_idname = "bname.toggle_lasso_tool"
    bl_label = "投げ縄ツール切替"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"PASS_THROUGH"}
        try:
            wm_tools = bpy.context.workspace.tools
            current_tool = None
            for t in wm_tools:
                # アクティブツールの id (mode/space に依存)
                if t.space_type == "VIEW_3D":
                    current_tool = t.idname
                    break
            new_tool = (
                "builtin.select_box"
                if current_tool == "builtin.select_lasso"
                else "builtin.select_lasso"
            )
            bpy.ops.wm.tool_set_by_id(name=new_tool)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("toggle_lasso_tool failed")
            self.report({"WARNING"}, f"ツール切替失敗: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


# ---------- Cut → 別レイヤー化 (Ctrl+X / Ctrl+V 上書き) ----------


# Cut → Paste 別レイヤー化のフラグ (module スコープ).
# scene custom property に置くと .blend に永続化され、Cut した状態で
# 保存→別ファイルを開いた最初の Paste で意図せず新レイヤーが作られる
# 不具合があるため、プロセス内変数として保持する。
_PASTE_TO_NEW_LAYER_FLAG = False


def _try_call_op(op_callable, *args, **kwargs) -> bool:
    """bpy.ops 呼び出しを try する (失敗時 False)."""
    try:
        result = op_callable(*args, **kwargs)
        return "FINISHED" in result
    except Exception:  # noqa: BLE001
        return False


def _gp_cut_to_clipboard(context) -> bool:
    """選択 GP ストロークをクリップボードへコピー + 削除.

    GP v3 / legacy で operator 名が異なるため複数候補を順に試す。
    """
    # クリップボードへコピー
    copied = False
    for op in (
        getattr(bpy.ops.grease_pencil, "copy", None),
        getattr(bpy.ops.gpencil, "copy", None),
    ):
        if op is None:
            continue
        if _try_call_op(op):
            copied = True
            break
    if not copied:
        return False
    # 選択削除
    for op in (
        getattr(bpy.ops.grease_pencil, "delete", None),
        getattr(bpy.ops.gpencil, "delete", None),
    ):
        if op is None:
            continue
        if _try_call_op(op):
            return True
    return True  # 削除に失敗してもコピーは成功


def _gp_paste_clipboard(context) -> bool:
    """クリップボードから GP ストロークを貼付."""
    for op in (
        getattr(bpy.ops.grease_pencil, "paste", None),
        getattr(bpy.ops.gpencil, "paste", None),
    ):
        if op is None:
            continue
        if _try_call_op(op):
            return True
    return False


class BNAME_OT_gp_cut_to_new_layer(Operator):
    """Ctrl+X 上書き: 選択 GP ストロークを切り取り、次の Paste で新レイヤー化フラグを立てる.

    B-Name 作品が開かれていない、または GP 編集モードでない場合は
    PASS_THROUGH で標準 Cut に譲る。
    """

    bl_idname = "bname.gp_cut_to_new_layer"
    bl_label = "切り取り (新レイヤー予約)"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"PASS_THROUGH"}
        obj = context.view_layer.objects.active if context.view_layer else None
        if obj is None or obj.type != "GREASEPENCIL":
            return {"PASS_THROUGH"}
        if obj.mode not in {"EDIT", "PAINT_GREASE_PENCIL", "SCULPT_GREASE_PENCIL"}:
            return {"PASS_THROUGH"}
        ok = _gp_cut_to_clipboard(context)
        if not ok:
            self.report({"WARNING"}, "Cut 失敗 (選択ストロークがありませんか?)")
            return {"CANCELLED"}
        # 次の Paste で新レイヤー化するフラグ (module 変数: 永続化しない)
        global _PASTE_TO_NEW_LAYER_FLAG
        _PASTE_TO_NEW_LAYER_FLAG = True
        return {"FINISHED"}


class BNAME_OT_gp_paste_to_new_layer(Operator):
    """Ctrl+V 上書き: フラグが立っていれば新規レイヤーを作成し、そこに paste.

    フラグが無い場合は通常 paste (現在レイヤーへ)。
    """

    bl_idname = "bname.gp_paste_to_new_layer"
    bl_label = "貼付 (新レイヤー)"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"PASS_THROUGH"}
        obj = context.view_layer.objects.active if context.view_layer else None
        if obj is None or obj.type != "GREASEPENCIL":
            return {"PASS_THROUGH"}
        scene = context.scene
        global _PASTE_TO_NEW_LAYER_FLAG
        if _PASTE_TO_NEW_LAYER_FLAG:
            # 新規レイヤーを作成して active に
            try:
                gp_data = obj.data
                layers = getattr(gp_data, "layers", None)
                if layers is not None:
                    active_layer = getattr(layers, "active", None)
                    parent_group = getattr(active_layer, "parent_group", None)
                    new_layer = layers.new(name="Pasted")
                    try:
                        layers.active = new_layer
                    except Exception:  # noqa: BLE001
                        pass
                    if parent_group is not None:
                        try:
                            from ..utils import gpencil as gp_utils
                            gp_utils.move_layer_to_group(gp_data, new_layer, parent_group)
                        except Exception:  # noqa: BLE001
                            pass
                    # 新レイヤーに現在フレームの空フレームを補充
                    try:
                        from ..utils import gpencil as gp_utils
                        gp_utils.ensure_active_frame(
                            new_layer,
                            frame_number=scene.frame_current if scene else 1,
                        )
                        gp_utils.ensure_layer_material(
                            obj,
                            new_layer,
                            activate=True,
                            assign_existing=True,
                        )
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                _logger.exception("paste_to_new_layer: layer create failed")
            _PASTE_TO_NEW_LAYER_FLAG = False
        ok = _gp_paste_clipboard(context)
        if not ok:
            self.report({"WARNING"}, "Paste 失敗 (クリップボード空?)")
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_toggle_asset_shelf(Operator):
    """3D View のアセットシェルフ表示をトグル.

    Blender 5.x の Grease Pencil 描画モードで Space に既定割り当てされている
    ブラシ Asset Shelf を、C キー側に移すための wrapper。
    """

    bl_idname = "bname.toggle_asset_shelf"
    bl_label = "アセットシェルフ表示切替"
    bl_options = {"REGISTER"}

    def execute(self, context):
        area = context.area
        if area is None or area.type != "VIEW_3D":
            for a in context.screen.areas:
                if a.type == "VIEW_3D":
                    area = a
                    break
        if area is None:
            return {"CANCELLED"}
        space = area.spaces.active
        if space is None:
            return {"CANCELLED"}
        # Asset Shelf 領域の表示プロパティをトグル (Blender 5.x)
        for attr in ("show_region_asset_shelf", "show_region_tool_header"):
            if hasattr(space, attr) and attr == "show_region_asset_shelf":
                try:
                    setattr(space, attr, not getattr(space, attr))
                    area.tag_redraw()
                    return {"FINISHED"}
                except Exception:  # noqa: BLE001
                    pass
        # フォールバック: region.alignment 切替で表示/非表示
        for region in area.regions:
            if getattr(region, "type", "") == "ASSET_SHELF":
                try:
                    region.alignment = (
                        "NONE" if region.alignment != "NONE" else "BOTTOM"
                    )
                    area.tag_redraw()
                    return {"FINISHED"}
                except Exception:  # noqa: BLE001
                    pass
        return {"CANCELLED"}


_CLASSES = (
    BNAME_OT_set_mode_object,
    BNAME_OT_set_mode_draw,
    BNAME_OT_page_next,
    BNAME_OT_page_prev,
    BNAME_OT_toggle_asset_shelf,
    BNAME_OT_toggle_lasso_tool,
    BNAME_OT_gp_cut_to_new_layer,
    BNAME_OT_gp_paste_to_new_layer,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            pass
