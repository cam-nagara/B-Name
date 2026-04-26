"""テキスト (縦書きセリフ/ナレーション/擬音) の Operator (Phase 3).

- 各ページの ``page.texts`` CollectionProperty にテキストを追加/削除
- invoke ではマウス直下のページを逆引きして active に追随
- フキダシへの attach/detach をサポート (``parent_balloon_id``)
- overlay 上の座標はページローカル mm。描画時に grid offset を加算する。
"""

from __future__ import annotations

import bpy
from bpy.props import EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..utils import log

_logger = log.get_logger(__name__)


_SPEAKER_TYPE_ITEMS = (
    ("normal", "通常セリフ", ""),
    ("thought", "思考", ""),
    ("shout", "叫び", ""),
    ("narration", "ナレーション", ""),
    ("monologue", "モノローグ", ""),
    ("sfx", "擬音", ""),
)


def _allocate_text_id(page) -> str:
    used = {t.id for t in page.texts}
    i = 1
    while True:
        candidate = f"text_{i:04d}"
        if candidate not in used:
            return candidate
        i += 1


def _resolve_page_from_event(context, event):
    """balloon_op と同じロジックでページ + local mm 座標を解決."""
    from . import balloon_op

    return balloon_op._resolve_page_from_event(context, event)


class BNAME_OT_text_add(Operator):
    """アクティブページにテキストを追加. マウス位置から座標決定."""

    bl_idname = "bname.text_add"
    bl_label = "テキストを追加"
    bl_options = {"REGISTER", "UNDO"}

    body: StringProperty(name="本文", default="")  # type: ignore[valid-type]
    speaker_type: EnumProperty(  # type: ignore[valid-type]
        name="種別",
        items=_SPEAKER_TYPE_ITEMS,
        default="normal",
    )
    x_mm: FloatProperty(name="X (mm)", default=0.0)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y (mm)", default=0.0)  # type: ignore[valid-type]
    width_mm: FloatProperty(name="幅 (mm)", default=30.0, min=0.1)  # type: ignore[valid-type]
    height_mm: FloatProperty(name="高さ (mm)", default=15.0, min=0.1)  # type: ignore[valid-type]
    parent_balloon_id: StringProperty(  # type: ignore[valid-type]
        name="親フキダシ ID",
        description="同じページの BNameBalloonEntry.id を指定 (空で独立テキスト)",
        default="",
    )

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded and get_active_page(context) is not None

    def invoke(self, context, event):
        work, page, lx, ly = _resolve_page_from_event(context, event)
        if work is None or page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        if lx is not None and ly is not None:
            # マウス位置を中央に. 親フキダシが指定済なら後で上書き
            self.x_mm = lx - self.width_mm / 2.0
            self.y_mm = ly - self.height_mm / 2.0
        else:
            self.x_mm = work.paper.canvas_width_mm / 2.0 - self.width_mm / 2.0
            self.y_mm = work.paper.canvas_height_mm / 2.0 - self.height_mm / 2.0
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        entry = page.texts.add()
        entry.id = _allocate_text_id(page)
        entry.body = self.body
        entry.speaker_type = self.speaker_type
        entry.x_mm = self.x_mm
        entry.y_mm = self.y_mm
        entry.width_mm = self.width_mm
        entry.height_mm = self.height_mm
        # 親フキダシ指定があれば、対応フキダシの位置に追従する (中央合わせ)
        if self.parent_balloon_id:
            for b in page.balloons:
                if b.id == self.parent_balloon_id:
                    entry.parent_balloon_id = self.parent_balloon_id
                    entry.x_mm = b.x_mm + (b.width_mm - entry.width_mm) / 2.0
                    entry.y_mm = b.y_mm + (b.height_mm - entry.height_mm) / 2.0
                    break
            else:
                self.report(
                    {"WARNING"},
                    f"親フキダシ {self.parent_balloon_id} が見つかりません (独立テキストとして追加)",
                )
        page.active_text_index = len(page.texts) - 1
        if hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "text"
        self.report({"INFO"}, f"テキスト追加: {entry.id}")
        return {"FINISHED"}


class BNAME_OT_text_remove(Operator):
    bl_idname = "bname.text_remove"
    bl_label = "テキストを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_text_index < len(page.texts)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_text_index
        if not (0 <= idx < len(page.texts)):
            return {"CANCELLED"}
        tid = page.texts[idx].id
        page.texts.remove(idx)
        if len(page.texts) == 0:
            page.active_text_index = -1
        elif idx >= len(page.texts):
            page.active_text_index = len(page.texts) - 1
        if len(page.texts) == 0 and hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "gp"
        self.report({"INFO"}, f"テキスト削除: {tid}")
        return {"FINISHED"}


class BNAME_OT_text_attach_to_balloon(Operator):
    """アクティブテキストをアクティブフキダシへ attach (親子連動対象化).

    空文字でも実行: 現在の親子連携を解除して独立テキスト化する。
    """

    bl_idname = "bname.text_attach_to_balloon"
    bl_label = "テキストをフキダシに紐付け"
    bl_options = {"REGISTER", "UNDO"}

    balloon_id: StringProperty(  # type: ignore[valid-type]
        name="フキダシ ID",
        description="空で親子関係を解除 (独立テキスト化)",
        default="",
    )

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_text_index < len(page.texts)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_text_index
        if not (0 <= idx < len(page.texts)):
            return {"CANCELLED"}
        txt = page.texts[idx]
        target_id = self.balloon_id.strip()
        if not target_id:
            txt.parent_balloon_id = ""
            self.report({"INFO"}, "テキストを独立化しました")
            return {"FINISHED"}
        # 指定 ID のフキダシが同じページに存在するか確認
        for b in page.balloons:
            if b.id == target_id:
                txt.parent_balloon_id = target_id
                # 位置を当該フキダシの中央に合わせる
                txt.x_mm = b.x_mm + (b.width_mm - txt.width_mm) / 2.0
                txt.y_mm = b.y_mm + (b.height_mm - txt.height_mm) / 2.0
                self.report({"INFO"}, f"テキストを紐付け: {target_id}")
                return {"FINISHED"}
        self.report({"ERROR"}, f"フキダシが見つかりません: {target_id}")
        return {"CANCELLED"}


_CLASSES = (
    BNAME_OT_text_add,
    BNAME_OT_text_remove,
    BNAME_OT_text_attach_to_balloon,
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
