"""ビューポート操作モーダルオペレータ (CSP 準拠).

CLIP STUDIO PAINT のショートカット規約を Blender の N パネル B-Name タブ
アクティブ時に再現する。Blender 標準 ``view3d.move`` / ``view3d.zoom`` /
``view3d.rotate`` は「マウスドラッグ前提」の挙動が噛み合わないため自前で
modal 実装する。

- **Space + LMB ドラッグ** → パン
- **Ctrl+Space + LMB ドラッグ** → ズーム (ドラッグ開始地点ピボット・左右で倍率)
- **Shift+Space + LMB ドラッグ** → 回転 (ビュー中心軸・角度追従・360° 一周)
- **Space + ダブルクリック** → 表示位置リセット + ページフィット (100% 相当)
- **Shift+Space + ダブルクリック** → 回転リセット (TOP 正投影へ)

modal 終了条件: Space (および修飾キー) のリリース。Space 継続中は LMB の
PRESS/RELEASE を何度も行える (複数回のドラッグ操作が可能)。

カーソル:
- パン: ``SCROLL_XY``
- ズーム: ``ZOOM_IN``
- 回転: ``CROSSHAIR`` (Blender に明示的な回転カーソルが無いため代用)
"""

from __future__ import annotations

import math

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator
from mathutils import Quaternion, Vector

from ..utils import log

_logger = log.get_logger(__name__)


# ---------- 共通ヘルパ ----------


def _find_view3d_window_region(context):
    """現在 context から VIEW_3D area / WINDOW region / rv3d を取得.

    N パネル (UI region) から起動された場合でも WINDOW region に切替えて返す。
    """
    area = context.area if context.area and context.area.type == "VIEW_3D" else None
    if area is None:
        screen = context.screen
        if screen is not None:
            for a in screen.areas:
                if a.type == "VIEW_3D":
                    area = a
                    break
    if area is None:
        return None, None, None
    region = None
    for r in area.regions:
        if r.type == "WINDOW":
            region = r
            break
    if region is None:
        return None, None, None
    space = area.spaces.active
    rv3d = getattr(space, "region_3d", None)
    if rv3d is None:
        return None, None, None
    return area, region, rv3d


def _region_to_world(region, rv3d, mx: float, my: float) -> Vector:
    """region ピクセル座標 (mx, my) を z=0 平面の world 座標に変換."""
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    origin = rv3d.view_location.copy()
    return region_2d_to_location_3d(region, rv3d, (float(mx), float(my)), origin)


def _to_region_local(region, mx_abs: float, my_abs: float) -> tuple[float, float]:
    """絶対 window 座標を region ローカル座標に変換."""
    return mx_abs - region.x, my_abs - region.y


def _in_region(region, mx_abs: float, my_abs: float) -> bool:
    return (
        region.x <= mx_abs < region.x + region.width
        and region.y <= my_abs < region.y + region.height
    )


# ---------- パン ----------


class BNAME_OT_view_pan(Operator):
    """Space 押下中、LMB ドラッグでビューをパン.

    - Space + ダブルクリック: 表示位置 = 原点、ページフィット (100% 相当)
    - Space リリースで modal 終了
    """

    bl_idname = "bname.view_pan"
    bl_label = "B-Name ビューパン"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        area, region, rv3d = _find_view3d_window_region(context)
        if area is None:
            return {"CANCELLED"}
        self._area = area
        self._region = region
        self._rv3d = rv3d
        self._dragging = False
        self._prev_mx = 0.0
        self._prev_my = 0.0

        context.window.cursor_modal_set("SCROLL_XY")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        # Space リリースで終了
        if event.type == "SPACE" and event.value == "RELEASE":
            return self._finish(context)

        # ダブルクリック: 表示位置リセット + フィット
        if event.type == "LEFTMOUSE" and event.value == "DOUBLE_CLICK":
            self._reset_view(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                self._dragging = True
                local = _to_region_local(self._region, event.mouse_x, event.mouse_y)
                self._prev_mx, self._prev_my = local
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE":
                self._dragging = False
                return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self._dragging:
            mx, my = _to_region_local(self._region, event.mouse_x, event.mouse_y)
            self._apply_pan(mx, my)
            self._prev_mx, self._prev_my = mx, my
            self._region.tag_redraw()
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def _apply_pan(self, mx: float, my: float) -> None:
        try:
            w_prev = _region_to_world(self._region, self._rv3d, self._prev_mx, self._prev_my)
            w_curr = _region_to_world(self._region, self._rv3d, mx, my)
            self._rv3d.view_location += (w_prev - w_curr)
        except Exception:  # noqa: BLE001
            _logger.exception("view_pan: apply failed")

    def _reset_view(self, context) -> None:
        """表示位置 = 原点、ページ全体がフィットする倍率へ."""
        rv3d = self._rv3d
        rv3d.view_location = Vector((0.0, 0.0, 0.0))
        # ページフィット (アクティブページを画面に合わせる) を呼び出して
        # 「100% 相当」の状態にリセット
        try:
            with bpy.context.temp_override(
                window=context.window, area=self._area, region=self._region
            ):
                bpy.ops.bname.view_fit_page("INVOKE_DEFAULT")
        except Exception:  # noqa: BLE001
            # フィットが使えない状況ではビュー距離のみ既定へ
            try:
                rv3d.view_distance = 2.0
            except Exception:  # noqa: BLE001
                pass
        self._region.tag_redraw()

    def _finish(self, context):
        try:
            context.window.cursor_modal_restore()
        except Exception:  # noqa: BLE001
            pass
        return {"FINISHED"}


# ---------- ズーム ----------


class BNAME_OT_view_zoom_drag(Operator):
    """Ctrl+Space 押下中、LMB ドラッグでズーム.

    - 左右ドラッグ量で倍率調整 (右=ズームイン、左=ズームアウト)
    - ドラッグ開始地点のワールド座標をピボットとして維持
    - Ctrl / Space いずれかのリリースで modal 終了
    """

    bl_idname = "bname.view_zoom_drag"
    bl_label = "B-Name ビューズーム (ドラッグ)"
    bl_options = {"REGISTER"}

    # CSP 風の感度 (大きいほど少しのドラッグで大きく変化)
    _SENSITIVITY = 0.006

    def invoke(self, context, event):
        area, region, rv3d = _find_view3d_window_region(context)
        if area is None:
            return {"CANCELLED"}
        self._area = area
        self._region = region
        self._rv3d = rv3d
        self._dragging = False
        self._drag_start_mx = 0.0
        self._drag_start_my = 0.0
        self._drag_start_distance = 1.0
        self._drag_start_view_location = Vector((0.0, 0.0, 0.0))
        self._drag_start_world_pivot = Vector((0.0, 0.0, 0.0))

        context.window.cursor_modal_set("ZOOM_IN")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        # Ctrl または Space リリースで終了
        if (
            (event.type == "SPACE" and event.value == "RELEASE")
            or (event.type in {"LEFT_CTRL", "RIGHT_CTRL"} and event.value == "RELEASE")
        ):
            return self._finish(context)

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                self._begin_drag(event)
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE":
                self._dragging = False
                return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self._dragging:
            mx, _my = _to_region_local(self._region, event.mouse_x, event.mouse_y)
            self._apply_zoom(mx)
            self._region.tag_redraw()
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def _begin_drag(self, event) -> None:
        mx, my = _to_region_local(self._region, event.mouse_x, event.mouse_y)
        self._drag_start_mx = mx
        self._drag_start_my = my
        self._drag_start_distance = float(self._rv3d.view_distance)
        self._drag_start_view_location = self._rv3d.view_location.copy()
        # ドラッグ開始時点のマウス位置 world 座標 (ピボット)
        try:
            self._drag_start_world_pivot = _region_to_world(
                self._region, self._rv3d, mx, my
            ).copy()
        except Exception:  # noqa: BLE001
            self._drag_start_world_pivot = self._rv3d.view_location.copy()
        self._dragging = True

    def _apply_zoom(self, mx: float) -> None:
        """左右ドラッグ量に応じて view_distance を更新し、ピボットを維持."""
        dx = mx - self._drag_start_mx
        # factor > 1 で近づく (ズームイン) = view_distance 縮小
        factor = math.exp(dx * self._SENSITIVITY)
        new_distance = self._drag_start_distance / max(1e-6, factor)
        # view_distance の下限・上限で発散を防止
        new_distance = max(1e-4, min(new_distance, 1e6))

        rv3d = self._rv3d
        region = self._region
        try:
            # 新しい距離を適用する前に、ピボットの world 座標を確定
            pivot_world = self._drag_start_world_pivot
            # 一旦 view_location と distance をリセット (ドラッグ開始状態) してから
            # 距離だけ更新 → ピボット移動後にずれを補正する
            rv3d.view_location = self._drag_start_view_location.copy()
            rv3d.view_distance = new_distance
            # 更新後、開始マウス位置 (self._drag_start_mx/my) が指す world が
            # ピボット world と一致するように view_location を平行移動
            new_pivot_world = _region_to_world(
                region, rv3d, self._drag_start_mx, self._drag_start_my
            )
            rv3d.view_location += (pivot_world - new_pivot_world)
        except Exception:  # noqa: BLE001
            _logger.exception("view_zoom: apply failed")

    def _finish(self, context):
        try:
            context.window.cursor_modal_restore()
        except Exception:  # noqa: BLE001
            pass
        return {"FINISHED"}


# ---------- 回転 ----------


class BNAME_OT_view_rotate(Operator):
    """Shift+Space 押下中、LMB ドラッグでビューを中心軸回転.

    - ドラッグ位置とビュー中心を結ぶベクトルの角度変化をそのまま view_rotation
      に積分 (360° ぐるっと回せば view も 360° 回転)
    - Shift+Space + ダブルクリック: 回転リセット (TOP 正投影へ)
    - Shift / Space いずれかのリリースで modal 終了
    """

    bl_idname = "bname.view_rotate"
    bl_label = "B-Name ビュー回転"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        area, region, rv3d = _find_view3d_window_region(context)
        if area is None:
            return {"CANCELLED"}
        self._area = area
        self._region = region
        self._rv3d = rv3d
        self._dragging = False
        self._prev_angle = 0.0

        context.window.cursor_modal_set("CROSSHAIR")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        # Shift または Space リリースで終了
        if (
            (event.type == "SPACE" and event.value == "RELEASE")
            or (event.type in {"LEFT_SHIFT", "RIGHT_SHIFT"} and event.value == "RELEASE")
        ):
            return self._finish(context)

        # ダブルクリック: 回転リセット
        if event.type == "LEFTMOUSE" and event.value == "DOUBLE_CLICK":
            self._reset_rotation(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                mx, my = _to_region_local(self._region, event.mouse_x, event.mouse_y)
                self._prev_angle = self._angle_at(mx, my)
                self._dragging = True
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE":
                self._dragging = False
                return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self._dragging:
            mx, my = _to_region_local(self._region, event.mouse_x, event.mouse_y)
            curr_angle = self._angle_at(mx, my)
            delta = curr_angle - self._prev_angle
            # 角度の折返し処理 (-π, π] → 連続値に近似
            if delta > math.pi:
                delta -= 2 * math.pi
            elif delta < -math.pi:
                delta += 2 * math.pi
            self._apply_rotation(delta)
            self._prev_angle = curr_angle
            self._region.tag_redraw()
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    def _angle_at(self, mx: float, my: float) -> float:
        """region ローカル (mx, my) とリージョン中心を結ぶベクトルの角度 (ラジアン)."""
        cx = self._region.width / 2.0
        cy = self._region.height / 2.0
        return math.atan2(my - cy, mx - cx)

    def _apply_rotation(self, delta_angle: float) -> None:
        """world Z 軸周りに delta_angle だけ view_rotation を回転."""
        if abs(delta_angle) < 1e-9:
            return
        try:
            rot = Quaternion((0.0, 0.0, 1.0), delta_angle)
            self._rv3d.view_rotation = rot @ self._rv3d.view_rotation
        except Exception:  # noqa: BLE001
            _logger.exception("view_rotate: apply failed")

    def _reset_rotation(self, context) -> None:
        """回転を TOP 正投影に戻す (ortho を保つ)."""
        rv3d = self._rv3d
        try:
            with bpy.context.temp_override(
                window=context.window, area=self._area, region=self._region
            ):
                bpy.ops.view3d.view_axis(type="TOP")
            if rv3d.view_perspective != "ORTHO":
                rv3d.view_perspective = "ORTHO"
        except Exception:  # noqa: BLE001
            _logger.exception("view_rotate: reset failed")
        self._region.tag_redraw()

    def _finish(self, context):
        try:
            context.window.cursor_modal_restore()
        except Exception:  # noqa: BLE001
            pass
        return {"FINISHED"}


# ---------- 1 ステップズーム (ホイール系) ----------


class BNAME_OT_view_zoom_step(Operator):
    """Ctrl+ホイールで 1 ステップズーム (方向引数)."""

    bl_idname = "bname.view_zoom_step"
    bl_label = "B-Name ビューズーム (1 ステップ)"
    bl_options = {"REGISTER"}

    direction: EnumProperty(  # type: ignore[valid-type]
        items=(("IN", "In", ""), ("OUT", "Out", "")),
        default="IN",
    )

    def invoke(self, context, event):
        area, region, rv3d = _find_view3d_window_region(context)
        if area is None or rv3d is None:
            return {"CANCELLED"}
        # Ctrl+ホイールではマウス位置ピボットでズーム
        mx, my = _to_region_local(region, event.mouse_x, event.mouse_y)
        # 1 ステップ倍率
        factor = 1.15 if self.direction == "IN" else 1.0 / 1.15
        try:
            pivot_world = _region_to_world(region, rv3d, mx, my).copy()
            rv3d.view_distance = max(1e-4, rv3d.view_distance / factor)
            new_pivot_world = _region_to_world(region, rv3d, mx, my)
            rv3d.view_location += (pivot_world - new_pivot_world)
        except Exception:  # noqa: BLE001
            _logger.exception("view_zoom_step failed")
            return {"CANCELLED"}
        region.tag_redraw()
        return {"FINISHED"}

    def execute(self, context):
        # Ctrl+ホイールのキーマップは PRESS で invoke を呼ぶが、非インタラクティブ
        # 呼出時は viewport 中心ピボット扱い
        area, region, rv3d = _find_view3d_window_region(context)
        if area is None or rv3d is None:
            return {"CANCELLED"}
        factor = 1.15 if self.direction == "IN" else 1.0 / 1.15
        try:
            rv3d.view_distance = max(1e-4, rv3d.view_distance / factor)
        except Exception:  # noqa: BLE001
            return {"CANCELLED"}
        region.tag_redraw()
        return {"FINISHED"}


# ---------- レイヤー選択 (既存) ----------


class BNAME_OT_view_layer_pick(Operator):
    """Ctrl+Shift+クリックで作画レイヤーを選択 (簡易: 直下のコマを active に)."""

    bl_idname = "bname.view_layer_pick"
    bl_label = "B-Name レイヤー選択"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        from ..core.work import get_active_page
        from ..utils.geom import m_to_mm

        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return {"CANCELLED"}

        try:
            from bpy_extras.view3d_utils import region_2d_to_location_3d
        except ImportError:
            return {"CANCELLED"}

        coord = (event.mouse_region_x, event.mouse_region_y)
        world = region_2d_to_location_3d(region, rv3d, coord, (0.0, 0.0, 0.0))
        x_mm = m_to_mm(world.x)
        y_mm = m_to_mm(world.y)

        for entry in sorted(page.panels, key=lambda p: -p.z_order):
            if entry.shape_type != "rect":
                continue
            if not (
                entry.rect_x_mm <= x_mm <= entry.rect_x_mm + entry.rect_width_mm
                and entry.rect_y_mm <= y_mm <= entry.rect_y_mm + entry.rect_height_mm
            ):
                continue
            for orig_idx, orig in enumerate(page.panels):
                if orig.panel_stem == entry.panel_stem:
                    page.active_panel_index = orig_idx
                    return {"FINISHED"}
        return {"CANCELLED"}


# ---------- スポイト ----------


class BNAME_OT_view_eyedropper(Operator):
    """スポイト (Blender 標準ペイントモード時の色取得を呼び出す)."""

    bl_idname = "bname.view_eyedropper"
    bl_label = "B-Name スポイト"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        area, region, _ = _find_view3d_window_region(context)
        if area is None:
            return {"CANCELLED"}
        try:
            with bpy.context.temp_override(
                window=context.window, area=area, region=region
            ):
                bpy.ops.ui.eyedropper_color("INVOKE_DEFAULT")
        except Exception:  # noqa: BLE001
            return {"CANCELLED"}
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_view_pan,
    BNAME_OT_view_rotate,
    BNAME_OT_view_zoom_drag,
    BNAME_OT_view_zoom_step,
    BNAME_OT_view_layer_pick,
    BNAME_OT_view_eyedropper,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    # ユーザーがパン/ズーム/回転モーダル中にアドオンを無効化すると、
    # 走行中のモーダルクラス解除で Blender が C レベルクラッシュする危険性が
    # ある。RuntimeError 以外 (segfault 以前の例外) も握り潰す防御。
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            _logger.exception("unregister skipped: %s", cls.__name__)
