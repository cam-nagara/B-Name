"""フキダシ Curve + 画像 / テキスト Empty Object 化 operators.

画像とテキストはオーバーレイ描画方式に統一し、Outliner 上は軽量な Empty
Object のみ置く (D&D / 親子変更 / マスク Modifier の対象になりつつ、
画像生成や典 Pillow 転写を回避してメモリ・応答性を改善)。
"""

from __future__ import annotations

import bpy

from ..utils import balloon_curve_object as bco
from ..utils import empty_layer_object as elo
from ..utils import log

_logger = log.get_logger(__name__)


class BNAME_OT_balloons_to_curve_all(bpy.types.Operator):
    bl_idname = "bname.balloons_to_curve_all"
    bl_label = "全フキダシを Curve として生成"
    bl_description = "全 page.balloons を Bezier Curve Object として生成します。"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work

        work = get_work(context)
        return bool(work and getattr(work, "loaded", False))

    def execute(self, context):
        from ..core.work import get_work

        scene = context.scene
        work = get_work(context)
        n = 0
        for page in getattr(work, "pages", []):
            for entry in getattr(page, "balloons", []):
                if bco.ensure_balloon_curve_object(scene=scene, entry=entry, page=page):
                    n += 1
        self.report({"INFO"}, f"{n} 件のフキダシ Curve を生成")
        return {"FINISHED"}


class BNAME_OT_images_to_empty_all(bpy.types.Operator):
    """画像レイヤーを Empty Object として Outliner に登録 (描画はオーバーレイ)."""

    bl_idname = "bname.images_to_empty_all"
    bl_label = "全画像レイヤーを Empty として登録"
    bl_description = (
        "画像レイヤーを Outliner 上の Empty Object として登録します。"
        "実際の絵柄は B-Name 独自オーバーレイで描画され、export pipeline は "
        "PropertyGroup から直接 Pillow 合成するためレンダリング結果は変わり"
        "ません。"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work

        work = get_work(context)
        if not (work and getattr(work, "loaded", False)):
            return False
        return getattr(context.scene, "bname_image_layers", None) is not None

    def execute(self, context):
        from ..core.work import get_work

        scene = context.scene
        work = get_work(context)
        coll = getattr(scene, "bname_image_layers", None) or []
        n = 0
        for entry in coll:
            # 画像 entry の所属 page を逆引き (parent_key の page 部分)
            parent_key = str(getattr(entry, "parent_key", "") or "")
            page_id = parent_key.split(":", 1)[0] if parent_key else ""
            page = None
            for p in getattr(work, "pages", []):
                if str(getattr(p, "id", "") or "") == page_id:
                    page = p
                    break
            if page is None and len(work.pages) > 0:
                page = work.pages[int(getattr(work, "active_page_index", 0) or 0)]
            if page is None:
                continue
            if elo.ensure_image_empty_object(scene=scene, entry=entry, page=page):
                n += 1
        self.report({"INFO"}, f"{n} 件の画像 Empty を登録")
        return {"FINISHED"}


class BNAME_OT_texts_to_empty_all(bpy.types.Operator):
    """テキストレイヤーを Empty Object として Outliner に登録 (描画はオーバーレイ)."""

    bl_idname = "bname.texts_to_empty_all"
    bl_label = "全テキストを Empty として登録"
    bl_description = (
        "テキストを Outliner 上の Empty Object として登録します。実際の文字は "
        "B-Name 独自オーバーレイで描画され、export pipeline は PropertyGroup "
        "から typography で直接 Pillow 合成するためレンダリング結果は変わり"
        "ません。"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work

        work = get_work(context)
        return bool(work and getattr(work, "loaded", False))

    def execute(self, context):
        from ..core.work import get_work

        scene = context.scene
        work = get_work(context)
        n = 0
        for page in getattr(work, "pages", []):
            for entry in getattr(page, "texts", []):
                if elo.ensure_text_empty_object(scene=scene, entry=entry, page=page):
                    n += 1
        self.report({"INFO"}, f"{n} 件のテキスト Empty を登録")
        return {"FINISHED"}


# 後方互換: 旧 op 名 bname.texts_to_plane_all を Empty 版にエイリアス
class BNAME_OT_texts_to_plane_all(BNAME_OT_texts_to_empty_all):
    bl_idname = "bname.texts_to_plane_all"
    bl_label = "全テキストを Empty として登録 (旧名)"


_CLASSES = (
    BNAME_OT_balloons_to_curve_all,
    BNAME_OT_images_to_empty_all,
    BNAME_OT_texts_to_empty_all,
    BNAME_OT_texts_to_plane_all,
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
