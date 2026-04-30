"""balloon / text レイヤーを plane Object 化する operators (Phase 4).

既存 ``operators/balloon_op.py`` ``operators/text_op.py`` と GPU overlay
は変更せず、追加 operator として「Object 化」を提供する。

提供 operator:
    - ``bname.balloon_to_object``: アクティブ balloon を Object 化
    - ``bname.balloons_all_to_object``: 全 balloon を一括 Object 化
    - ``bname.text_to_object``: アクティブ text を Object 化
    - ``bname.texts_all_to_object``: 全 text を一括 Object 化
"""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..utils import balloon_text_plane as btp
from ..utils import log

_logger = log.get_logger(__name__)


def _all_balloons_with_pages(work):
    for page in getattr(work, "pages", []):
        for entry in getattr(page, "balloons", []):
            yield page, entry


def _all_texts_with_pages(work):
    for page in getattr(work, "pages", []):
        for entry in getattr(page, "texts", []):
            yield page, entry


class BNAME_OT_balloon_to_object(Operator):
    bl_idname = "bname.balloon_to_object"
    bl_label = "フキダシを Object 化"
    bl_description = (
        "アクティブページのアクティブフキダシを balloon plane Object として"
        "生成し、Outliner Collection 階層に登録します (Phase 4)。GPU overlay は"
        "引き続き描画されます。"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work

        work = get_work(context)
        if work is None or not getattr(work, "loaded", False):
            return False
        pages = getattr(work, "pages", None)
        if not pages:
            return False
        idx = int(getattr(work, "active_page_index", 0))
        if not (0 <= idx < len(pages)):
            return False
        return bool(len(pages[idx].balloons))

    def execute(self, context):
        from ..core.work import get_work

        scene = context.scene
        work = get_work(context)
        idx = int(getattr(work, "active_page_index", 0))
        page = work.pages[idx]
        b_idx = int(getattr(page, "active_balloon_index", 0))
        if not (0 <= b_idx < len(page.balloons)):
            b_idx = 0
        entry = page.balloons[b_idx]
        obj = btp.ensure_balloon_object(scene=scene, entry=entry, page=page)
        if obj is None:
            self.report({"ERROR"}, "balloon Object 生成失敗")
            return {"CANCELLED"}
        self.report({"INFO"}, f"balloon plane: {obj.name}")
        return {"FINISHED"}


class BNAME_OT_balloons_all_to_object(Operator):
    bl_idname = "bname.balloons_all_to_object"
    bl_label = "全フキダシを Object 化"
    bl_description = "scene 内の全 balloon を一括で plane Object 化します。"
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
        created = 0
        for page, entry in _all_balloons_with_pages(work):
            if btp.ensure_balloon_object(scene=scene, entry=entry, page=page):
                created += 1
        self.report({"INFO"}, f"{created} 件の balloon plane を生成しました")
        return {"FINISHED"}


class BNAME_OT_text_to_object(Operator):
    bl_idname = "bname.text_to_object"
    bl_label = "テキストを Object 化"
    bl_description = (
        "アクティブページのアクティブテキストを text plane Object として"
        "生成し、Outliner 階層に登録します (Phase 4: placeholder 画像)。"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work

        work = get_work(context)
        if work is None or not getattr(work, "loaded", False):
            return False
        pages = getattr(work, "pages", None)
        if not pages:
            return False
        idx = int(getattr(work, "active_page_index", 0))
        if not (0 <= idx < len(pages)):
            return False
        return bool(len(pages[idx].texts))

    def execute(self, context):
        from ..core.work import get_work

        scene = context.scene
        work = get_work(context)
        idx = int(getattr(work, "active_page_index", 0))
        page = work.pages[idx]
        t_idx = int(getattr(page, "active_text_index", 0))
        if not (0 <= t_idx < len(page.texts)):
            t_idx = 0
        entry = page.texts[t_idx]
        obj = btp.ensure_text_plane_object(scene=scene, entry=entry, page=page)
        if obj is None:
            self.report({"ERROR"}, "text Object 生成失敗")
            return {"CANCELLED"}
        self.report({"INFO"}, f"text plane: {obj.name}")
        return {"FINISHED"}


class BNAME_OT_texts_all_to_object(Operator):
    bl_idname = "bname.texts_all_to_object"
    bl_label = "全テキストを Object 化"
    bl_description = "scene 内の全 text を一括で plane Object 化します。"
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
        created = 0
        for page, entry in _all_texts_with_pages(work):
            if btp.ensure_text_plane_object(scene=scene, entry=entry, page=page):
                created += 1
        self.report({"INFO"}, f"{created} 件の text plane を生成しました")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_balloon_to_object,
    BNAME_OT_balloons_all_to_object,
    BNAME_OT_text_to_object,
    BNAME_OT_texts_all_to_object,
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
