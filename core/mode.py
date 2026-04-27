"""紙面編集モード / コマ編集モードの状態管理 (計画書 3.4 参照).

Scene.bname_mode に現在のモード文字列を保持。切り替えは operators の
bname.mode_toggle で行い、描画ハンドラ側 (ui/overlay.py) がモードを
見て紙面 / 個別コマのどちらを描くかを判定する。

Phase 2 段階では状態保持のみ。実際の Scene 差し替え・3D シーン切替は
Phase 4 (3D 連携) で実装する。
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import EnumProperty, StringProperty

from ..utils import log

_logger = log.get_logger(__name__)

MODE_PAGE = "PAGE"
MODE_PANEL = "PANEL"

_MODE_ITEMS = (
    (MODE_PAGE, "紙面編集", "原稿用紙全体を編集するモード"),
    (MODE_PANEL, "コマ編集", "選択中のコマの 3D シーンを編集するモード"),
)


def register() -> None:
    bpy.types.Scene.bname_mode = EnumProperty(
        name="B-Name モード",
        items=_MODE_ITEMS,
        default=MODE_PAGE,
    )
    bpy.types.Scene.bname_current_panel_stem = StringProperty(
        name="現在編集中のコマ stem",
        default="",
    )
    bpy.types.Scene.bname_current_panel_page_id = StringProperty(
        name="現在編集中のコマ page_id",
        default="",
    )
    _logger.debug("mode registered")


def unregister() -> None:
    try:
        del bpy.types.Scene.bname_mode
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bname_current_panel_stem
    except AttributeError:
        pass
    try:
        del bpy.types.Scene.bname_current_panel_page_id
    except AttributeError:
        pass


def _find_work_root(blend_path: Path) -> Path | None:
    p = blend_path.parent
    for _ in range(6):
        if p.suffix == ".bname":
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


def _infer_mode_from_filepath(scene) -> tuple[str, str, str] | None:
    path_text = str(getattr(bpy.data, "filepath", "") or "")
    if not path_text:
        return None
    blend_path = Path(path_text)
    if blend_path.name == "":
        return None
    work = getattr(scene, "bname_work", None)
    work_dir_text = str(getattr(work, "work_dir", "") or "")
    work_dir = Path(work_dir_text) if work_dir_text else _find_work_root(blend_path)
    if work_dir is None:
        return None
    try:
        rel = blend_path.resolve().relative_to(work_dir.resolve())
    except ValueError:
        fallback_work_dir = _find_work_root(blend_path)
        if fallback_work_dir is None:
            return None
        try:
            rel = blend_path.resolve().relative_to(fallback_work_dir.resolve())
        except ValueError:
            return None
    parts = rel.parts
    if len(parts) == 1 and parts[0] == "work.blend":
        return MODE_PAGE, "", ""
    if (
        len(parts) == 4
        and parts[0] == "pages"
        and parts[2] == "panels"
        and parts[3].endswith(".blend")
    ):
        page_id = parts[1]
        stem = parts[3][: -len(".blend")]
        if page_id and stem.startswith("panel_"):
            return MODE_PANEL, page_id, stem
    return None


def _sync_scene_state_from_filepath(scene, mode: str, page_id: str, panel_stem: str) -> None:
    try:
        if getattr(scene, "bname_mode", MODE_PAGE) != mode:
            scene.bname_mode = mode
    except Exception:  # noqa: BLE001
        return
    if mode == MODE_PANEL:
        try:
            if str(getattr(scene, "bname_current_panel_page_id", "") or "") != page_id:
                scene.bname_current_panel_page_id = page_id
            if str(getattr(scene, "bname_current_panel_stem", "") or "") != panel_stem:
                scene.bname_current_panel_stem = panel_stem
            work = getattr(scene, "bname_work", None)
            for page_index, page in enumerate(getattr(work, "pages", []) or []):
                if str(getattr(page, "id", "") or "") != page_id:
                    continue
                try:
                    if int(getattr(work, "active_page_index", -1)) != page_index:
                        work.active_page_index = page_index
                except Exception:  # noqa: BLE001
                    pass
                for panel_index, panel in enumerate(getattr(page, "panels", []) or []):
                    if str(getattr(panel, "panel_stem", "") or "") == panel_stem:
                        try:
                            if int(getattr(page, "active_panel_index", -1)) != panel_index:
                                page.active_panel_index = panel_index
                        except Exception:  # noqa: BLE001
                            pass
                        break
                break
        except Exception:  # noqa: BLE001
            pass
    elif mode == MODE_PAGE:
        try:
            if str(getattr(scene, "bname_current_panel_page_id", "") or ""):
                scene.bname_current_panel_page_id = ""
            if str(getattr(scene, "bname_current_panel_stem", "") or ""):
                scene.bname_current_panel_stem = ""
            if hasattr(scene, "bname_overview_mode") and not bool(scene.bname_overview_mode):
                scene.bname_overview_mode = True
        except Exception:  # noqa: BLE001
            pass


def get_mode(context: bpy.types.Context | None = None) -> str:
    ctx = context or bpy.context
    scene = getattr(ctx, "scene", None)
    if scene is None:
        return MODE_PAGE
    inferred = _infer_mode_from_filepath(scene)
    if inferred is not None:
        mode, page_id, panel_stem = inferred
        _sync_scene_state_from_filepath(scene, mode, page_id, panel_stem)
        return mode
    return getattr(scene, "bname_mode", MODE_PAGE)


def set_mode(mode: str, context: bpy.types.Context | None = None) -> None:
    ctx = context or bpy.context
    scene = getattr(ctx, "scene", None)
    if scene is None:
        return
    if mode not in (MODE_PAGE, MODE_PANEL):
        raise ValueError(f"invalid mode: {mode}")
    scene.bname_mode = mode
