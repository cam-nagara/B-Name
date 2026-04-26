"""panel_NNN.blend 用の scene 構築/掃除ヘルパ."""

from __future__ import annotations

import re
from pathlib import Path

import bpy

from ..core.mode import MODE_PANEL, set_mode
from ..core.work import find_page_by_id, get_work
from . import gpencil as gp_utils
from . import log

_logger = log.get_logger(__name__)

_PAGE_COLLECTION_RE = re.compile(r"^page_\d{4}(?:-\d{4})?$")
_PAGE_HELPER_OBJECT_RE = re.compile(
    r"^page_\d{4}(?:-\d{4})?_(?:paper|sketch(?:_R)?)$"
)
_PAGE_HELPER_DATA_RE = re.compile(
    r"^page_\d{4}(?:-\d{4})?_(?:paper_data|sketch_data(?:_R)?)$"
)


def bootstrap_new_panel_blend(
    context,
    work_dir: Path,
    page_id: str,
    panel_stem: str,
) -> bool:
    """空の current mainfile を panel.blend 用 scene として初期化する."""
    scene = _resolve_scene(context)
    if scene is None:
        return False

    _reset_current_mainfile_to_empty(scene)
    work = _sync_scene_work_from_disk(context, work_dir)
    if work is None:
        return False
    if not _set_panel_scene_state(context, work, page_id, panel_stem):
        return False
    prepare_panel_blend_scene(context)
    try:
        from . import panel_camera

        panel_camera.ensure_panel_camera_scene(
            context,
            work=work,
            page_id=page_id,
            panel_stem=panel_stem,
            generate_references=True,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("bootstrap_new_panel_blend: panel camera setup failed")
    return True


def prepare_panel_blend_scene(context) -> None:
    """panel.blend を panel 専用 scene に寄せる.

    既存の save_as 由来 panel.blend を開いたときも、この関数で work.blend 由来の
    B-Name collection / paper / GP を scene から掃除する。
    """
    scene = _resolve_scene(context)
    if scene is None:
        return
    try:
        scene.bname_overview_mode = False
    except Exception:  # noqa: BLE001
        pass

    roots = _panel_cleanup_roots(scene)
    for root in roots:
        _rehome_non_internal_objects(scene, root)
    _remove_internal_bname_objects()
    for root in roots:
        _remove_collection_tree(scene, root)
    _purge_internal_bname_data()


def _resolve_scene(context):
    scene = getattr(context, "scene", None) if context is not None else None
    return scene or bpy.context.scene


def _panel_cleanup_roots(scene) -> list[object]:
    roots: list[object] = []
    for child in tuple(scene.collection.children):
        if _is_internal_bname_collection(child):
            roots.append(child)
    root = bpy.data.collections.get(gp_utils.ROOT_COLLECTION_NAME)
    if root is not None and root not in roots:
        roots.append(root)
    return roots


def _sync_scene_work_from_disk(context, work_dir: Path):
    from . import handlers

    return handlers.sync_scene_work_from_disk(context, Path(work_dir))


def _set_panel_scene_state(context, work, page_id: str, panel_stem: str) -> bool:
    page = find_page_by_id(work, page_id)
    if page is None:
        _logger.error("bootstrap_new_panel_blend: page not found: %s", page_id)
        return False
    scene = _resolve_scene(context)
    if scene is None:
        return False

    for idx, candidate in enumerate(work.pages):
        if getattr(candidate, "id", "") == page_id:
            work.active_page_index = idx
            break
    active_panel_index = -1
    for idx, entry in enumerate(page.panels):
        if getattr(entry, "panel_stem", "") == panel_stem:
            active_panel_index = idx
            break
    if active_panel_index < 0:
        _logger.error(
            "bootstrap_new_panel_blend: panel not found: %s/%s",
            page_id,
            panel_stem,
        )
        return False
    page.active_panel_index = active_panel_index
    scene.bname_current_panel_stem = panel_stem
    scene.bname_current_panel_page_id = page_id
    set_mode(MODE_PANEL, context)
    return True


def _reset_current_mainfile_to_empty(scene) -> None:
    """startup file の内容を空にし、panel.blend 用の最小 scene にする."""
    for other_scene in tuple(bpy.data.scenes):
        if other_scene == scene:
            continue
        try:
            bpy.data.scenes.remove(other_scene)
        except Exception:  # noqa: BLE001
            _logger.exception("reset panel scene: remove scene failed: %s", other_scene.name)

    for obj in tuple(bpy.data.objects):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.exception("reset panel scene: remove object failed: %s", obj.name)

    for child in tuple(scene.collection.children):
        try:
            scene.collection.children.unlink(child)
        except Exception:  # noqa: BLE001
            _logger.exception("reset panel scene: unlink child collection failed: %s", child.name)

    for coll in tuple(bpy.data.collections):
        if coll == scene.collection:
            continue
        try:
            bpy.data.collections.remove(coll)
        except Exception:  # noqa: BLE001
            _logger.exception("reset panel scene: remove collection failed: %s", coll.name)

    _purge_generic_orphan_data()


def _rehome_non_internal_objects(scene, root) -> None:
    target = scene.collection
    for coll in _collection_tree(root):
        for child in tuple(coll.children):
            if _is_internal_bname_collection(child):
                continue
            if child.name in target.children:
                continue
            try:
                target.children.link(child)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "prepare_panel_blend_scene: rehome child collection failed: %s",
                    child.name,
                )
        for obj in tuple(coll.objects):
            if _is_internal_bname_object(obj):
                continue
            if obj.name in target.objects:
                continue
            try:
                target.objects.link(obj)
            except Exception:  # noqa: BLE001
                _logger.exception("prepare_panel_blend_scene: rehome failed: %s", obj.name)


def _remove_internal_bname_objects() -> None:
    for obj in tuple(bpy.data.objects):
        if not _is_internal_bname_object(obj):
            continue
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            _logger.exception("prepare_panel_blend_scene: remove internal object failed: %s", obj.name)


def _remove_collection_tree(scene, root) -> None:
    for coll in reversed(tuple(_collection_tree(root))):
        if not _is_internal_bname_collection(coll):
            continue
        _unlink_collection_from_all_parents(scene, coll)
        if coll.users == 0:
            try:
                bpy.data.collections.remove(coll)
            except Exception:  # noqa: BLE001
                _logger.exception("prepare_panel_blend_scene: remove collection failed: %s", coll.name)


def _collection_tree(root):
    yield root
    for child in tuple(root.children):
        yield from _collection_tree(child)


def _unlink_collection_from_all_parents(scene, coll) -> None:
    try:
        if coll.name in scene.collection.children:
            scene.collection.children.unlink(coll)
    except Exception:  # noqa: BLE001
        _logger.exception("prepare_panel_blend_scene: unlink scene child failed: %s", coll.name)
    for parent in tuple(bpy.data.collections):
        if parent == coll:
            continue
        try:
            if coll.name in parent.children:
                parent.children.unlink(coll)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "prepare_panel_blend_scene: unlink parent child failed: %s <- %s",
                parent.name,
                coll.name,
            )


def _purge_internal_bname_data() -> None:
    for coll in tuple(bpy.data.collections):
        if not _is_internal_bname_collection(coll):
            continue
        if coll.users != 0:
            continue
        try:
            bpy.data.collections.remove(coll)
        except Exception:  # noqa: BLE001
            _logger.exception("prepare_panel_blend_scene: purge collection failed: %s", coll.name)

    for mesh in tuple(bpy.data.meshes):
        if not _PAGE_HELPER_DATA_RE.match(mesh.name):
            continue
        if mesh.users != 0:
            continue
        try:
            bpy.data.meshes.remove(mesh)
        except Exception:  # noqa: BLE001
            _logger.exception("prepare_panel_blend_scene: purge mesh failed: %s", mesh.name)

    gp_blocks = getattr(bpy.data, "grease_pencils_v3", None)
    if gp_blocks is None:
        gp_blocks = getattr(bpy.data, "grease_pencils", None)
    if gp_blocks is not None:
        for gp_data in tuple(gp_blocks):
            if not _is_internal_bname_gp_data_name(gp_data.name):
                continue
            if gp_data.users != 0:
                continue
            try:
                gp_blocks.remove(gp_data)
            except Exception:  # noqa: BLE001
                _logger.exception("prepare_panel_blend_scene: purge GP failed: %s", gp_data.name)

    mat = bpy.data.materials.get(gp_utils.PAPER_MATERIAL_NAME)
    if mat is not None and mat.users == 0:
        try:
            bpy.data.materials.remove(mat)
        except Exception:  # noqa: BLE001
            _logger.exception("prepare_panel_blend_scene: purge paper material failed")


def _purge_generic_orphan_data() -> None:
    _purge_orphan_collection(bpy.data.meshes)
    _purge_orphan_collection(bpy.data.cameras)
    _purge_orphan_collection(bpy.data.lights)
    _purge_orphan_collection(bpy.data.armatures)
    _purge_orphan_collection(bpy.data.curves)
    _purge_orphan_collection(bpy.data.materials)
    _purge_orphan_collection(bpy.data.images)
    gp_blocks = getattr(bpy.data, "grease_pencils_v3", None)
    if gp_blocks is None:
        gp_blocks = getattr(bpy.data, "grease_pencils", None)
    if gp_blocks is not None:
        _purge_orphan_collection(gp_blocks)


def _purge_orphan_collection(blocks) -> None:
    for block in tuple(blocks):
        if getattr(block, "users", 1) != 0:
            continue
        try:
            blocks.remove(block)
        except Exception:  # noqa: BLE001
            pass


def _is_internal_bname_collection(coll) -> bool:
    return coll.name == gp_utils.ROOT_COLLECTION_NAME or bool(_PAGE_COLLECTION_RE.match(coll.name))


def _is_internal_bname_object(obj) -> bool:
    if obj.name == gp_utils.MASTER_GP_OBJECT_NAME:
        return True
    return bool(_PAGE_HELPER_OBJECT_RE.match(obj.name))


def _is_internal_bname_gp_data_name(name: str) -> bool:
    return name == gp_utils.MASTER_GP_DATA_NAME or bool(_PAGE_HELPER_DATA_RE.match(name))
