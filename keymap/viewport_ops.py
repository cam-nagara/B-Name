"""ビューポート操作モーダルオペレータ (計画書 3.6).

CLIP STUDIO PAINT 準拠のショートカット:
- Space + ドラッグ       → パン (view3d.move 相当)
- Shift + Space + ドラッグ → 回転 (view3d.rotate 相当)
- Ctrl + Space + クリック  → ズームイン (1 ステップ)
- Alt + Space + クリック   → ズームアウト (1 ステップ)
- Ctrl + Space + ドラッグ  → ズーム (連続、view3d.zoom 相当)
- 右クリック (設定で有効時) → スポイト

実装方針:
- Blender 標準の view3d.move / view3d.rotate / view3d.zoom を temp_override
  で呼び出して既存挙動を流用する
- modal Operator ではなく単発 Operator として、既定キーマップの上書きを
  keymap/keymap.py で行う
- カスタムモーダル処理 (1 クリックズーム等) はこのモジュールで実装
"""

from __future__ import annotations

import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator

from ..preferences import get_preferences
from ..utils import log

_logger = log.get_logger(__name__)

ZOOM_IN_DELTA = -120   # Blender の zoom mouse delta は符号が逆 (負がズームイン)
ZOOM_OUT_DELTA = 120
KEYMAP_ZOOM_IN_STEP = 0.9
KEYMAP_ZOOM_OUT_STEP = 1.1


def _find_view3d_context(context):
    """現在マウスカーソルがある VIEW_3D area を temp_override 用に返す."""
    window = context.window
    area = context.area
    region = context.region
    if area is not None and area.type == "VIEW_3D":
        return window, area, region
    # fallback: screen 内の最初の VIEW_3D
    screen = context.screen
    if screen is not None:
        for a in screen.areas:
            if a.type != "VIEW_3D":
                continue
            for r in a.regions:
                if r.type == "WINDOW":
                    return window, a, r
    return None, None, None


class BNAME_OT_view_pan(Operator):
    """Space ドラッグでビューをパン."""

    bl_idname = "bname.view_pan"
    bl_label = "B-Name ビューパン"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        window, area, region = _find_view3d_context(context)
        if area is None:
            return {"CANCELLED"}
        try:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.move("INVOKE_DEFAULT")
        except Exception:  # noqa: BLE001
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_view_rotate(Operator):
    """Shift+Space ドラッグでビューを回転."""

    bl_idname = "bname.view_rotate"
    bl_label = "B-Name ビュー回転"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        window, area, region = _find_view3d_context(context)
        if area is None:
            return {"CANCELLED"}
        try:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.rotate("INVOKE_DEFAULT")
        except Exception:  # noqa: BLE001
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_view_zoom_drag(Operator):
    """Ctrl+Space ドラッグでズーム連続."""

    bl_idname = "bname.view_zoom_drag"
    bl_label = "B-Name ビューズーム (ドラッグ)"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        window, area, region = _find_view3d_context(context)
        if area is None:
            return {"CANCELLED"}
        try:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.zoom("INVOKE_DEFAULT")
        except Exception:  # noqa: BLE001
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_view_zoom_step(Operator):
    """Ctrl+Space クリックで 1 ステップズーム (方向引数)."""

    bl_idname = "bname.view_zoom_step"
    bl_label = "B-Name ビューズーム (1 ステップ)"
    bl_options = {"REGISTER"}

    direction: EnumProperty(  # type: ignore[valid-type]
        items=(("IN", "In", ""), ("OUT", "Out", "")),
        default="IN",
    )

    def execute(self, context):
        window, area, region = _find_view3d_context(context)
        if area is None:
            return {"CANCELLED"}
        delta = -1 if self.direction == "IN" else 1  # view3d.zoom の delta 規約に合わせる
        try:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.zoom(delta=delta)
        except Exception:  # noqa: BLE001
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_view_layer_pick(Operator):
    """Ctrl+Shift+クリックで作画レイヤーを選択 (簡易: 直下のコマを active に)."""

    bl_idname = "bname.view_layer_pick"
    bl_label = "B-Name レイヤー選択"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        # Phase 実装: 画面座標からワールド座標に変換してコマ枠 (rect) にヒット判定
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

        # Z 順降順 (手前優先) でヒット判定
        for i, entry in enumerate(sorted(page.panels, key=lambda p: -p.z_order)):
            if entry.shape_type != "rect":
                continue
            if (
                entry.rect_x_mm <= x_mm <= entry.rect_x_mm + entry.rect_width_mm
                and entry.rect_y_mm <= y_mm <= entry.rect_y_mm + entry.rect_height_mm
            ):
                # collection 内の元 index を探す
                for orig_idx, orig in enumerate(page.panels):
                    if orig.panel_stem == entry.panel_stem:
                        page.active_panel_index = orig_idx
                        return {"FINISHED"}
                break
        return {"CANCELLED"}


class BNAME_OT_view_eyedropper(Operator):
    """スポイト (Blender 標準ペイントモード時の色取得を呼び出す)."""

    bl_idname = "bname.view_eyedropper"
    bl_label = "B-Name スポイト"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        window, area, region = _find_view3d_context(context)
        if area is None:
            return {"CANCELLED"}
        try:
            with bpy.context.temp_override(window=window, area=area, region=region):
                # ペイントモード中はスポイトを呼ぶ
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
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
