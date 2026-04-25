"""Grease Pencil v3 関連 Operator (ネーム作画用, Phase 2 ページ単位化).

Phase 2 以降は「1 ページ = 1 GP オブジェクト (``page_NNNN_sketch``)」で、
overview 上で全ページの GP が grid 配置される。オペレータは以下の導線:

- ``bname.gpencil_page_ensure``: アクティブページの GP を確保 (必要なら生成)
  し、view_layer.objects.active をその GP に設定。描画モードには入らない
  (ユーザーが任意で Blender 標準の mode_set を使う)。
- ``bname.gpencil_follow_cursor``: マウス位置 → アクティブページ/GP を自動
  切替するタイマー watcher の ON/OFF をトグル。
- ``bname.gpencil_layer_add`` / ``bname.gpencil_layer_remove`` /
  ``bname.gpencil_layer_select``: アクティブ GP のレイヤー操作 (Phase 1 と同等)。

``bname.gpencil_setup`` (1 つのグローバル GP を作る旧オペレータ) は廃止し、
page_ensure にリネーム・改変した。
"""

from __future__ import annotations

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..utils import gpencil as gp_utils
from ..utils import geom, log, page_grid

_logger = log.get_logger(__name__)

_GP_OBJECT_TYPE = "GREASEPENCIL"  # Blender 5.x の GP v3 オブジェクトタイプ
_GP_PAINT_MODE = "PAINT_GREASE_PENCIL"


def _active_gp_object(context):
    obj = context.active_object
    if obj is not None and obj.type == _GP_OBJECT_TYPE:
        return obj
    return None


def _set_view_layer_active(context, obj) -> None:
    """view_layer.objects.active を安全に切替."""
    vl = context.view_layer
    if vl is None or obj is None:
        return
    try:
        for o in list(context.selected_objects):
            if o is not obj:
                o.select_set(False)
    except Exception:  # noqa: BLE001
        pass
    try:
        vl.objects.active = obj
    except Exception:  # noqa: BLE001
        _logger.exception("set active failed: %s", obj.name)
    try:
        obj.select_set(True)
    except Exception:  # noqa: BLE001
        pass


class BNAME_OT_gpencil_page_ensure(Operator):
    """アクティブページの GP オブジェクトを確保して active 化.

    - ページ Collection が無ければ生成
    - GP オブジェクトが無ければ生成 + 既定レイヤー追加
    - view_layer の active を当該 GP に設定
    描画モードには切替しない (ユーザーの意図を尊重)。
    """

    bl_idname = "bname.gpencil_page_ensure"
    bl_label = "ページ用 GP を用意"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded and get_active_page(context) is not None

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        try:
            obj = gp_utils.ensure_page_gpencil(context.scene, page.id)
            work = get_work(context)
            if work is not None:
                page_grid.apply_page_collection_transforms(context, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("gpencil_page_ensure failed")
            self.report({"ERROR"}, f"GP 作成失敗: {exc}")
            return {"CANCELLED"}
        _set_view_layer_active(context, obj)
        self.report({"INFO"}, f"ページ GP を用意: {obj.name}")
        return {"FINISHED"}


class BNAME_OT_gpencil_layer_add(Operator):
    """アクティブ GP v3 にレイヤーを追加."""

    bl_idname = "bname.gpencil_layer_add"
    bl_label = "レイヤー追加"
    bl_options = {"REGISTER", "UNDO"}

    layer_name: StringProperty(name="レイヤー名", default="")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return _active_gp_object(context) is not None

    def execute(self, context):
        obj = _active_gp_object(context)
        if obj is None:
            return {"CANCELLED"}
        gp_data = obj.data
        base = self.layer_name.strip() or "レイヤー"
        existing = {layer.name for layer in gp_data.layers}
        # 最初はサフィックスなしで試し、衝突したら .001, .002, ... と採番
        name = base
        i = 0
        while name in existing:
            i += 1
            name = f"{base}.{i:03d}"
        try:
            layer = gp_data.layers.new(name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("layers.new failed")
            self.report({"ERROR"}, f"レイヤー追加失敗: {exc}")
            return {"CANCELLED"}
        # アクティブ化 (API 差異に備えて try)
        try:
            gp_data.layers.active = layer
        except Exception:  # noqa: BLE001
            pass
        return {"FINISHED"}


class BNAME_OT_gpencil_layer_remove(Operator):
    """アクティブレイヤーを削除."""

    bl_idname = "bname.gpencil_layer_remove"
    bl_label = "レイヤー削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = _active_gp_object(context)
        if obj is None:
            return False
        return getattr(obj.data.layers, "active", None) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        obj = _active_gp_object(context)
        if obj is None:
            return {"CANCELLED"}
        gp_data = obj.data
        layer = getattr(gp_data.layers, "active", None)
        if layer is None:
            return {"CANCELLED"}
        try:
            gp_data.layers.remove(layer)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("layers.remove failed")
            self.report({"ERROR"}, f"レイヤー削除失敗: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_gpencil_layer_select(Operator):
    """名前指定で GP レイヤーをアクティブ化."""

    bl_idname = "bname.gpencil_layer_select"
    bl_label = "レイヤー選択"
    bl_options = {"REGISTER"}

    layer_name: StringProperty(default="")  # type: ignore[valid-type]

    def execute(self, context):
        obj = _active_gp_object(context)
        if obj is None:
            return {"CANCELLED"}
        layer = obj.data.layers.get(self.layer_name)
        if layer is None:
            return {"CANCELLED"}
        try:
            obj.data.layers.active = layer
        except Exception:  # noqa: BLE001
            return {"CANCELLED"}
        return {"FINISHED"}


# ---------- cursor follow watcher (modal) ----------

# 切替のデッドゾーン (mm). これより短い距離で別ページ境界を跨いだときは
# 切替を行わない (境界近傍でのハンチング防止)。
_FOLLOW_DEAD_ZONE_MM = 3.0
# 更新スロットリング (秒). MOUSEMOVE イベント毎ではなく間引く。
_FOLLOW_THROTTLE_SEC = 0.1


_follow_state: dict = {
    "running": False,
    "last_update_time": 0.0,
    "last_x_mm": None,
    "last_y_mm": None,
    "last_page_id": None,
}


def _update_follow_from_event(context, event) -> None:
    """event の mouse 位置から active page + GP を逆引きして切替."""
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    import time

    now = time.monotonic()
    if now - _follow_state["last_update_time"] < _FOLLOW_THROTTLE_SEC:
        return
    _follow_state["last_update_time"] = now

    scene = context.scene
    if scene is None or not getattr(scene, "bname_overview_mode", False):
        return
    try:
        from ..preferences import get_preferences

        prefs = get_preferences()
        if prefs is not None and not bool(prefs.gpencil_follow_cursor):
            return
    except Exception:  # noqa: BLE001
        pass

    work = get_work(context)
    if work is None or not work.loaded or len(work.pages) == 0:
        return

    screen = getattr(context, "screen", None)
    if screen is None:
        return
    mx = event.mouse_x
    my = event.mouse_y
    target_region = None
    target_rv3d = None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "WINDOW":
                continue
            if not (
                region.x <= mx < region.x + region.width
                and region.y <= my < region.y + region.height
            ):
                continue
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if rv3d is None:
                continue
            target_region = region
            target_rv3d = rv3d
            break
        if target_region is not None:
            break
    if target_region is None:
        return

    local_mx = mx - target_region.x
    local_my = my - target_region.y
    loc = region_2d_to_location_3d(
        target_region, target_rv3d, (local_mx, local_my), (0.0, 0.0, 0.0)
    )
    if loc is None:
        return
    x_mm = geom.m_to_mm(loc.x)
    y_mm = geom.m_to_mm(loc.y)

    last_x = _follow_state["last_x_mm"]
    last_y = _follow_state["last_y_mm"]
    if last_x is not None and last_y is not None:
        if (
            abs(x_mm - last_x) < _FOLLOW_DEAD_ZONE_MM
            and abs(y_mm - last_y) < _FOLLOW_DEAD_ZONE_MM
        ):
            return
    _follow_state["last_x_mm"] = x_mm
    _follow_state["last_y_mm"] = y_mm

    page_idx = page_grid.page_index_at_world_mm(work, scene, x_mm, y_mm)
    if page_idx is None or not (0 <= page_idx < len(work.pages)):
        return
    page = work.pages[page_idx]
    if page.id == _follow_state["last_page_id"]:
        if work.active_page_index != page_idx:
            work.active_page_index = page_idx
        return
    _follow_state["last_page_id"] = page.id
    work.active_page_index = page_idx
    # 新仕様 (master GP) ではページ単位 GP の active 切替は不要。
    # active_page_index の更新だけで「現在のページ」UI は追従する。


class BNAME_OT_gpencil_follow_modal(Operator):
    """カーソル追従 watcher の内部モーダルオペレータ.

    ユーザーは直接呼び出さない。``_follow_start()`` が起動する。
    - TIMER イベントで MOUSEMOVE 以外の移動もキャッチ (描画中でも吸い上げ)
    - 常に PASS_THROUGH を返して他のオペレータを邪魔しない
    """

    bl_idname = "bname.gpencil_follow_modal"
    bl_label = "B-Name: GP 追従"
    bl_options = {"INTERNAL"}

    _timer = None

    def modal(self, context, event):
        if not _follow_state["running"]:
            self._cleanup(context)
            return {"CANCELLED"}
        if event.type in {"MOUSEMOVE", "TIMER"}:
            try:
                _update_follow_from_event(context, event)
            except Exception:  # noqa: BLE001
                _logger.exception("follow modal tick failed")
        return {"PASS_THROUGH"}

    def invoke(self, context, event):
        wm = context.window_manager
        self._timer = wm.event_timer_add(_FOLLOW_THROTTLE_SEC, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _cleanup(self, context):
        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:  # noqa: BLE001
                pass
            self._timer = None


def _follow_start() -> None:
    if _follow_state["running"]:
        return
    _follow_state["running"] = True
    _follow_state["last_update_time"] = 0.0
    _follow_state["last_x_mm"] = None
    _follow_state["last_y_mm"] = None
    _follow_state["last_page_id"] = None
    # context に応じた invoke 呼出. window が無い場合はスキップ
    # (起動直後 register 時にこのパスを通る場合があるので無害)。
    try:
        if bpy.context.window is not None:
            bpy.ops.bname.gpencil_follow_modal("INVOKE_DEFAULT")
    except Exception:  # noqa: BLE001
        _logger.exception("follow_start: invoke failed")


def _follow_stop() -> None:
    _follow_state["running"] = False


class BNAME_OT_gpencil_follow_cursor(Operator):
    """マウス位置追従 watcher の ON/OFF トグル.

    preferences.gpencil_follow_cursor に状態を保存する。
    """

    bl_idname = "bname.gpencil_follow_cursor"
    bl_label = "カーソル追従 GP"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            from ..preferences import get_preferences

            prefs = get_preferences()
        except Exception:  # noqa: BLE001
            prefs = None
        if prefs is not None:
            # prefs の update コールバックが _follow_start / _follow_stop を
            # 呼ぶため、ここでは値を書き換えるだけで十分。
            new_state = not bool(prefs.gpencil_follow_cursor)
            prefs.gpencil_follow_cursor = new_state
        else:
            # prefs が取得できないフォールバック: セッション内フラグで直接制御
            new_state = not _follow_state["running"]
            if new_state:
                _follow_start()
            else:
                _follow_stop()
        self.report({"INFO"}, f"カーソル追従 GP: {'ON' if new_state else 'OFF'}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_gpencil_page_ensure,
    BNAME_OT_gpencil_follow_modal,
    BNAME_OT_gpencil_follow_cursor,
    BNAME_OT_gpencil_layer_add,
    BNAME_OT_gpencil_layer_remove,
    BNAME_OT_gpencil_layer_select,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    # register 時の自動起動は行わない。
    # 理由: モーダルオペレータを register 直後に起動してしまうと、アドオン
    # 無効化時 (unregister) にモーダルが動作中のままクラス解除が発生し、
    # Blender が C レベルでクラッシュする (Phase 2 実装で発生確認済)。
    # ユーザーは N パネル > Grease Pencil > 「切替」ボタンで任意に起動する。


def unregister() -> None:
    # 1) モーダル停止フラグを立てる (次の event tick で self._cleanup が走る)
    _follow_stop()
    # 2) BNAME_OT_gpencil_follow_modal は最後に unregister し、例外を握り潰す
    #    (走行中のモーダルが残っていても Blender がクラッシュしないよう防御)
    modal_cls = None
    other_classes = []
    for cls in _CLASSES:
        if cls.__name__ == "BNAME_OT_gpencil_follow_modal":
            modal_cls = cls
        else:
            other_classes.append(cls)
    for cls in reversed(other_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    if modal_cls is not None:
        try:
            bpy.utils.unregister_class(modal_cls)
        except Exception:  # noqa: BLE001 - Blender 内部エラー全般を握り潰し
            _logger.exception("follow_modal unregister skipped")
