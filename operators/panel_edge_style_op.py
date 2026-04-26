"""枠線選択ツールの個別線スタイル編集."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.types import Operator

from ..core.work import get_work
from ..io import page_io, panel_io
from ..utils import log

_logger = log.get_logger(__name__)

_SELECTION_STYLE_SYNCING = False


def _find_or_add_edge_style(panel, edge_index: int):
    edge_index = int(edge_index)
    for style in panel.edge_styles:
        if int(style.edge_index) == edge_index:
            return style
    style = panel.edge_styles.add()
    style.edge_index = edge_index
    style.color = panel.border.color
    style.width_mm = panel.border.width_mm
    return style


def _remove_edge_style(panel, edge_index: int) -> bool:
    edge_index = int(edge_index)
    for i, style in enumerate(panel.edge_styles):
        if int(style.edge_index) == edge_index:
            panel.edge_styles.remove(i)
            return True
    return False


def _panel_edge_count(panel) -> int:
    if panel.shape_type == "rect":
        return 4
    return len(panel.vertices)


def _vertex_edge_indices(panel, vertex_index: int) -> tuple[int, int] | None:
    edge_count = _panel_edge_count(panel)
    if edge_count <= 0 or not (0 <= vertex_index < edge_count):
        return None
    return ((vertex_index - 1) % edge_count, vertex_index)


def _find_edge_style(panel, edge_index: int):
    for style in panel.edge_styles:
        if int(style.edge_index) == int(edge_index):
            return style
    return None


def _selected_style_target(context):
    wm = context.window_manager
    kind = getattr(wm, "bname_edge_select_kind", "none")
    if kind not in {"edge", "border", "vertex"}:
        return None
    work = get_work(context)
    if work is None or not work.loaded:
        return None
    page_index = int(getattr(wm, "bname_edge_select_page", -1))
    panel_index = int(getattr(wm, "bname_edge_select_panel", -1))
    if not (0 <= page_index < len(work.pages)):
        return None
    page = work.pages[page_index]
    if not (0 <= panel_index < len(page.panels)):
        return None
    return kind, work, page, page.panels[panel_index], wm


def _selected_style_values(context) -> tuple[tuple[float, float, float, float], float] | None:
    target = _selected_style_target(context)
    if target is None:
        return None
    kind, _work, _page, panel, wm = target
    if kind == "border":
        return tuple(float(v) for v in panel.border.color), float(panel.border.width_mm)
    if kind == "edge":
        edge_index = int(getattr(wm, "bname_edge_select_edge", -1))
        style = _find_edge_style(panel, edge_index)
        if style is not None:
            return tuple(float(v) for v in style.color), float(style.width_mm)
        return tuple(float(v) for v in panel.border.color), float(panel.border.width_mm)
    if kind == "vertex":
        vertex_index = int(getattr(wm, "bname_edge_select_vertex", -1))
        edge_pair = _vertex_edge_indices(panel, vertex_index)
        if edge_pair is None:
            return None
        style = _find_edge_style(panel, edge_pair[0])
        if style is not None:
            return tuple(float(v) for v in style.color), float(style.width_mm)
        return tuple(float(v) for v in panel.border.color), float(panel.border.width_mm)
    return None


def sync_selected_style_props(context) -> None:
    global _SELECTION_STYLE_SYNCING
    values = _selected_style_values(context)
    if values is None:
        return
    color, width = values
    wm = context.window_manager
    _SELECTION_STYLE_SYNCING = True
    try:
        wm.bname_edge_style_color = color
        wm.bname_edge_style_width_mm = width
    finally:
        _SELECTION_STYLE_SYNCING = False


def _tag_view3d_redraw(context) -> None:
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _apply_selected_style_values(context, color: tuple[float, float, float, float], width_mm: float) -> None:
    target = _selected_style_target(context)
    if target is None:
        return
    kind, work, page, panel, wm = target
    if kind == "border":
        panel.border.color = color
        panel.border.width_mm = float(width_mm)
    elif kind == "edge":
        edge_index = int(getattr(wm, "bname_edge_select_edge", -1))
        style = _find_or_add_edge_style(panel, edge_index)
        style.color = color
        style.width_mm = float(width_mm)
    elif kind == "vertex":
        vertex_index = int(getattr(wm, "bname_edge_select_vertex", -1))
        edge_pair = _vertex_edge_indices(panel, vertex_index)
        if edge_pair is None:
            return
        for edge_index in edge_pair:
            style = _find_or_add_edge_style(panel, edge_index)
            style.color = color
            style.width_mm = float(width_mm)
    else:
        return
    _save_panel_change(work, page)
    _tag_view3d_redraw(context)


def _on_selected_style_prop_changed(_self, context) -> None:
    if context is None or _SELECTION_STYLE_SYNCING:
        return
    wm = context.window_manager
    color = tuple(float(v) for v in getattr(wm, "bname_edge_style_color", (0.0, 0.0, 0.0, 1.0)))
    width_mm = float(getattr(wm, "bname_edge_style_width_mm", 0.5))
    _apply_selected_style_values(context, color, width_mm)


def _save_panel_change(work, page) -> None:
    if work is None or work.work_dir == "":
        return
    work_dir = Path(work.work_dir)
    try:
        for p in page.panels:
            panel_io.save_panel_meta(work_dir, page.id, p)
        page_io.save_page_json(work_dir, page)
    except Exception:  # noqa: BLE001
        _logger.exception("edge_style save failed")


class BNAME_OT_edge_style_create(Operator):
    """選択中の辺に edge_style override を新規作成する."""

    bl_idname = "bname.edge_style_create"
    bl_label = "この辺に個別設定を追加"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        wm = context.window_manager
        if wm.bname_edge_select_kind != "edge":
            return {"CANCELLED"}
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        pi = wm.bname_edge_select_page
        pn = wm.bname_edge_select_panel
        ei = wm.bname_edge_select_edge
        if not (0 <= pi < len(work.pages)):
            return {"CANCELLED"}
        page = work.pages[pi]
        if not (0 <= pn < len(page.panels)):
            return {"CANCELLED"}
        panel = page.panels[pn]
        _find_or_add_edge_style(panel, ei)
        _save_panel_change(work, page)
        sync_selected_style_props(context)
        _tag_view3d_redraw(context)
        return {"FINISHED"}


class BNAME_OT_edge_style_remove(Operator):
    """選択中の辺の edge_style override を削除する."""

    bl_idname = "bname.edge_style_remove"
    bl_label = "個別設定を削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        wm = context.window_manager
        if wm.bname_edge_select_kind != "edge":
            return {"CANCELLED"}
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        pi = wm.bname_edge_select_page
        pn = wm.bname_edge_select_panel
        ei = wm.bname_edge_select_edge
        if not (0 <= pi < len(work.pages)):
            return {"CANCELLED"}
        page = work.pages[pi]
        if not (0 <= pn < len(page.panels)):
            return {"CANCELLED"}
        panel = page.panels[pn]
        _remove_edge_style(panel, ei)
        _save_panel_change(work, page)
        sync_selected_style_props(context)
        _tag_view3d_redraw(context)
        return {"FINISHED"}


class BNAME_OT_edge_style_clear_all(Operator):
    """選択中の panel の全 edge_style override を一括削除する."""

    bl_idname = "bname.edge_style_clear_all"
    bl_label = "全ての個別設定を削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        wm = context.window_manager
        if wm.bname_edge_select_kind not in {"edge", "border"}:
            return {"CANCELLED"}
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        pi = wm.bname_edge_select_page
        pn = wm.bname_edge_select_panel
        if not (0 <= pi < len(work.pages)):
            return {"CANCELLED"}
        page = work.pages[pi]
        if not (0 <= pn < len(page.panels)):
            return {"CANCELLED"}
        panel = page.panels[pn]
        panel.edge_styles.clear()
        _save_panel_change(work, page)
        sync_selected_style_props(context)
        _tag_view3d_redraw(context)
        return {"FINISHED"}


class BNAME_OT_vertex_style_remove(Operator):
    """選択中の頂点に接続する2辺の edge_style override を削除する."""

    bl_idname = "bname.vertex_style_remove"
    bl_label = "頂点の個別設定を削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        wm = context.window_manager
        if wm.bname_edge_select_kind != "vertex":
            return {"CANCELLED"}
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        pi = wm.bname_edge_select_page
        pn = wm.bname_edge_select_panel
        vi = int(getattr(wm, "bname_edge_select_vertex", -1))
        if not (0 <= pi < len(work.pages)):
            return {"CANCELLED"}
        page = work.pages[pi]
        if not (0 <= pn < len(page.panels)):
            return {"CANCELLED"}
        panel = page.panels[pn]
        edge_pair = _vertex_edge_indices(panel, vi)
        if edge_pair is None:
            return {"CANCELLED"}
        for edge_index in edge_pair:
            _remove_edge_style(panel, edge_index)
        _save_panel_change(work, page)
        sync_selected_style_props(context)
        _tag_view3d_redraw(context)
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_edge_style_create,
    BNAME_OT_edge_style_remove,
    BNAME_OT_edge_style_clear_all,
    BNAME_OT_vertex_style_remove,
)


def register() -> None:
    from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, IntProperty

    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bname_edge_select_kind = EnumProperty(
        name="選択種別",
        items=[
            ("none", "未選択", ""),
            ("edge", "辺", ""),
            ("border", "枠線全体", ""),
            ("vertex", "頂点", ""),
        ],
        default="none",
    )
    bpy.types.WindowManager.bname_edge_select_page = IntProperty(default=-1)
    bpy.types.WindowManager.bname_edge_select_panel = IntProperty(default=-1)
    bpy.types.WindowManager.bname_edge_select_edge = IntProperty(default=-1)
    bpy.types.WindowManager.bname_edge_select_vertex = IntProperty(default=-1)
    bpy.types.WindowManager.bname_edge_style_color = FloatVectorProperty(
        name="線色",
        subtype="COLOR",
        size=4,
        default=(0.0, 0.0, 0.0, 1.0),
        min=0.0,
        max=1.0,
        update=_on_selected_style_prop_changed,
    )
    bpy.types.WindowManager.bname_edge_style_width_mm = FloatProperty(
        name="線幅 (mm)",
        default=0.5,
        min=0.0,
        soft_max=10.0,
        update=_on_selected_style_prop_changed,
    )


def unregister() -> None:
    for prop in (
        "bname_edge_select_kind",
        "bname_edge_select_page",
        "bname_edge_select_panel",
        "bname_edge_select_edge",
        "bname_edge_select_vertex",
        "bname_edge_style_color",
        "bname_edge_style_width_mm",
    ):
        try:
            delattr(bpy.types.WindowManager, prop)
        except AttributeError:
            pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
