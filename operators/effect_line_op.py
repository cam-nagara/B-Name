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
        # TODO (Phase 3 後半): Grease Pencil v3 API でレイヤー生成
        # - 基準図形の頂点座標算出
        # - 線の角度/本数/長さに従ってストローク配列生成
        # - params.in_percent / out_percent に基づいて入り抜きカーブ
        _logger.info(
            "effect_line_generate (stub): type=%s, shape=%s",
            params.effect_type,
            params.base_shape,
        )
        self.report({"INFO"}, "効果線生成 (Phase 3 骨格): パラメータを保持しました")
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
