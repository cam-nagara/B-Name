"""ビューポート Alt 系 reparent オペレーター.

3 つのオペレーターを提供:
- ``BNAME_OT_alt_reparent_drag``: Alt+ドラッグ → ドロップ位置のコンテナへ移動 + 位置追従
- ``BNAME_OT_alt_reparent_into``: Alt+クリック → クリック位置の最深コンテナへ (位置維持)
- ``BNAME_OT_alt_reparent_out``: Alt+Shift+クリック → 1 段浅い親へ (位置維持)

選択中のレイヤー (アクティブ + ``selected`` フラグ) を一括で reparent する。
GPU ドロップインジケーターは ``ui/reparent_overlay`` に状態を渡すことで実現。

フェーズ A/B 範囲: ページ内 / 別ページのコマ・ページへの reparent と、ページ外への昇格。
"""

from __future__ import annotations

import bpy
from bpy.props import IntProperty
from bpy.types import Operator

from ..ui import reparent_overlay
from ..utils import layer_reparent
from ..utils import layer_stack as layer_stack_utils
from ..utils import log
from ..utils.layer_hierarchy import (
    COMA_KIND,
    PAGE_KIND,
    coma_stack_key,
    page_stack_key,
    split_child_key,
)

_logger = log.get_logger(__name__)


_DRAG_THRESHOLD_PX = 4.0


def _has_selected_targets(context) -> bool:
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bname_layer_stack", None) if scene is not None else None
    if stack is None or len(stack) == 0:
        return False
    active_idx = int(getattr(scene, "bname_active_layer_stack_index", -1))
    if 0 <= active_idx < len(stack):
        return True
    for item in stack:
        if layer_stack_utils.is_item_selected(context, item):
            return True
    return False


def _selected_count(context) -> int:
    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bname_layer_stack", None) if scene is not None else None
    if stack is None:
        return 0
    active_idx = int(getattr(scene, "bname_active_layer_stack_index", -1))
    seen_uids: set[str] = set()
    if 0 <= active_idx < len(stack):
        seen_uids.add(layer_stack_utils.stack_item_uid(stack[active_idx]))
    for item in stack:
        if layer_stack_utils.is_item_selected(context, item):
            seen_uids.add(layer_stack_utils.stack_item_uid(item))
    return len(seen_uids)


def _set_overlay_for_target(target) -> None:
    if target is None or target.kind not in {"coma", "page"}:
        reparent_overlay.clear_hover()
        return
    if target.kind == "coma":
        reparent_overlay.set_hover(
            "coma",
            page_id=str(getattr(target.page, "id", "") or ""),
            coma_id=str(getattr(target.panel, "coma_id", "") or ""),
            page_index=int(target.page_index),
        )
    else:
        reparent_overlay.set_hover(
            "page",
            page_id=str(getattr(target.page, "id", "") or ""),
            page_index=int(target.page_index),
        )


def _set_confirm_for_target(target) -> None:
    if target is None or target.kind not in {"coma", "page"}:
        return
    if target.kind == "coma":
        reparent_overlay.flash_confirm(
            "coma",
            page_id=str(getattr(target.page, "id", "") or ""),
            coma_id=str(getattr(target.panel, "coma_id", "") or ""),
            page_index=int(target.page_index),
        )
    else:
        reparent_overlay.flash_confirm(
            "page",
            page_id=str(getattr(target.page, "id", "") or ""),
            page_index=int(target.page_index),
        )


def _set_error_for_target(target) -> None:
    if target is None:
        return
    if target.kind == "coma":
        reparent_overlay.flash_error(
            "coma",
            page_id=str(getattr(target.page, "id", "") or ""),
            coma_id=str(getattr(target.panel, "coma_id", "") or ""),
            page_index=int(target.page_index),
        )
    elif target.kind == "page":
        reparent_overlay.flash_error(
            "page",
            page_id=str(getattr(target.page, "id", "") or ""),
            page_index=int(target.page_index),
        )


# ---------- Alt+クリック (位置維持で 1 段深く) ----------


class BNAME_OT_alt_reparent_into(Operator):
    """Alt+クリック: 選択中のレイヤーをクリック地点の最深コンテナへ reparent (位置維持)."""

    bl_idname = "bname.alt_reparent_into"
    bl_label = "Alt クリックでコマ/ページに入れる"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _has_selected_targets(context)

    def invoke(self, context, event):
        target = layer_reparent.find_click_target(context, event)
        if target.kind == "outside":
            self.report({"INFO"}, "クリック位置にコンテナがありません")
            reparent_overlay.flash_error("page", duration=0.3)
            return {"CANCELLED"}
        changed = layer_reparent.reparent_selected(context, target)
        if changed > 0:
            _set_confirm_for_target(target)
            self.report({"INFO"}, f"{changed} レイヤーを {target.kind} に移動しました")
            return {"FINISHED"}
        # 同一親で何も変わらなかった
        _set_error_for_target(target)
        self.report({"INFO"}, "変更ありません (同じ親)")
        return {"CANCELLED"}


# ---------- Alt+Shift+クリック (位置維持で 1 段浅く) ----------


class BNAME_OT_alt_reparent_out(Operator):
    """Alt+Shift+クリック: 選択中のレイヤーを 1 段浅い親 (コマ→ページ) へ reparent."""

    bl_idname = "bname.alt_reparent_out"
    bl_label = "Alt+Shift クリックでコマ/ページから出す"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _has_selected_targets(context)

    def invoke(self, context, event):
        click_target = layer_reparent.find_click_target(context, event)
        # 選択中の各レイヤーごとに「1 段浅い親」を計算し、最初の有効ターゲットを採用
        scene = context.scene
        stack = scene.bname_layer_stack
        active_idx = int(getattr(scene, "bname_active_layer_stack_index", -1))
        candidates = []
        if 0 <= active_idx < len(stack):
            candidates.append(stack[active_idx])
        for item in stack:
            if layer_stack_utils.is_item_selected(context, item):
                if not any(layer_stack_utils.stack_item_uid(c) == layer_stack_utils.stack_item_uid(item) for c in candidates):
                    candidates.append(item)
        # 全選択レイヤー共通の「1 段浅い」ターゲットを決定
        # 各レイヤーごとに上位先が違う場合は、最初の候補を採用
        target = None
        for item in candidates:
            t = layer_reparent.shallower_target_for_item(context, item, click_target)
            if t is None:
                continue
            if target is None:
                target = t
                break
        if target is None:
            self.report({"INFO"}, "これ以上浅い親はありません")
            reparent_overlay.flash_error("page", duration=0.3)
            return {"CANCELLED"}
        changed = layer_reparent.reparent_selected(context, target)
        if changed > 0:
            _set_confirm_for_target(target)
            self.report({"INFO"}, f"{changed} レイヤーを {target.kind} に出しました")
            return {"FINISHED"}
        _set_error_for_target(target)
        self.report({"INFO"}, "変更ありません (同じ親)")
        return {"CANCELLED"}


# ---------- Alt+ドラッグ (位置追従 + 親変更) ----------


class BNAME_OT_alt_reparent_drag(Operator):
    """Alt+ドラッグ: 選択レイヤーを引きずって、ドロップ先のコンテナへ reparent + 位置追従."""

    bl_idname = "bname.alt_reparent_drag"
    bl_label = "Alt ドラッグでレイヤーを移動 + reparent"
    bl_options = {"REGISTER", "UNDO"}

    _start_x: float
    _start_y: float
    _moved: bool
    _last_target_kind: str
    _last_world_xy: tuple[float, float] | None

    @classmethod
    def poll(cls, context):
        return _has_selected_targets(context)

    def invoke(self, context, event):
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"CANCELLED"}
        if not bool(getattr(event, "alt", False)):
            return {"PASS_THROUGH"}
        # Ctrl+Alt は brush_size_drag 用 → こちらは発動させない
        if bool(getattr(event, "ctrl", False)):
            return {"PASS_THROUGH"}
        # Shift+Alt は alt_reparent_out (クリック型) 用 → ドラッグ前提のこの operator は発動しない
        if bool(getattr(event, "shift", False)):
            return {"PASS_THROUGH"}
        self._start_x = float(event.mouse_x)
        self._start_y = float(event.mouse_y)
        self._moved = False
        self._last_target_kind = ""
        self._last_world_xy = None
        # 初期ターゲット表示
        target = layer_reparent.find_target_for_drop(context, event)
        _set_overlay_for_target(target)
        if target.world_xy_mm is not None:
            count = _selected_count(context)
            reparent_overlay.set_preview(
                world_xy_mm=target.world_xy_mm,
                count=count,
            )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "MOUSEMOVE":
            dx = float(event.mouse_x) - self._start_x
            dy = float(event.mouse_y) - self._start_y
            if abs(dx) >= _DRAG_THRESHOLD_PX or abs(dy) >= _DRAG_THRESHOLD_PX:
                self._moved = True
            target = layer_reparent.find_target_for_drop(context, event)
            _set_overlay_for_target(target)
            if target.world_xy_mm is not None:
                self._last_world_xy = target.world_xy_mm
                reparent_overlay.set_preview(
                    world_xy_mm=target.world_xy_mm,
                    count=_selected_count(context),
                )
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            return self._commit(context, event)
        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            self._cleanup_overlay()
            self.report({"INFO"}, "Alt+ドラッグ reparent をキャンセルしました")
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _commit(self, context, event):
        target = layer_reparent.find_target_for_drop(context, event)
        self._cleanup_overlay()
        # ドラッグせずに離した場合 (= ほぼクリック扱い) でも、位置をドロップ位置に移動
        new_world_xy = target.world_xy_mm if self._moved else None
        changed = layer_reparent.reparent_selected(
            context,
            target,
            new_world_xy_mm=new_world_xy,
        )
        if changed > 0:
            _set_confirm_for_target(target)
            self.report({"INFO"}, f"{changed} レイヤーを {target.kind} に移動 (Alt+ドラッグ)")
            return {"FINISHED"}
        if new_world_xy is not None:
            # 位置移動だけは試みる
            self.report({"INFO"}, "親変更なし (同じコンテナ)")
        _set_error_for_target(target)
        return {"CANCELLED"}

    def _cleanup_overlay(self) -> None:
        reparent_overlay.clear_hover()
        reparent_overlay.clear_preview()


# ---------- register ----------


_CLASSES = (
    BNAME_OT_alt_reparent_into,
    BNAME_OT_alt_reparent_out,
    BNAME_OT_alt_reparent_drag,
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
