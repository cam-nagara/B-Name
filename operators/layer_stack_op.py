"""統合レイヤーリストの選択・追加・並び替え・削除 Operator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator
from bpy_extras.io_utils import ImportHelper

from ..utils import layer_stack as layer_stack_utils
from ..utils.layer_hierarchy import (
    PAGE_KIND,
    COMA_KIND,
    page_stack_key,
    coma_stack_key,
    split_child_key,
)


_ADD_KIND_ITEMS = (
    ("page", "ページ", ""),
    ("coma", "コマ", ""),
    ("gp", "グリースペンシル", ""),
    ("image", "画像 (配置)", ""),
    ("raster", "ラスター (描画)", ""),
    ("balloon", "フキダシ", ""),
    ("text", "テキスト", ""),
    ("effect", "効果線", ""),
    ("gp_folder", "フォルダ", ""),
)

_ADD_KIND_ICONS = {
    "page": "FILE_BLANK",
    "coma": "MOD_WIREFRAME",
    "gp": "OUTLINER_OB_GREASEPENCIL",
    "image": "IMAGE_DATA",
    "raster": "BRUSH_DATA",
    "balloon_group": "FILE_FOLDER",
    "balloon": "MOD_FLUID",
    "text": "FONT_DATA",
    "effect": "STROKE",
    "gp_folder": "FILE_FOLDER",
}


def _active_stack_item(context):
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return None
    idx = int(getattr(context.scene, "bname_active_layer_stack_index", -1))
    if 0 <= idx < len(stack):
        return stack[idx]
    return None


def _active_stack_uid(context) -> str:
    item = _active_stack_item(context)
    return layer_stack_utils.stack_item_uid(item) if item is not None else ""


def _page_key_for_item(item) -> str:
    if item is None:
        return ""
    if item.kind == PAGE_KIND:
        return item.key
    if item.kind in {COMA_KIND, "balloon_group", "balloon", "text"}:
        page_key, _child = split_child_key(item.key)
        return page_key
    parent_key = str(getattr(item, "parent_key", "") or "")
    if parent_key and ":" not in parent_key:
        return parent_key
    if parent_key:
        page_key, _child = split_child_key(parent_key)
        return page_key
    return ""


def _placement_anchor_uid(context, kind: str) -> str:
    item = _active_stack_item(context)
    if item is None:
        return ""
    if kind == PAGE_KIND:
        page_key = _page_key_for_item(item)
        return layer_stack_utils.target_uid(PAGE_KIND, page_key) if page_key else ""
    if kind == COMA_KIND:
        if item.kind == COMA_KIND:
            return layer_stack_utils.stack_item_uid(item)
        parent_key = str(getattr(item, "parent_key", "") or "")
        if parent_key and ":" in parent_key:
            return layer_stack_utils.target_uid(COMA_KIND, parent_key)
        return ""
    return layer_stack_utils.stack_item_uid(item)


def _find_page(context, page_key: str):
    from ..core.work import get_work

    work = get_work(context)
    if work is None:
        return None, -1
    for i, page in enumerate(work.pages):
        if page_stack_key(page) == page_key:
            return page, i
    return None, -1


def _find_panel(context, coma_key: str):
    page_key, stem = split_child_key(coma_key)
    page, page_idx = _find_page(context, page_key)
    if page is None:
        return None, None, page_idx, -1
    for i, panel in enumerate(page.comas):
        if coma_stack_key(page, panel) == coma_key or getattr(panel, "coma_id", "") == stem:
            return page, panel, page_idx, i
    return page, None, page_idx, -1


def _active_or_anchor_page(context, anchor_uid: str):
    from ..core.work import get_active_page, get_work

    stack = getattr(context.scene, "bname_layer_stack", None)
    anchor = None
    if stack is not None and anchor_uid:
        for item in stack:
            if layer_stack_utils.stack_item_uid(item) == anchor_uid:
                anchor = item
                break
    page_key = _page_key_for_item(anchor)
    if page_key:
        page, page_idx = _find_page(context, page_key)
        if page is not None:
            work = get_work(context)
            if work is not None and 0 <= page_idx < len(work.pages):
                work.active_page_index = page_idx
            return work, page
    work = get_work(context)
    return work, get_active_page(context)


def _coma_bounds(panel) -> tuple[float, float, float, float] | None:
    from ..utils.layer_hierarchy import coma_polygon

    points = coma_polygon(panel)
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _default_rect_for_parent(context, work, page, parent_key: str, width: float, height: float):
    if parent_key and ":" in parent_key:
        _page, panel, _page_idx, _coma_idx = _find_panel(context, parent_key)
        bounds = _coma_bounds(panel) if panel is not None else None
        if bounds is not None:
            left, bottom, right, top = bounds
            return (
                left + max(0.0, (right - left - width) * 0.5),
                bottom + max(0.0, (top - bottom - height) * 0.5),
            )
    paper = getattr(work, "paper", None)
    canvas_w = float(getattr(paper, "canvas_width_mm", 210.0))
    canvas_h = float(getattr(paper, "canvas_height_mm", 297.0))
    return max(0.0, canvas_w - width - 5.0), max(0.0, canvas_h - height - 5.0)


def _parent_key_for_new_item(context, anchor_uid: str, kind: str) -> str:
    """新規レイヤー追加時の親キーを、レイヤーリスト上で選択中の行から推定する.

    CSP / Photoshop のレイヤーパネルと同じ感覚にする:
    - Page / Coma 選択中: そのページ/コマの中に追加 (返り値 = 行の key)
    - gp_folder 選択中: フォルダの中に追加 (返り値 = フォルダの key)
    - 他レイヤー選択中: その兄弟として追加 (返り値 = 行の parent_key)
    """
    stack = getattr(context.scene, "bname_layer_stack", None)
    if stack is None or not anchor_uid:
        return ""
    from ..core.work import get_work
    from ..utils import gp_layer_parenting as gp_parent

    work = get_work(context)
    logical_child_kinds = {"gp", "effect", "raster", "balloon", "text"}
    for item in stack:
        if layer_stack_utils.stack_item_uid(item) != anchor_uid:
            continue
        # Page / Coma 行を選択中: そのコンテナの中へ
        if kind in logical_child_kinds and item.kind in {PAGE_KIND, COMA_KIND}:
            return item.key
        if kind in logical_child_kinds and gp_parent.parent_key_exists(
            work, str(getattr(item, "parent_key", "") or "")
        ):
            return str(getattr(item, "parent_key", "") or "")
        # gp_folder 行を選択中: フォルダの中へ
        if kind in {"gp", "gp_folder"} and item.kind == "gp_folder":
            return str(getattr(item, "key", "") or "")
        # 同種レイヤー選択中: 兄弟として
        if kind in {"gp", "gp_folder"} and item.kind == "gp":
            return str(getattr(item, "parent_key", "") or "")
        if kind in {"effect", "raster", "balloon", "text"} and item.kind in {"effect", "raster", "balloon", "text"}:
            return str(getattr(item, "parent_key", "") or "")
        if kind == COMA_KIND and item.kind == COMA_KIND:
            return str(getattr(item, "parent_key", "") or "")
        return ""
    return ""


def _place_new_item(context, new_uid: str, anchor_uid: str) -> bool:
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None or not new_uid:
        return False
    new_idx = next(
        (i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == new_uid),
        -1,
    )
    if new_idx < 0:
        return False
    anchor_idx = next(
        (i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == anchor_uid),
        -1,
    )
    if anchor_idx >= 0 and anchor_idx != new_idx:
        target_idx = anchor_idx if new_idx > anchor_idx else max(0, anchor_idx - 1)
        if target_idx != new_idx:
            stack.move(new_idx, target_idx)
    layer_stack_utils.apply_stack_order(context)
    layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    for i, item in enumerate(context.scene.bname_layer_stack):
        if layer_stack_utils.stack_item_uid(item) == new_uid:
            layer_stack_utils.select_stack_index(context, i)
            layer_stack_utils.remember_layer_stack_signature(context)
            return True
    return False


def _unique_name(existing: set[str], base: str) -> str:
    if base not in existing:
        return base
    i = 1
    while True:
        candidate = f"{base}.{i:03d}"
        if candidate not in existing:
            return candidate
        i += 1


def _copy_image_entry(src, dst) -> None:
    for attr in (
        "title", "filepath", "x_mm", "y_mm", "width_mm", "height_mm",
        "rotation_deg", "flip_x", "flip_y", "visible", "locked", "opacity",
        "blend_mode", "brightness", "contrast", "binarize_enabled",
        "binarize_threshold", "tint_color",
    ):
        try:
            setattr(dst, attr, getattr(src, attr))
        except Exception:  # noqa: BLE001
            pass


def _active_row_in_visible_subtree(stack, active_index: int, parent_index: int) -> bool:
    if active_index <= parent_index or not (0 <= active_index < len(stack)):
        return False
    parent_depth = int(getattr(stack[parent_index], "depth", 0))
    for i in range(parent_index + 1, len(stack)):
        depth = int(getattr(stack[i], "depth", 0))
        if depth <= parent_depth:
            return False
        if i == active_index:
            return True
    return False


def _select_stack_uid(context, uid: str) -> bool:
    stack = getattr(context.scene, "bname_layer_stack", None)
    if stack is None or not uid:
        return False
    for i, item in enumerate(stack):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return layer_stack_utils.select_stack_index(context, i)
    return False


class BNAME_OT_layer_stack_select(Operator):
    bl_idname = "bname.layer_stack_select"
    bl_label = "レイヤーを選択"
    bl_options = {"REGISTER"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bname_layer_stack", None) is not None

    def execute(self, context):
        if not layer_stack_utils.select_stack_index(context, self.index):
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_layer_stack_move(Operator):
    bl_idname = "bname.layer_stack_move"
    bl_label = "レイヤー順を変更"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(  # type: ignore[valid-type]
        items=(
            ("FRONT", "最前面", ""),
            ("UP", "前面へ", ""),
            ("DOWN", "背面へ", ""),
            ("BACK", "最背面", ""),
        ),
        default="UP",
    )

    @classmethod
    def poll(cls, context):
        stack = getattr(context.scene, "bname_layer_stack", None)
        return stack is not None and len(stack) > 0

    def execute(self, context):
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        stack = context.scene.bname_layer_stack
        idx = int(getattr(context.scene, "bname_active_layer_stack_index", -1))
        if not (0 <= idx < len(stack)):
            return {"CANCELLED"}
        if not layer_stack_utils.move_stack_item(context, idx, direction=self.direction):
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_layer_stack_drag(Operator):
    bl_idname = "bname.layer_stack_drag"
    bl_label = "レイヤーをドラッグ移動"
    bl_description = "クリックで選択、ドラッグで並び替え"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]

    _moved_uid: str

    DRAG_THRESHOLD_PX: float = 6.0  # この距離以上動いたらドラッグ扱い

    @classmethod
    def poll(cls, context):
        stack = getattr(context.scene, "bname_layer_stack", None)
        return stack is not None and len(stack) > 0

    def invoke(self, context, event):
        """ボタン invoke は RELEASE 時に呼ばれる。

        - press 位置と release 位置の差が閾値未満ならクリック扱い → 選択だけ
        - 閾値以上ならドラッグ扱い → Y デルタから新しい位置を計算して移動
        """
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        if stack is None or not (0 <= self.index < len(stack)):
            return {"CANCELLED"}

        try:
            press_x = float(getattr(event, "mouse_prev_press_x", getattr(event, "mouse_x", 0.0)))
            press_y = float(getattr(event, "mouse_prev_press_y", getattr(event, "mouse_y", 0.0)))
            cur_x = float(getattr(event, "mouse_x", press_x))
            cur_y = float(getattr(event, "mouse_y", press_y))
        except Exception:  # noqa: BLE001
            press_x = press_y = cur_x = cur_y = 0.0

        dx = cur_x - press_x
        dy = cur_y - press_y

        if abs(dx) < self.DRAG_THRESHOLD_PX and abs(dy) < self.DRAG_THRESHOLD_PX:
            # クリック扱い: 選択だけ
            layer_stack_utils.select_stack_index(context, self.index)
            return {"FINISHED"}

        # ドラッグ扱い: Y デルタから移動先 index を決める
        row_height = self._estimate_row_height(context)
        if row_height <= 0.0:
            layer_stack_utils.select_stack_index(context, self.index)
            return {"CANCELLED"}
        # Blender UI の Y は上が大きい。ユーザーが下方向 (画面下) にドラッグした
        # = dy が負 → リスト上で「後ろ (下)」へ移動 → offset 正。
        offset = int(round(-dy / row_height))
        target_index = max(0, min(len(stack) - 1, self.index + offset))

        moved_uid = layer_stack_utils.stack_item_uid(stack[self.index])
        if target_index == self.index:
            layer_stack_utils.select_stack_index(context, self.index)
            return {"FINISHED"}

        stack.move(self.index, target_index)
        # 親キー更新 (CSP/PS 風: 直前行のコンテナを親に採用)
        if not layer_stack_utils.apply_stack_drop_hint(
            context, moved_uid, nesting_delta=0
        ):
            layer_stack_utils.apply_stack_order(context)
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)

        # 移動後の位置を選択
        for i, item in enumerate(context.scene.bname_layer_stack):
            if layer_stack_utils.stack_item_uid(item) == moved_uid:
                layer_stack_utils.select_stack_index(context, i)
                break

        layer_stack_utils.remember_layer_stack_signature(context)
        self._tag_ui_redraw(context)
        return {"FINISHED"}

    def execute(self, context):
        # 直接 EXEC されたとき (履歴から再実行など) は単純な選択にする.
        layer_stack_utils.select_stack_index(context, self.index)
        return {"FINISHED"}

    def _estimate_row_height(self, context) -> float:
        scale = 1.0
        prefs = getattr(context, "preferences", None)
        view = getattr(prefs, "view", None) if prefs is not None else None
        try:
            scale = float(getattr(view, "ui_scale", 1.0))
        except Exception:  # noqa: BLE001
            scale = 1.0
        return max(16.0, 22.0 * max(0.5, scale))

    def _tag_ui_redraw(self, context) -> None:
        area = getattr(context, "area", None)
        if area is not None:
            try:
                area.tag_redraw()
            except Exception:  # noqa: BLE001
                pass
        layer_stack_utils.tag_view3d_redraw(context)


class BNAME_MT_layer_stack_add(Menu):
    bl_idname = "BNAME_MT_layer_stack_add"
    bl_label = "レイヤーを追加"

    def draw(self, _context):
        layout = self.layout
        for kind, label, _desc in _ADD_KIND_ITEMS:
            if kind == "raster":
                layout.menu(
                    "BNAME_MT_layer_stack_add_raster",
                    text=label,
                    icon=_ADD_KIND_ICONS.get(kind, "ADD"),
                )
                continue
            op = layout.operator(
                "bname.layer_stack_add",
                text=label,
                icon=_ADD_KIND_ICONS.get(kind, "ADD"),
            )
            op.kind = kind


class BNAME_MT_layer_stack_add_raster(Menu):
    bl_idname = "BNAME_MT_layer_stack_add_raster"
    bl_label = "ラスターを追加"

    def draw(self, _context):
        layout = self.layout
        op = layout.operator(
            "bname.layer_stack_add",
            text="300dpi / グレー 8bit",
            icon="BRUSH_DATA",
        )
        op.kind = "raster"
        op.dpi = 300
        op.bit_depth = "gray8"
        op = layout.operator(
            "bname.layer_stack_add",
            text="150dpi / グレー 8bit",
            icon="BRUSH_DATA",
        )
        op.kind = "raster"
        op.dpi = 150
        op.bit_depth = "gray8"


class BNAME_OT_layer_stack_add(Operator, ImportHelper):
    bl_idname = "bname.layer_stack_add"
    bl_label = "レイヤーを追加"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(items=_ADD_KIND_ITEMS, default="gp")  # type: ignore[valid-type]
    anchor_uid: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    dpi: IntProperty(name="DPI", default=300, min=30, soft_max=1200)  # type: ignore[valid-type]
    bit_depth: EnumProperty(  # type: ignore[valid-type]
        name="階調",
        items=(("gray8", "グレー 8bit", ""), ("gray1", "1bit", "")),
        default="gray8",
    )
    filter_glob: StringProperty(  # type: ignore[valid-type]
        default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.psd;*.bmp",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bname_layer_stack", None) is not None

    def invoke(self, context, _event):
        self.anchor_uid = _placement_anchor_uid(context, self.kind)
        if self.kind == "image":
            self.filepath = ""
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}
        return self.execute(context)

    def execute(self, context):
        anchor_uid = self.anchor_uid or _placement_anchor_uid(context, self.kind)
        try:
            new_uid = self._add_by_kind(context, anchor_uid)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"レイヤー追加失敗: {exc}")
            return {"CANCELLED"}
        if not new_uid:
            return {"CANCELLED"}
        _place_new_item(context, new_uid, anchor_uid)
        return {"FINISHED"}

    def _add_by_kind(self, context, anchor_uid: str) -> str:
        if self.kind == "page":
            return self._add_page(context)
        if self.kind == "coma":
            return self._add_panel(context, anchor_uid)
        if self.kind == "gp":
            return self._add_gp_layer(context, anchor_uid)
        if self.kind == "image":
            return self._add_image(context)
        if self.kind == "raster":
            return self._add_raster(context, anchor_uid)
        if self.kind == "balloon":
            return self._add_balloon(context, anchor_uid)
        if self.kind == "text":
            return self._add_text(context, anchor_uid)
        if self.kind == "effect":
            return self._add_effect(context, anchor_uid)
        if self.kind == "gp_folder":
            return self._add_gp_folder(context, anchor_uid)
        return ""

    def _add_page(self, context) -> str:
        from ..core.work import get_work
        from ..io import page_io, work_io
        from ..utils import gpencil as gp_utils
        from ..utils import page_grid, page_range
        from .coma_op import create_basic_frame_coma

        work = get_work(context)
        if work is None or not work.loaded or not work.work_dir:
            self.report({"ERROR"}, "作品が開かれていません")
            return ""
        work_dir = Path(work.work_dir)
        entry = page_io.register_new_page(work)
        page_io.ensure_page_dir(work_dir, entry.id)
        create_basic_frame_coma(work, entry, work_dir)
        gp_utils.ensure_page_gpencil(context.scene, entry.id)
        page_grid.apply_page_collection_transforms(context, work)
        page_io.save_pages_json(work_dir, work)
        page_range.sync_end_number_to_page_count(work)
        work_io.save_work_json(work_dir, work)
        context.scene.bname_active_layer_kind = PAGE_KIND
        layer_stack_utils.sync_layer_stack_after_data_change(context, align_page_order=True)
        return layer_stack_utils.target_uid(PAGE_KIND, page_stack_key(entry))

    def _add_panel(self, context, anchor_uid: str) -> str:
        from ..io import page_io
        from .coma_op import create_rect_coma

        work, page = _active_or_anchor_page(context, anchor_uid)
        if work is None or page is None or not work.work_dir:
            self.report({"ERROR"}, "ページが選択されていません")
            return ""
        p = work.paper
        x_mm = (p.canvas_width_mm - 60.0) / 2.0
        y_mm = (p.canvas_height_mm - 40.0) / 2.0
        entry = create_rect_coma(work, page, Path(work.work_dir), x_mm, y_mm, 60.0, 40.0)
        page_io.save_pages_json(Path(work.work_dir), work)
        context.scene.bname_active_layer_kind = COMA_KIND
        layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        return layer_stack_utils.target_uid(COMA_KIND, coma_stack_key(page, entry))

    def _add_gp_layer(self, context, anchor_uid: str) -> str:
        from ..utils import gpencil as gp_utils
        from ..utils import gp_layer_parenting as gp_parent
        from ..core.work import get_work

        obj = gp_utils.ensure_master_gpencil(context.scene)
        gp_data = obj.data
        parent_key = _parent_key_for_new_item(context, anchor_uid, "gp")
        groups = getattr(gp_data, "layer_groups", None)
        parent = layer_stack_utils._find_gp_group_by_key(groups, parent_key)
        logical_parent = gp_parent.parent_key_exists(get_work(context), parent_key)
        existing = {layer.name for layer in gp_data.layers}
        layer = gp_data.layers.new(_unique_name(existing, "レイヤー"))
        if parent is not None:
            gp_utils.move_layer_to_group(gp_data, layer, parent)
        elif logical_parent:
            gp_parent.set_parent_key(layer, parent_key)
        gp_data.layers.active = layer
        gp_utils.ensure_active_frame(layer)
        gp_utils.ensure_layer_material(obj, layer, activate=True, assign_existing=True)
        context.scene.bname_active_layer_kind = "gp"
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return layer_stack_utils.target_uid("gp", layer_stack_utils._node_stack_key(layer))

    def _add_gp_folder(self, context, anchor_uid: str) -> str:
        from ..utils import gpencil as gp_utils

        obj = gp_utils.ensure_master_gpencil(context.scene)
        gp_data = obj.data
        groups = getattr(gp_data, "layer_groups", None)
        if groups is None:
            self.report({"ERROR"}, "この Blender ではフォルダを作成できません")
            return ""
        group = groups.new(gp_utils.unique_layer_group_name(gp_data))
        parent_key = _parent_key_for_new_item(context, anchor_uid, "gp_folder")
        parent = layer_stack_utils._find_gp_group_by_key(groups, parent_key)
        if parent is not None:
            gp_utils.move_group_to_group(gp_data, group, parent)
        group.is_expanded = True
        context.scene.bname_active_layer_kind = "gp_folder"
        context.scene.bname_active_gp_folder_key = layer_stack_utils._node_stack_key(group)
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return layer_stack_utils.target_uid("gp_folder", layer_stack_utils._node_stack_key(group))

    def _add_image(self, context) -> str:
        path = Path(self.filepath)
        if not path.is_file():
            self.report({"ERROR"}, f"ファイルが見つかりません: {path}")
            return ""
        coll = getattr(context.scene, "bname_image_layers", None)
        if coll is None:
            self.report({"ERROR"}, "画像レイヤーが未初期化です")
            return ""
        used = {entry.id for entry in coll}
        i = 1
        while f"image_{i:04d}" in used:
            i += 1
        entry = coll.add()
        entry.id = f"image_{i:04d}"
        entry.title = path.stem
        entry.filepath = str(path)
        try:
            img = bpy.data.images.load(str(path), check_existing=True)
            entry.width_mm = max(1.0, img.size[0] / 6.0)
            entry.height_mm = max(1.0, img.size[1] / 6.0)
        except Exception:  # noqa: BLE001
            pass
        context.scene.bname_active_image_layer_index = len(coll) - 1
        context.scene.bname_active_layer_kind = "image"
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return layer_stack_utils.target_uid("image", entry.id)

    def _add_raster(self, context, anchor_uid: str) -> str:
        before = {
            getattr(entry, "id", "")
            for entry in (getattr(context.scene, "bname_raster_layers", None) or [])
        }
        result = bpy.ops.bname.raster_layer_add(
            "EXEC_DEFAULT",
            dpi=int(getattr(self, "dpi", 300)),
            bit_depth=str(getattr(self, "bit_depth", "gray8") or "gray8"),
        )
        if "FINISHED" not in result:
            return ""
        coll = getattr(context.scene, "bname_raster_layers", None)
        if coll is None:
            return ""
        for entry in coll:
            if getattr(entry, "id", "") not in before:
                parent_key = _parent_key_for_new_item(context, anchor_uid, "raster")
                if parent_key:
                    entry.scope = "page"
                    entry.parent_kind = "coma" if ":" in parent_key else "page"
                    entry.parent_key = parent_key
                return layer_stack_utils.target_uid("raster", entry.id)
        idx = int(getattr(context.scene, "bname_active_raster_layer_index", -1))
        if 0 <= idx < len(coll):
            return layer_stack_utils.target_uid("raster", coll[idx].id)
        return ""

    def _add_balloon(self, context, anchor_uid: str) -> str:
        from .balloon_op import _allocate_balloon_id, _creation_violates_layer_scope

        work, page = _active_or_anchor_page(context, anchor_uid)
        if work is None or page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return ""
        width, height = 40.0, 20.0
        parent_key = _parent_key_for_new_item(context, anchor_uid, "balloon")
        x_mm, y_mm = _default_rect_for_parent(context, work, page, parent_key, width, height)
        if _creation_violates_layer_scope(context, page, x_mm, y_mm, width, height):
            self.report({"ERROR"}, "このモードではその位置にフキダシを作成できません")
            return ""
        entry = page.balloons.add()
        entry.id = _allocate_balloon_id(page)
        entry.shape = "rect"
        entry.x_mm = x_mm
        entry.y_mm = y_mm
        entry.width_mm = width
        entry.height_mm = height
        entry.rounded_corner_enabled = True
        entry.parent_kind = "coma" if ":" in parent_key else "page"
        entry.parent_key = parent_key or page_stack_key(page)
        page.active_balloon_index = len(page.balloons) - 1
        context.scene.bname_active_layer_kind = "balloon"
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return layer_stack_utils.target_uid("balloon", f"{page_stack_key(page)}:{entry.id}")

    def _add_text(self, context, anchor_uid: str) -> str:
        from .text_op import _create_text_entry, _creation_blocked

        work, page = _active_or_anchor_page(context, anchor_uid)
        if work is None or page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return ""
        width, height = 30.0, 15.0
        parent_key = _parent_key_for_new_item(context, anchor_uid, "text")
        x_mm, y_mm = _default_rect_for_parent(context, work, page, parent_key, width, height)
        if _creation_blocked(context, page, x_mm, y_mm, width, height):
            self.report({"ERROR"}, "このモードではその位置にテキストを作成できません")
            return ""
        entry, _missing = _create_text_entry(
            context,
            page,
            body="テキスト",
            speaker_type="normal",
            x_mm=x_mm,
            y_mm=y_mm,
            width_mm=width,
            height_mm=height,
        )
        if parent_key:
            entry.parent_kind = "coma" if ":" in parent_key else "page"
            entry.parent_key = parent_key
        return layer_stack_utils.target_uid("text", f"{page_stack_key(page)}:{entry.id}")

    def _add_effect(self, context, anchor_uid: str) -> str:
        from .effect_line_op import _create_effect_layer

        parent_key = _parent_key_for_new_item(context, anchor_uid, "effect")
        _obj, layer = _create_effect_layer(context, parent_key=parent_key)
        return layer_stack_utils.target_uid("effect", layer_stack_utils._node_stack_key(layer))


class BNAME_OT_layer_stack_duplicate(Operator):
    bl_idname = "bname.layer_stack_duplicate"
    bl_label = "レイヤーを複製"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        stack = getattr(context.scene, "bname_layer_stack", None)
        idx = int(getattr(context.scene, "bname_active_layer_stack_index", -1))
        return stack is not None and 0 <= idx < len(stack)

    def execute(self, context):
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        idx = int(getattr(context.scene, "bname_active_layer_stack_index", -1))
        if stack is None or not (0 <= idx < len(stack)):
            return {"CANCELLED"}
        anchor_uid = layer_stack_utils.stack_item_uid(stack[idx])
        before = {layer_stack_utils.stack_item_uid(item) for item in stack}
        if not self._duplicate_item(context, stack[idx]):
            return {"CANCELLED"}
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        new_uid = self._new_uid_after_duplicate(context, before)
        if new_uid:
            _place_new_item(context, new_uid, anchor_uid)
        return {"FINISHED"}

    def _new_uid_after_duplicate(self, context, before: set[str]) -> str:
        stack = getattr(context.scene, "bname_layer_stack", None)
        if stack is None:
            return ""
        for item in stack:
            uid = layer_stack_utils.stack_item_uid(item)
            if uid not in before:
                return uid
        return _active_stack_uid(context)

    def _duplicate_item(self, context, item) -> bool:
        if item.kind in {PAGE_KIND, COMA_KIND}:
            if not layer_stack_utils.select_stack_index(
                context,
                int(getattr(context.scene, "bname_active_layer_stack_index", -1)),
            ):
                return False
            op_name = "page_duplicate" if item.kind == PAGE_KIND else "coma_duplicate"
            return "FINISHED" in getattr(bpy.ops.bname, op_name)("EXEC_DEFAULT")
        if item.kind in {"gp", "effect"}:
            return self._duplicate_gp_layer(context, item)
        if item.kind == "gp_folder":
            return self._duplicate_gp_folder(context, item)
        if item.kind == "image":
            return self._duplicate_image(context, item)
        if item.kind == "balloon":
            return self._duplicate_balloon(context, item)
        if item.kind == "text":
            return self._duplicate_text(context, item)
        return False

    def _duplicate_gp_layer(self, context, item) -> bool:
        if not layer_stack_utils.select_stack_index(
            context,
            int(getattr(context.scene, "bname_active_layer_stack_index", -1)),
        ):
            return False
        try:
            parent_key = str(getattr(item, "parent_key", "") or "")
            source_obj = None
            source_layer = None
            if item.kind == "effect":
                resolved = layer_stack_utils.resolve_stack_item(context, item)
                source_obj = resolved.get("object") if resolved is not None else None
                source_layer = resolved.get("target") if resolved is not None else None
            result = bpy.ops.grease_pencil.layer_duplicate("EXEC_DEFAULT", empty_keyframes=False)
            if "FINISHED" not in result:
                return False
            if item.kind == "gp" and parent_key:
                from ..core.work import get_work
                from ..utils import gp_layer_parenting as gp_parent
                from ..utils import gpencil as gp_utils

                layer = getattr(getattr(gp_utils.get_master_gpencil(), "data", None), "layers", None)
                active = getattr(layer, "active", None) if layer is not None else None
                if active is not None and gp_parent.parent_key_exists(get_work(context), parent_key):
                    gp_parent.set_parent_key(active, parent_key)
            elif item.kind == "effect":
                from . import effect_line_op

                active = getattr(getattr(source_obj, "data", None), "layers", None)
                active = getattr(active, "active", None) if active is not None else None
                effect_line_op.copy_layer_effect_meta(source_obj, source_layer, active)
                if active is not None and hasattr(context.scene, "bname_active_effect_layer_name"):
                    context.scene.bname_active_effect_layer_name = layer_stack_utils._node_stack_key(active)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _duplicate_gp_folder(self, context, item) -> bool:
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        target = resolved.get("target") if resolved is not None else None
        obj = resolved.get("object") if resolved is not None else None
        if target is None or obj is None:
            return False
        groups = getattr(obj.data, "layer_groups", None)
        if groups is None:
            return False
        from ..utils import gpencil as gp_utils

        group = groups.new(gp_utils.unique_layer_group_name(obj.data, target.name))
        parent = getattr(target, "parent_group", None)
        if parent is not None:
            gp_utils.move_group_to_group(obj.data, group, parent)
        context.scene.bname_active_layer_kind = "gp_folder"
        context.scene.bname_active_gp_folder_key = layer_stack_utils._node_stack_key(group)
        return True

    def _duplicate_image(self, context, item) -> bool:
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        src = resolved.get("target") if resolved is not None else None
        coll = getattr(context.scene, "bname_image_layers", None)
        if src is None or coll is None:
            return False
        used = {entry.id for entry in coll}
        i = 1
        while f"image_{i:04d}" in used:
            i += 1
        dst = coll.add()
        dst.id = f"image_{i:04d}"
        _copy_image_entry(src, dst)
        dst.title = _unique_name({entry.title for entry in coll if entry is not dst}, f"{src.title} 複製")
        context.scene.bname_active_image_layer_index = len(coll) - 1
        context.scene.bname_active_layer_kind = "image"
        return True

    def _duplicate_balloon(self, context, item) -> bool:
        from ..io import schema
        from .balloon_op import _allocate_balloon_id

        resolved = layer_stack_utils.resolve_stack_item(context, item)
        src = resolved.get("target") if resolved is not None else None
        page = resolved.get("page") if resolved is not None else None
        if src is None or page is None:
            return False
        dst = page.balloons.add()
        schema.balloon_entry_from_dict(dst, schema.balloon_entry_to_dict(src))
        dst.id = _allocate_balloon_id(page)
        dst.x_mm += 5.0
        dst.y_mm -= 5.0
        page.active_balloon_index = len(page.balloons) - 1
        context.scene.bname_active_layer_kind = "balloon"
        return True

    def _duplicate_text(self, context, item) -> bool:
        from ..io import schema
        from .text_op import _allocate_text_id

        resolved = layer_stack_utils.resolve_stack_item(context, item)
        src = resolved.get("target") if resolved is not None else None
        page = resolved.get("page") if resolved is not None else None
        if src is None or page is None:
            return False
        dst = page.texts.add()
        schema.text_entry_from_dict(dst, schema.text_entry_to_dict(src))
        dst.id = _allocate_text_id(page)
        dst.x_mm += 5.0
        dst.y_mm -= 5.0
        page.active_text_index = len(page.texts) - 1
        context.scene.bname_active_layer_kind = "text"
        return True


class BNAME_OT_layer_stack_toggle_visibility(Operator):
    bl_idname = "bname.layer_stack_toggle_visibility"
    bl_label = "レイヤー表示を切替"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bname_layer_stack", None) is not None

    def execute(self, context):
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        stack = getattr(context.scene, "bname_layer_stack", None)
        if stack is None or not (0 <= self.index < len(stack)):
            return {"CANCELLED"}
        item = stack[self.index]
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        target = resolved.get("target") if resolved is not None else None
        if target is None:
            return {"CANCELLED"}
        if item.kind in {PAGE_KIND, COMA_KIND} and hasattr(target, "visible"):
            target.visible = not bool(target.visible)
        elif item.kind in {"image", "raster"} and hasattr(target, "visible"):
            target.visible = not bool(target.visible)
        elif item.kind in {"gp", "gp_folder", "effect"} and hasattr(target, "hide"):
            target.hide = not bool(target.hide)
        else:
            return {"CANCELLED"}
        layer_stack_utils.tag_view3d_redraw(context)
        return {"FINISHED"}


class BNAME_OT_layer_stack_toggle_expanded(Operator):
    bl_idname = "bname.layer_stack_toggle_expanded"
    bl_label = "レイヤー階層を開閉"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bname_layer_stack", None) is not None

    def execute(self, context):
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        stack = getattr(context.scene, "bname_layer_stack", None)
        if stack is None or not (0 <= self.index < len(stack)):
            return {"CANCELLED"}
        item = stack[self.index]
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        target = resolved.get("target") if resolved is not None else None
        if target is None:
            return {"CANCELLED"}
        parent_uid = layer_stack_utils.stack_item_uid(item)
        active_will_be_hidden = _active_row_in_visible_subtree(
            stack,
            int(getattr(context.scene, "bname_active_layer_stack_index", -1)),
            self.index,
        )
        if item.kind == PAGE_KIND and hasattr(target, "stack_expanded"):
            was_expanded = bool(target.stack_expanded)
            target.stack_expanded = not was_expanded
            active_will_be_hidden = active_will_be_hidden and was_expanded
        elif item.kind == "gp_folder" and hasattr(target, "is_expanded"):
            was_expanded = bool(target.is_expanded)
            target.is_expanded = not was_expanded
            active_will_be_hidden = active_will_be_hidden and was_expanded
        else:
            return {"CANCELLED"}
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        if active_will_be_hidden:
            _select_stack_uid(context, parent_uid)
        layer_stack_utils.remember_layer_stack_signature(context)
        layer_stack_utils.tag_view3d_redraw(context)
        return {"FINISHED"}


class BNAME_OT_layer_stack_delete(Operator):
    bl_idname = "bname.layer_stack_delete"
    bl_label = "レイヤーを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        stack = getattr(context.scene, "bname_layer_stack", None)
        idx = int(getattr(context.scene, "bname_active_layer_stack_index", -1))
        return stack is not None and 0 <= idx < len(stack)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        idx = int(getattr(context.scene, "bname_active_layer_stack_index", -1))
        if not layer_stack_utils.delete_stack_index(context, idx):
            return {"CANCELLED"}
        return {"FINISHED"}


class BNAME_OT_layer_stack_enter_coma(Operator):
    bl_idname = "bname.layer_stack_enter_coma"
    bl_label = "コマ編集へ"
    bl_options = {"REGISTER"}

    stack_index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bname_layer_stack", None) is not None

    def execute(self, context):
        if not layer_stack_utils.select_stack_index(context, self.stack_index):
            return {"CANCELLED"}
        item = layer_stack_utils.active_stack_item(context)
        if item is None or item.kind != "coma":
            return {"CANCELLED"}
        return bpy.ops.bname.enter_coma_mode("EXEC_DEFAULT")


class BNAME_OT_layer_stack_detail(Operator):
    bl_idname = "bname.layer_stack_detail"
    bl_label = "詳細設定"
    bl_options = {"REGISTER"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]
    uid: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    preserve_edge_selection: BoolProperty(default=False, options={"HIDDEN"})  # type: ignore[valid-type]
    offset_from_selection: BoolProperty(default=False, options={"HIDDEN"})  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bname_layer_stack", None) is not None

    def invoke(self, context, event):
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        if stack is None or not (0 <= self.index < len(stack)):
            self.report({"ERROR"}, "詳細設定を開くレイヤーが見つかりません")
            return {"CANCELLED"}
        edge_state = self._capture_edge_selection(context)
        self.uid = layer_stack_utils.stack_item_uid(stack[self.index])
        layer_stack_utils.select_stack_index(context, self.index)
        self._restore_edge_selection_if_needed(context, stack[self.index], edge_state)
        layer_stack_utils.tag_view3d_redraw(context)
        self._offset_cursor_for_selection_popup(context, event)
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, context):
        layer_stack_utils.tag_view3d_redraw(context)
        return {"FINISHED"}

    def check(self, context):
        layer_stack_utils.tag_view3d_redraw(context)
        return True

    def draw(self, context):
        layout = self.layout
        stack = getattr(context.scene, "bname_layer_stack", None)
        if stack is None:
            layout.label(text="レイヤー一覧が未初期化です", icon="ERROR")
            return
        item = self._resolve_item(stack)
        if item is None:
            layout.label(text="レイヤーが見つかりません", icon="ERROR")
            return
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        try:
            from ..panels import gpencil_panel

            if not gpencil_panel.draw_stack_item_detail(layout, context, item, resolved):
                layout.label(text="このレイヤーの詳細を表示できません", icon="ERROR")
        except Exception as exc:  # noqa: BLE001
            layout.label(text="詳細設定を描画できません", icon="ERROR")
            layout.label(text=str(exc)[:80])
        layer_stack_utils.tag_view3d_redraw(context)

    def _offset_cursor_for_selection_popup(self, context, event) -> None:
        if not bool(getattr(self, "offset_from_selection", False)):
            return
        window = getattr(context, "window", None)
        if window is None or event is None:
            return
        try:
            original_x = int(getattr(event, "mouse_x", 0))
            original_y = int(getattr(event, "mouse_y", 0))
            if original_x <= 0 and original_y <= 0:
                return
            width = int(getattr(window, "width", 0))
            height = int(getattr(window, "height", 0))
            offset_x = 360
            target_x = original_x + offset_x
            if width > 0:
                target_x = min(max(20, target_x), max(20, width - 20))
            target_y = original_y
            if height > 0:
                target_y = min(max(20, target_y), max(20, height - 20))
            if target_x == original_x and target_y == original_y:
                return
            window.cursor_warp(target_x, target_y)

            def _restore_cursor():
                try:
                    current_window = getattr(bpy.context, "window", None)
                    if current_window is not None:
                        current_window.cursor_warp(original_x, original_y)
                except Exception:  # noqa: BLE001
                    pass
                return None

            bpy.app.timers.register(_restore_cursor, first_interval=0.05)
        except Exception:  # noqa: BLE001
            pass

    def _resolve_item(self, stack):
        if self.uid:
            for item in stack:
                if layer_stack_utils.stack_item_uid(item) == self.uid:
                    return item
        if 0 <= self.index < len(stack):
            return stack[self.index]
        return None

    def _capture_edge_selection(self, context) -> tuple[str, int, int, int, int]:
        wm = getattr(context, "window_manager", None)
        if wm is None:
            return ("none", -1, -1, -1, -1)
        return (
            str(getattr(wm, "bname_edge_select_kind", "none") or "none"),
            int(getattr(wm, "bname_edge_select_page", -1)),
            int(getattr(wm, "bname_edge_select_coma", -1)),
            int(getattr(wm, "bname_edge_select_edge", -1)),
            int(getattr(wm, "bname_edge_select_vertex", -1)),
        )

    def _restore_edge_selection_if_needed(self, context, item, edge_state) -> None:
        if not bool(getattr(self, "preserve_edge_selection", False)):
            return
        if item.kind != COMA_KIND:
            return
        kind, page_index, coma_index, edge_index, vertex_index = edge_state
        if kind not in {"edge", "vertex", "border"}:
            return
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        if resolved is None:
            return
        if (
            int(resolved.get("page_index", -2)) != page_index
            or int(resolved.get("index", -2)) != coma_index
        ):
            return
        from ..utils import edge_selection

        edge_selection.set_selection(
            context,
            kind,
            page_index=page_index,
            coma_index=coma_index,
            edge_index=edge_index,
            vertex_index=vertex_index,
        )


_CLASSES = (
    BNAME_OT_layer_stack_select,
    BNAME_OT_layer_stack_move,
    BNAME_OT_layer_stack_drag,
    BNAME_MT_layer_stack_add,
    BNAME_MT_layer_stack_add_raster,
    BNAME_OT_layer_stack_add,
    BNAME_OT_layer_stack_duplicate,
    BNAME_OT_layer_stack_toggle_visibility,
    BNAME_OT_layer_stack_toggle_expanded,
    BNAME_OT_layer_stack_delete,
    BNAME_OT_layer_stack_enter_coma,
    BNAME_OT_layer_stack_detail,
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
