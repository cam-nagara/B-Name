"""フキダシ Curve / テキスト Plane Object 化 operators (Phase 4c)."""

from __future__ import annotations

import bpy

from ..utils import balloon_curve_object as bco
from ..utils import log
from ..utils import text_plane_object as tpo

_logger = log.get_logger(__name__)


class BNAME_OT_balloons_to_curve_all(bpy.types.Operator):
    bl_idname = "bname.balloons_to_curve_all"
    bl_label = "全フキダシを Curve として生成"
    bl_description = "全 page.balloons を Bezier Curve Object として生成します (Phase 4c)。"
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


class BNAME_OT_texts_to_plane_all(bpy.types.Operator):
    bl_idname = "bname.texts_to_plane_all"
    bl_label = "全テキストを Plane として生成"
    bl_description = "全 page.texts を typography 画像付き Plane Object として生成 (Phase 4c)。"
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
                if tpo.ensure_text_plane_object(scene=scene, entry=entry, page=page):
                    n += 1
        self.report({"INFO"}, f"{n} 件のテキスト Plane を生成")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_balloons_to_curve_all,
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
