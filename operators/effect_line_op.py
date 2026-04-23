"""効果線 Operator (Phase 3 骨格).

計画書 3.1.6 参照。ドラッグで範囲指定 → パラメータ調整 → Grease Pencil
レイヤー生成、のうち Phase 3 では「パラメータ保持 + 生成呼び出し (空実装)」
の骨格のみ用意する。実ストローク生成は Phase 3 後半以降。
"""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..utils import log

_logger = log.get_logger(__name__)


class BNAME_OT_effect_line_generate(Operator):
    bl_idname = "bname.effect_line_generate"
    bl_label = "効果線を生成"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bname_effect_line_params", None) is not None

    def execute(self, context):
        params = context.scene.bname_effect_line_params
        from ..utils import gpencil

        from . import effect_line_gen

        # ストローク配列生成
        strokes = effect_line_gen.generate_strokes(params)
        if not strokes:
            self.report({"WARNING"}, "生成するストロークがありません")
            return {"CANCELLED"}

        # Grease Pencil v3 オブジェクト/レイヤー/フレーム確保
        try:
            gp_obj = gpencil.ensure_gpencil_object("BName_EffectLines")
            gp_data = gp_obj.data
            layer_name = f"effect_{params.effect_type}"
            layer = gpencil.ensure_layer(gp_data, layer_name)
            frame = gpencil.ensure_active_frame(layer)
            if frame is None:
                self.report({"ERROR"}, "Grease Pencil フレーム確保失敗")
                return {"CANCELLED"}
            drawing = frame.drawing
            added = 0
            for s in strokes:
                if gpencil.add_stroke_to_drawing(
                    drawing, s.points_xyz, radius=s.radius, cyclic=s.cyclic
                ):
                    added += 1
        except Exception as exc:  # noqa: BLE001
            _logger.exception("effect_line_generate failed")
            self.report({"ERROR"}, f"効果線生成失敗: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"効果線生成: {added}/{len(strokes)} ストローク")
        return {"FINISHED"}


_CLASSES = (BNAME_OT_effect_line_generate,)


def register() -> None:
    from ..core.effect_line import BNameEffectLineParams

    bpy.types.Scene.bname_effect_line_params = bpy.props.PointerProperty(
        type=BNameEffectLineParams
    )
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    try:
        del bpy.types.Scene.bname_effect_line_params
    except AttributeError:
        pass
