"""画像レイヤーを image plane Object 化する operators (Phase 3b).

既存 ``operators/image_layer_op.py`` と GPU overlay (``ui/overlay_image.py``)
は変更せず、追加 operator として「Object 化」を提供する。

提供 operator:
    - ``bname.image_layer_to_object``: 1 つの image_layer entry を Object 化。
    - ``bname.image_layers_all_to_object``: scene 内全 image_layer を Object 化。
"""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..utils import image_plane_object as ipo
from ..utils import log

_logger = log.get_logger(__name__)


def _resolve_active_page(context):
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    work = getattr(scene, "bname_work", None)
    if work is None or not getattr(work, "loaded", False):
        return None
    pages = getattr(work, "pages", None)
    if not pages:
        return None
    idx = int(getattr(work, "active_page_index", 0))
    if not (0 <= idx < len(pages)):
        return None
    return pages[idx]


def _find_page_for_entry(work, entry):
    """entry の parent_key からページを逆引きする."""
    parent_key = str(getattr(entry, "parent_key", "") or "")
    if not parent_key:
        return None
    page_id = parent_key.split(":", 1)[0] if ":" in parent_key else parent_key
    for page in getattr(work, "pages", []):
        if str(getattr(page, "id", "") or "") == page_id:
            return page
    return None


class BNAME_OT_image_layer_to_object(Operator):
    """アクティブな image_layer を image plane Object 化."""

    bl_idname = "bname.image_layer_to_object"
    bl_label = "画像レイヤーを Object 化"
    bl_description = (
        "選択中 (active index) の画像レイヤーを image plane Object として"
        "生成し、Outliner Collection 階層に登録します (Phase 3b)。"
        "GPU overlay は引き続き描画されます (二重描画は material alpha=0 で回避)。"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        coll = getattr(scene, "bname_image_layers", None)
        return bool(coll and len(coll) > 0)

    def execute(self, context):
        scene = context.scene
        coll = scene.bname_image_layers
        idx = int(getattr(scene, "bname_active_image_layer_index", -1))
        if not (0 <= idx < len(coll)):
            idx = 0
        entry = coll[idx]
        from ..core.work import get_work

        work = get_work(context)
        page = _find_page_for_entry(work, entry) if work else None
        if page is None:
            page = _resolve_active_page(context)
        if page is None:
            self.report({"WARNING"}, "ページが特定できません")
            return {"CANCELLED"}
        obj = ipo.ensure_image_plane_object(scene=scene, entry=entry, page=page)
        if obj is None:
            self.report({"ERROR"}, "image plane Object の生成に失敗しました")
            return {"CANCELLED"}
        self.report({"INFO"}, f"image plane 生成: {obj.name}")
        return {"FINISHED"}


class BNAME_OT_image_layers_all_to_object(Operator):
    """全 image_layer を image plane Object 化 (一括)."""

    bl_idname = "bname.image_layers_all_to_object"
    bl_label = "全画像レイヤーを Object 化"
    bl_description = "scene 内の全 image_layer を image plane Object として生成します。"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        coll = getattr(scene, "bname_image_layers", None)
        return bool(coll is not None and len(coll) > 0)

    def execute(self, context):
        scene = context.scene
        from ..core.work import get_work

        work = get_work(context)
        if work is None:
            self.report({"WARNING"}, "work が未ロードです")
            return {"CANCELLED"}
        created = 0
        for entry in scene.bname_image_layers:
            page = _find_page_for_entry(work, entry)
            if page is None:
                page = _resolve_active_page(context)
            if page is None:
                continue
            if ipo.ensure_image_plane_object(scene=scene, entry=entry, page=page):
                created += 1
        self.report({"INFO"}, f"{created} 件の image plane を生成しました")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_image_layer_to_object,
    BNAME_OT_image_layers_all_to_object,
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
