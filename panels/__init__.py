"""panels — N-Panel (View3D > UI region) の B-Name タブ."""

from __future__ import annotations

import bpy

from . import (
    balloon_panel as _legacy_balloon_panel,
    effect_line_panel as _legacy_effect_line_panel,
    export_panel,
    gpencil_panel,
    layer_panel as _legacy_layer_panel,
    page_panel as _legacy_page_panel,
    coma_camera_panel,
    coma_detail_panel,
    coma_list_panel as _legacy_coma_list_panel,
    coma_tools_panel as _legacy_coma_tools_panel,
    paper_panel,
    tool_panel,
    view_panel,
    work_panel,
)

_MODULES = (
    work_panel,
    paper_panel,
    tool_panel,
    view_panel,
    coma_camera_panel,
    coma_detail_panel,
    gpencil_panel,
    export_panel,
)


def _unregister_legacy_image_layer_panel() -> None:
    """旧「画像レイヤー」独立パネルを登録済みクラス名からも確実に外す."""
    try:
        _legacy_layer_panel.unregister()
    except Exception:
        pass
    for class_name in ("BNAME_PT_image_layers", "BNAME_UL_image_layers"):
        cls = getattr(bpy.types, class_name, None)
        if cls is None:
            continue
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


def _unregister_legacy_tool_panels() -> None:
    """旧独立セクションを Reload Addons 後も残さない."""
    for module in (
        _legacy_balloon_panel,
        _legacy_effect_line_panel,
        _legacy_page_panel,
        _legacy_coma_list_panel,
        _legacy_coma_tools_panel,
    ):
        try:
            module.unregister()
        except Exception:
            pass
    for class_name in (
        "BNAME_UL_balloons",
        "BNAME_UL_texts",
        "BNAME_PT_balloons",
        "BNAME_PT_texts",
        "BNAME_PT_effect_line",
        "BNAME_UL_pages",
        "BNAME_PT_pages",
        "BNAME_OT_coma_enter_from_list",
        "BNAME_UL_comas",
        "BNAME_PT_comas",
        "BNAME_PT_coma_tools",
        "BNAME_PT_coma_shape",
        "BNAME_PT_coma_border",
        "BNAME_PT_coma_white_margin",
    ):
        cls = getattr(bpy.types, class_name, None)
        if cls is None:
            continue
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


def register() -> None:
    # 旧「画像レイヤー」/「フキダシ」/「テキスト」/「効果線」独立パネルは
    # 新 UI では登録しない。
    # Reload Addons 時に前回登録分が残っている場合もここで外す。
    _unregister_legacy_image_layer_panel()
    _unregister_legacy_tool_panels()
    for module in _MODULES:
        module.register()


def unregister() -> None:
    for module in reversed(_MODULES):
        try:
            module.unregister()
        except Exception:
            pass
    _unregister_legacy_image_layer_panel()
    _unregister_legacy_tool_panels()
