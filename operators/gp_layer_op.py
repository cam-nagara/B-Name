"""1 GP Object = 1 B-Name レイヤー operators.

提供 operator:
    - ``bname.gp_layer_create_per_object``: 現在のアクティブコマ直下に新規
      GP Object を生成し、Outliner Collection 階層に正規 link する。
"""

from __future__ import annotations

import uuid

import bpy
from bpy.props import IntProperty, StringProperty

from ..utils import gp_object_layer as gpol
from ..utils import log
from ..utils import object_naming as on

_logger = log.get_logger(__name__)


def _make_gp_bname_id() -> str:
    """``gp_<uuid12>`` 形式の安定 ID を生成 (衝突時はフル UUID へ拡張)."""
    for _ in range(10):
        candidate = f"gp_{uuid.uuid4().hex[:12]}"
        if on.find_object_by_bname_id(candidate, kind="gp") is None:
            return candidate
    # 10 回連続衝突 (天文学的低確率) はフル uuid hex で再試行
    full = f"gp_{uuid.uuid4().hex}"
    if on.find_object_by_bname_id(full, kind="gp") is None:
        return full
    raise RuntimeError("gp bname_id 生成に失敗しました (UUID 衝突)")


def _resolve_active_coma(context):
    """アクティブページとコマを (page_id, coma_id, title) で返す.

    優先順位:
        1. ``scene.bname_current_coma_id`` が設定されていれば、そのコマ ID を
           採用 (cNN.blend 編集中などコマ単独編集モード)。
        2. ``page.active_coma_index`` (BNamePageEntry の IntProperty) を使う。
        3. それ以外は最初のコマ (0 番目)。

    アクティブコマが特定できない場合 ``coma_id=None`` を返し、呼出側で
    ページ直下を採用するか警告するかを判断する。
    """
    scene = getattr(context, "scene", None)
    if scene is None:
        return None, None, None
    work = getattr(scene, "bname_work", None)
    if work is None or not getattr(work, "loaded", False):
        return None, None, None
    pages = getattr(work, "pages", None)
    if not pages:
        return None, None, None
    idx = int(getattr(work, "active_page_index", 0))
    if not (0 <= idx < len(pages)):
        return None, None, None
    page = pages[idx]
    page_id = str(getattr(page, "id", "") or "")
    comas = getattr(page, "comas", None)
    if not comas:
        return page_id, None, None

    # 1. scene.bname_current_coma_id (cNN.blend 編集中) を最優先
    current_coma_id = str(getattr(scene, "bname_current_coma_id", "") or "")
    if current_coma_id:
        for coma in comas:
            if str(getattr(coma, "id", "") or "") == current_coma_id:
                title = str(getattr(coma, "title", "") or current_coma_id)
                return page_id, current_coma_id, title

    # 2. page.active_coma_index (BNamePageEntry.active_coma_index)
    coma_idx = int(getattr(page, "active_coma_index", 0))
    if not (0 <= coma_idx < len(comas)):
        coma_idx = 0
    coma = comas[coma_idx]
    coma_id = str(getattr(coma, "id", "") or "")
    coma_title = str(getattr(coma, "title", "") or coma_id)
    return page_id, coma_id, coma_title


class BNAME_OT_gp_layer_create_per_object(bpy.types.Operator):
    """新規 GP Object を 1 レイヤーとしてアクティブコマに作成 (Phase 2)."""

    bl_idname = "bname.gp_layer_create_per_object"
    bl_label = "新 GP レイヤーを作成"
    bl_description = (
        "アクティブコマ直下に新規 GP Object を生成し、B-Name 安定 ID を付与"
        "して Outliner 階層に登録します (Phase 2: 1 GP Object = 1 B-Name "
        "レイヤー モデル)。"
    )
    bl_options = {"REGISTER", "UNDO"}

    title: StringProperty(  # type: ignore[valid-type]
        name="表示名",
        default="新規GPレイヤー",
    )
    z_index: IntProperty(  # type: ignore[valid-type]
        name="z_index",
        default=100,
        min=0,
    )

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        work = getattr(scene, "bname_work", None) if scene is not None else None
        return bool(work and getattr(work, "loaded", False))

    def execute(self, context):
        scene = context.scene
        from ..utils import active_target as _at

        parent_kind, parent_key, page = _at.resolve_active_target(context)
        if page is None:
            self.report({"WARNING"}, "アクティブページが見つかりません")
            return {"CANCELLED"}
        bname_id = _make_gp_bname_id()
        obj = gpol.create_layer_gp_object(
            scene=scene,
            bname_id=bname_id,
            title=self.title,
            z_index=int(self.z_index),
            parent_kind=parent_kind,
            parent_key=parent_key,
        )
        if obj is None:
            self.report({"ERROR"}, "GP Object 生成に失敗しました")
            return {"CANCELLED"}
        # アクティブ選択にも反映
        try:
            for o in bpy.data.objects:
                o.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
        except Exception:  # noqa: BLE001
            pass
        self.report({"INFO"}, f"GP レイヤー Object 生成: {obj.name}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_gp_layer_create_per_object,
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
