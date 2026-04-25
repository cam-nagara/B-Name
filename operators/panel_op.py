"""コマ追加・削除・複製・移動・Z順序変更 Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..io import page_io, panel_io, schema
from ..utils import log, paths

_logger = log.get_logger(__name__)


# ---------- 共通ヘルパ ----------


def _require_active_page(op: Operator, context):
    work = get_work(context)
    page = get_active_page(context)
    if work is None or not work.loaded or page is None:
        op.report({"ERROR"}, "作品 / ページが選択されていません")
        return None, None
    return work, page


def _save_page_and_pages(work, page, work_dir: Path) -> None:
    page_io.save_page_json(work_dir, page)
    page.panel_count = len(page.panels)
    page_io.save_pages_json(work_dir, work)


# ---------- コマ生成ヘルパ (page_op などから再利用) ----------


def create_rect_panel(
    work,
    page,
    work_dir: Path,
    x_mm: float,
    y_mm: float,
    width_mm: float,
    height_mm: float,
    title: str | None = None,
):
    """指定矩形の新規コマを page.panels に追加し、panel.json と page.json を保存.

    pages.json の保存は呼出側の責務。新規 entry を返す。
    """
    stem = panel_io.allocate_new_panel_stem(work_dir, page.id)
    entry = page.panels.add()
    entry.panel_stem = stem
    entry.id = stem.split("_", 1)[1]
    entry.title = title if title is not None else stem
    entry.shape_type = "rect"
    entry.rect_x_mm = x_mm
    entry.rect_y_mm = y_mm
    entry.rect_width_mm = width_mm
    entry.rect_height_mm = height_mm
    # 追加直後の entry を除いて max を取る (entry 自身は初期値 0)
    max_z = max(
        (pe.z_order for pe in page.panels if pe is not entry),
        default=-1,
    )
    entry.z_order = max_z + 1
    page.active_panel_index = len(page.panels) - 1
    panel_io.save_panel_meta(work_dir, page.id, entry)
    page_io.save_page_json(work_dir, page)
    page.panel_count = len(page.panels)
    return entry


def create_basic_frame_panel(work, page, work_dir: Path):
    """基本枠 (用紙の inner_frame) サイズの矩形コマを 1 個生成して返す."""
    p = work.paper
    x_mm = (p.canvas_width_mm - p.inner_frame_width_mm) / 2.0 + p.inner_frame_offset_x_mm
    y_mm = (p.canvas_height_mm - p.inner_frame_height_mm) / 2.0 + p.inner_frame_offset_y_mm
    return create_rect_panel(
        work,
        page,
        work_dir,
        x_mm,
        y_mm,
        p.inner_frame_width_mm,
        p.inner_frame_height_mm,
        title="基本枠",
    )


# ---------- コマ追加 ----------


class BNAME_OT_panel_add(Operator):
    """現在のページに矩形コマを追加."""

    bl_idname = "bname.panel_add"
    bl_label = "コマを追加"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return get_active_page(context) is not None

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        try:
            # デフォルトは中央に 60×40mm の矩形
            p = work.paper
            x_mm = (p.canvas_width_mm - 60.0) / 2.0
            y_mm = (p.canvas_height_mm - 40.0) / 2.0
            entry = create_rect_panel(work, page, work_dir, x_mm, y_mm, 60.0, 40.0)
            stem = entry.panel_stem
            page_io.save_pages_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_add failed")
            self.report({"ERROR"}, f"コマ追加失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ追加: {stem}")
        return {"FINISHED"}


class BNAME_OT_panel_remove(Operator):
    """選択中のコマを削除."""

    bl_idname = "bname.panel_remove"
    bl_label = "コマを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return page is not None and 0 <= page.active_panel_index < len(page.panels)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_panel_index
        if not (0 <= idx < len(page.panels)):
            return {"CANCELLED"}
        entry = page.panels[idx]
        stem = entry.panel_stem
        work_dir = Path(work.work_dir)
        try:
            panel_io.remove_panel_files(work_dir, page.id, stem)
            page.panels.remove(idx)
            if len(page.panels) == 0:
                page.active_panel_index = -1
            elif idx >= len(page.panels):
                page.active_panel_index = len(page.panels) - 1
            _save_page_and_pages(work, page, work_dir)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_remove failed")
            self.report({"ERROR"}, f"コマ削除失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ削除: {stem}")
        return {"FINISHED"}


class BNAME_OT_panel_duplicate(Operator):
    """選択中のコマを同ページ内で複製."""

    bl_idname = "bname.panel_duplicate"
    bl_label = "コマを複製"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return page is not None and 0 <= page.active_panel_index < len(page.panels)

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_panel_index
        src = page.panels[idx]
        work_dir = Path(work.work_dir)
        try:
            new_stem = panel_io.allocate_new_panel_stem(work_dir, page.id)
            panel_io.copy_panel_files(work_dir, page.id, page.id, src.panel_stem, new_stem)
            # entry を複製
            new_entry = page.panels.add()
            _copy_panel_entry(src, new_entry)
            new_entry.panel_stem = new_stem
            new_entry.id = new_stem.split("_", 1)[1]
            new_entry.title = f"{src.title} (複製)"
            new_entry.rect_x_mm = src.rect_x_mm + 5.0
            new_entry.rect_y_mm = src.rect_y_mm - 5.0
            new_entry.z_order = max((p.z_order for p in page.panels), default=0) + 1
            # 直後に配置
            new_index = len(page.panels) - 1
            if new_index != idx + 1:
                page.panels.move(new_index, idx + 1)
            page.active_panel_index = idx + 1
            panel_io.save_panel_meta(work_dir, page.id, new_entry)
            _save_page_and_pages(work, page, work_dir)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_duplicate failed")
            self.report({"ERROR"}, f"コマ複製失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ複製: {src.panel_stem} → {new_stem}")
        return {"FINISHED"}


def _copy_panel_entry(src, dst) -> None:
    """PanelEntry の内容を複製.

    PointerProperty (border / white_margin) と CollectionProperty
    (vertices / layer_refs) を含めてすべてコピーするため、schema 経由で
    dict を往復させる。dst.id / dst.panel_stem は呼出側で上書きされる想定。
    """
    data = schema.panel_entry_to_dict(src)
    schema.panel_entry_from_dict(dst, data)


# ---------- 他ページへの移動 ----------


def _other_page_enum_items(_self, context):
    work = get_work(context)
    active_idx = work.active_page_index if work else -1
    cache: list[tuple[str, str, str]] = []
    if work is None:
        return [("", "(ページなし)", "")]
    for i, p in enumerate(work.pages):
        if i == active_idx:
            continue
        cache.append((p.id, p.title or p.id, ""))
    if not cache:
        cache.append(("", "(他のページなし)", ""))
    _OTHER_PAGE_CACHE[:] = cache
    return _OTHER_PAGE_CACHE


_OTHER_PAGE_CACHE: list[tuple[str, str, str]] = []


class BNAME_OT_panel_move_to_page(Operator):
    """選択中のコマを別のページへ移動."""

    bl_idname = "bname.panel_move_to_page"
    bl_label = "コマを他ページへ移動"
    bl_options = {"REGISTER", "UNDO"}

    target_page_id: EnumProperty(  # type: ignore[valid-type]
        name="移動先ページ",
        items=_other_page_enum_items,
    )

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        page = get_active_page(context)
        return (
            w is not None
            and w.loaded
            and page is not None
            and 0 <= page.active_panel_index < len(page.panels)
            and len(w.pages) >= 2
        )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        if not self.target_page_id:
            self.report({"ERROR"}, "移動先ページを選択してください")
            return {"CANCELLED"}
        # 移動先ページ
        target_page = None
        for p in work.pages:
            if p.id == self.target_page_id:
                target_page = p
                break
        if target_page is None:
            self.report({"ERROR"}, f"移動先ページが見つかりません: {self.target_page_id}")
            return {"CANCELLED"}
        idx = page.active_panel_index
        src_entry = page.panels[idx]
        work_dir = Path(work.work_dir)
        try:
            # 移動先で衝突しないファイル名を採番
            dst_stem = panel_io.allocate_new_panel_stem(work_dir, target_page.id)
            panel_io.move_panel_files(
                work_dir, page.id, target_page.id, src_entry.panel_stem, dst_stem
            )
            # 移動先 collection に追加
            new_entry = target_page.panels.add()
            _copy_panel_entry(src_entry, new_entry)
            new_entry.panel_stem = dst_stem
            new_entry.id = dst_stem.split("_", 1)[1]
            new_entry.title = src_entry.title
            # 元の collection から削除
            page.panels.remove(idx)
            if len(page.panels) == 0:
                page.active_panel_index = -1
            elif idx >= len(page.panels):
                page.active_panel_index = len(page.panels) - 1
            page_io.save_page_json(work_dir, page)
            page_io.save_page_json(work_dir, target_page)
            page.panel_count = len(page.panels)
            target_page.panel_count = len(target_page.panels)
            page_io.save_pages_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_move_to_page failed")
            self.report({"ERROR"}, f"コマ移動失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"コマ移動: → {target_page.id}/{dst_stem}")
        return {"FINISHED"}


# ---------- Z順序 ----------


class BNAME_OT_panel_z_order(Operator):
    """選択中のコマの Z 順序を変更."""

    bl_idname = "bname.panel_z_order"
    bl_label = "Z順序変更"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(  # type: ignore[valid-type]
        name="方向",
        items=(
            ("FRONT", "最前面", ""),
            ("BACK", "最背面", ""),
            ("FORWARD", "前面へ", ""),
            ("BACKWARD", "背面へ", ""),
        ),
    )

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return page is not None and 0 <= page.active_panel_index < len(page.panels)

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_panel_index
        current = page.panels[idx].z_order
        all_orders = [p.z_order for p in page.panels]
        mn, mx = (min(all_orders), max(all_orders)) if all_orders else (0, 0)
        if self.direction == "FRONT":
            page.panels[idx].z_order = mx + 1
        elif self.direction == "BACK":
            page.panels[idx].z_order = mn - 1
        elif self.direction == "FORWARD":
            page.panels[idx].z_order = current + 1
        elif self.direction == "BACKWARD":
            page.panels[idx].z_order = current - 1
        work_dir = Path(work.work_dir)
        try:
            panel_io.save_panel_meta(work_dir, page.id, page.panels[idx])
            page_io.save_page_json(work_dir, page)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_z_order failed")
            self.report({"ERROR"}, f"Z順序変更失敗: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


# ---------- 分割テンプレート ----------


class BNAME_OT_panel_split_template(Operator):
    """基本枠を縦 / 横に均等分割してコマを一括生成."""

    bl_idname = "bname.panel_split_template"
    bl_label = "分割テンプレートで一括生成"
    bl_options = {"REGISTER", "UNDO"}

    rows: IntProperty(name="行数", default=3, min=1, soft_max=10)  # type: ignore[valid-type]
    cols: IntProperty(name="列数", default=2, min=1, soft_max=10)  # type: ignore[valid-type]
    clear_existing: BoolProperty(  # type: ignore[valid-type]
        name="既存コマを削除",
        description="ON で現在のページのコマを全削除してから生成",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return get_active_page(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work, page = _require_active_page(self, context)
        if page is None:
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        p = work.paper
        # 基本枠の矩形
        inner_x = (p.canvas_width_mm - p.inner_frame_width_mm) / 2.0 + p.inner_frame_offset_x_mm
        inner_y = (p.canvas_height_mm - p.inner_frame_height_mm) / 2.0 + p.inner_frame_offset_y_mm
        gap_v = work.panel_gap.vertical_mm
        gap_h = work.panel_gap.horizontal_mm
        rows, cols = self.rows, self.cols
        # 枠内有効幅/高さから gap を引いて分割
        total_gap_w = gap_h * (cols - 1)
        total_gap_h = gap_v * (rows - 1)
        cell_w = (p.inner_frame_width_mm - total_gap_w) / cols
        cell_h = (p.inner_frame_height_mm - total_gap_h) / rows

        try:
            if self.clear_existing:
                # 既存コマとファイルを削除
                for entry in list(page.panels):
                    panel_io.remove_panel_files(work_dir, page.id, entry.panel_stem)
                page.panels.clear()
                page.active_panel_index = -1

            # 行は上から下へ (漫画は右→左の読み順だが、ここでは配列順のみ)
            for r in range(rows):
                for c in range(cols):
                    stem = panel_io.allocate_new_panel_stem(work_dir, page.id)
                    entry = page.panels.add()
                    entry.panel_stem = stem
                    entry.id = stem.split("_", 1)[1]
                    entry.title = f"{r + 1}-{c + 1}"
                    entry.shape_type = "rect"
                    entry.rect_x_mm = inner_x + c * (cell_w + gap_h)
                    entry.rect_y_mm = (
                        inner_y + (rows - 1 - r) * (cell_h + gap_v)
                    )
                    entry.rect_width_mm = cell_w
                    entry.rect_height_mm = cell_h
                    entry.z_order = r * cols + c
                    panel_io.save_panel_meta(work_dir, page.id, entry)
            page.active_panel_index = 0 if len(page.panels) else -1
            _save_page_and_pages(work, page, work_dir)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_split_template failed")
            self.report({"ERROR"}, f"分割失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"分割: {rows}×{cols} = {rows * cols} コマ")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_panel_add,
    BNAME_OT_panel_remove,
    BNAME_OT_panel_duplicate,
    BNAME_OT_panel_move_to_page,
    BNAME_OT_panel_z_order,
    BNAME_OT_panel_split_template,
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
