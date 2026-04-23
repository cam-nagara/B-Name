"""フキダシ関連 Operator (Phase 3 骨格).

Phase 3 段階では PropertyGroup への追加/削除/種別変更の最小 Operator の
み。形状ごとの頂点ベース描画・パスツールによるカスタム形状登録は
Phase 3 後半以降で拡張。
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator, Scene

from ..core.work import get_active_page, get_work
from ..io import balloon_presets
from ..utils import log

_logger = log.get_logger(__name__)

_SHAPE_FOR_ADD = (
    ("rect", "矩形", ""),
    ("ellipse", "楕円", ""),
    ("cloud", "雲", ""),
    ("spike_curve", "トゲ (曲線)", ""),
    ("spike_straight", "トゲ (直線)", ""),
    ("none", "本体なし (テキスト単体)", ""),
)


def _get_balloons_collection(scene: Scene):
    """Scene にフキダシコレクションを lazy に確保.

    Phase 3 骨格では Scene に attach しておき、将来ページ/コマ配下に移す
    設計変更が発生しても移行しやすいよう 1 層のコレクションで保持。
    """
    if not hasattr(scene, "bname_balloons"):
        return None
    return scene.bname_balloons


class BNAME_OT_balloon_add(Operator):
    bl_idname = "bname.balloon_add"
    bl_label = "フキダシを追加"
    bl_options = {"REGISTER", "UNDO"}

    shape: EnumProperty(  # type: ignore[valid-type]
        name="形状",
        items=_SHAPE_FOR_ADD,
        default="rect",
    )

    @classmethod
    def poll(cls, context):
        return get_active_page(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        balloons = _get_balloons_collection(context.scene)
        if balloons is None:
            self.report({"ERROR"}, "balloon collection が初期化されていません")
            return {"CANCELLED"}
        entry = balloons.add()
        entry.id = f"balloon_{len(balloons):04d}"
        entry.shape = self.shape
        # 既定で画面中央あたりに配置
        entry.x_mm = 100.0
        entry.y_mm = 200.0
        entry.width_mm = 40.0
        entry.height_mm = 20.0
        entry.rounded_corner_enabled = (self.shape == "rect")
        context.scene.bname_active_balloon_index = len(balloons) - 1
        self.report({"INFO"}, f"フキダシ追加: {entry.id} ({self.shape})")
        return {"FINISHED"}


class BNAME_OT_balloon_remove(Operator):
    bl_idname = "bname.balloon_remove"
    bl_label = "フキダシを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        balloons = _get_balloons_collection(context.scene)
        if balloons is None:
            return False
        idx = getattr(context.scene, "bname_active_balloon_index", -1)
        return 0 <= idx < len(balloons)

    def execute(self, context):
        balloons = _get_balloons_collection(context.scene)
        if balloons is None:
            return {"CANCELLED"}
        idx = context.scene.bname_active_balloon_index
        if not (0 <= idx < len(balloons)):
            return {"CANCELLED"}
        bid = balloons[idx].id
        balloons.remove(idx)
        if len(balloons) == 0:
            context.scene.bname_active_balloon_index = -1
        elif idx >= len(balloons):
            context.scene.bname_active_balloon_index = len(balloons) - 1
        self.report({"INFO"}, f"フキダシ削除: {bid}")
        return {"FINISHED"}


class BNAME_OT_balloon_tail_add(Operator):
    bl_idname = "bname.balloon_tail_add"
    bl_label = "尻尾を追加"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        balloons = _get_balloons_collection(context.scene)
        if balloons is None:
            return False
        idx = getattr(context.scene, "bname_active_balloon_index", -1)
        return 0 <= idx < len(balloons)

    def execute(self, context):
        balloons = _get_balloons_collection(context.scene)
        idx = context.scene.bname_active_balloon_index
        entry = balloons[idx]
        tail = entry.tails.add()
        tail.type = "straight"
        tail.length_mm = 6.0
        tail.root_width_mm = 3.0
        return {"FINISHED"}


class BNAME_OT_balloon_save_preset(Operator):
    """選択中フキダシの形状をカスタムプリセット JSON として保存."""

    bl_idname = "bname.balloon_save_preset"
    bl_label = "カスタム形状として保存"
    bl_options = {"REGISTER"}

    preset_name: StringProperty(name="プリセット名", default="新規フキダシ")  # type: ignore[valid-type]
    description: StringProperty(name="説明", default="")  # type: ignore[valid-type]
    absolute_coords: BoolProperty(name="絶対座標で登録", default=False)  # type: ignore[valid-type]
    to_global: BoolProperty(  # type: ignore[valid-type]
        name="グローバルに登録",
        description="ON: <addon>/presets/balloons/ に保存 / OFF: 作品ローカル",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        balloons = _get_balloons_collection(context.scene)
        if balloons is None:
            return False
        idx = getattr(context.scene, "bname_active_balloon_index", -1)
        return 0 <= idx < len(balloons)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        balloons = _get_balloons_collection(context.scene)
        idx = context.scene.bname_active_balloon_index
        entry = balloons[idx]
        # Phase 3 骨格: 矩形 4 頂点を保存。パスツール実装後は任意形状へ。
        verts = [
            (entry.x_mm, entry.y_mm),
            (entry.x_mm + entry.width_mm, entry.y_mm),
            (entry.x_mm + entry.width_mm, entry.y_mm + entry.height_mm),
            (entry.x_mm, entry.y_mm + entry.height_mm),
        ]
        try:
            if self.to_global:
                out = balloon_presets.save_global_preset(
                    self.preset_name, self.description, verts, self.absolute_coords
                )
            else:
                work = get_work(context)
                if work is None or not work.loaded or not work.work_dir:
                    self.report({"ERROR"}, "ローカル保存には作品を開く必要があります")
                    return {"CANCELLED"}
                out = balloon_presets.save_local_preset(
                    Path(work.work_dir),
                    self.preset_name,
                    self.description,
                    verts,
                    self.absolute_coords,
                )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("balloon_save_preset failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"フキダシプリセット保存: {out.name}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_balloon_add,
    BNAME_OT_balloon_remove,
    BNAME_OT_balloon_tail_add,
    BNAME_OT_balloon_save_preset,
)


def register() -> None:
    from ..core.balloon import BNameBalloonEntry

    bpy.types.Scene.bname_balloons = bpy.props.CollectionProperty(type=BNameBalloonEntry)
    bpy.types.Scene.bname_active_balloon_index = bpy.props.IntProperty(default=-1, min=-1)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    for attr in ("bname_active_balloon_index", "bname_balloons"):
        try:
            delattr(bpy.types.Scene, attr)
        except AttributeError:
            pass
