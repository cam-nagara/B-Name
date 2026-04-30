"""コマ ID を順番通り (c01, c02, ...) に振り直す operator.

枠線カット等で coma_id に飛び番が出たとき、ユーザー操作で順番通りに
リネームする。``BNameComaEntry.id`` / ``BNameComaEntry.coma_id`` の両方を
更新し、Outliner Collection 名 (mirror 経由) も追従する。

注意: 物理ファイル名 (cNN.blend / cNN フォルダ) はリネームしない。
ファイル整合は別途ユーザー操作で行う想定。
"""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..utils import log

_logger = log.get_logger(__name__)


def _format_coma_id(index: int) -> str:
    """1 → "c01" のように 2 桁ゼロパディング (3 桁以上は素直にそのまま)."""
    if index < 100:
        return f"c{index:02d}"
    return f"c{index:d}"


def _renumber_page_comas(page) -> int:
    """page.comas の id / coma_id を 1 から順に振り直す。変更件数を返す."""
    comas = getattr(page, "comas", None)
    if comas is None:
        return 0
    changed = 0
    for i, coma in enumerate(comas):
        new_id = _format_coma_id(i + 1)
        old_id = str(getattr(coma, "id", "") or "")
        if old_id != new_id:
            try:
                coma.id = new_id
            except Exception:  # noqa: BLE001
                _logger.exception("coma renumber: id set failed")
                continue
            changed += 1
        old_stem = str(getattr(coma, "coma_id", "") or "")
        if old_stem != new_id:
            try:
                coma.coma_id = new_id
            except Exception:  # noqa: BLE001
                pass
    return changed


class BNAME_OT_coma_renumber_active_page(Operator):
    """アクティブページのコマ ID を順番通りに振り直す."""

    bl_idname = "bname.coma_renumber_active_page"
    bl_label = "コマ ID を順番通り再採番"
    bl_description = (
        "アクティブページの BNameComaEntry を 1 から順に c01/c02/... に "
        "振り直します。枠線カットで飛び番が出た直後に実行してください。"
        "物理ファイル (cNN.blend) はリネームされません。"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from ..core.work import get_work

        work = get_work(context)
        if not (work and getattr(work, "loaded", False)):
            return False
        pages = getattr(work, "pages", None)
        if not pages:
            return False
        idx = int(getattr(work, "active_page_index", 0))
        if not (0 <= idx < len(pages)):
            return False
        return bool(len(pages[idx].comas))

    def execute(self, context):
        from ..core.work import get_work
        from ..utils import layer_object_sync as los
        from ..utils import object_naming as on
        from ..utils import outliner_model as om

        scene = context.scene
        work = get_work(context)
        idx = int(getattr(work, "active_page_index", 0))
        page = work.pages[idx]
        page_id = str(getattr(page, "id", "") or "")
        if not page_id:
            self.report({"WARNING"}, "アクティブページの ID が空です")
            return {"CANCELLED"}

        # 旧 coma_id → 新 coma_id の対応を作る
        old_ids = [str(getattr(c, "id", "") or "") for c in page.comas]
        changed = _renumber_page_comas(page)
        new_ids = [str(getattr(c, "id", "") or "") for c in page.comas]

        # Outliner Collection の bname_id も更新 (旧 "p:c01" → 新 "p:c02" 等)
        with los.suppress_sync():
            for old_id, new_id in zip(old_ids, new_ids):
                if old_id == new_id:
                    continue
                old_coll = on.find_collection_by_bname_id(
                    f"{page_id}:{old_id}", kind="coma"
                )
                if old_coll is not None:
                    # 一時的に bname_id を新値に書き換え
                    old_coll[on.PROP_ID] = f"{page_id}:{new_id}"
            # mirror を再走させて Collection 名と階層を最新化
            los.mirror_work_to_outliner(scene, work)

        self.report(
            {"INFO"},
            f"page {page_id}: {changed} 件のコマ ID を再採番しました",
        )
        return {"FINISHED"}


_CLASSES = (BNAME_OT_coma_renumber_active_page,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
