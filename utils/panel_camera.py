"""コマ編集モード用カメラ・下絵管理ヘルパ."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

import bpy

from ..core.mode import MODE_PANEL, get_mode
from ..core.work import find_page_by_id, get_work
from ..io import export_pipeline
from . import log, paths, panel_preview
from .geom import mm_to_px

_logger = log.get_logger(__name__)

PANEL_CAMERA_NAME = "Camera"
REFERENCE_DIR_NAME = "panel_camera_refs"
MANAGED_IMAGE_PROP = "_bname_panel_camera_ref"
NAME_REF_PREFIX = "BName_ネーム"
KOMA_REF_PREFIX = "BName_コマ"
DEFAULT_REF_DPI = 120
DEFAULT_CAMERA_DISTANCE = 6.0


def ensure_panel_camera_scene(
    context,
    work=None,
    page_id: str = "",
    panel_stem: str = "",
    *,
    generate_references: bool = True,
) -> None:
    """panel_NNN.blend 内にカメラと下絵背景を整備する."""
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    if scene is None:
        return
    if work is None:
        work = get_work(context)
    if not page_id:
        page_id = str(getattr(scene, "bname_current_panel_page_id", "") or "")
    if not panel_stem:
        panel_stem = str(getattr(scene, "bname_current_panel_stem", "") or "")

    camera = ensure_panel_camera(scene)
    scene.camera = camera
    configure_render_for_current_panel(scene, work, page_id, panel_stem)
    ensure_default_resolution_settings(scene)

    refs: list[ReferenceImage] = []
    if generate_references and work is not None and getattr(work, "work_dir", ""):
        refs = ensure_reference_images(work, page_id, panel_stem)
    configure_camera_backgrounds(scene, camera, refs, page_id, panel_stem)
    update_render_border_from_current_panel(context)
    view_camera_in_viewports(context)


def ensure_panel_camera(scene):
    """panel blend 用 Camera オブジェクトを取得または作成する."""
    cam_obj = scene.camera
    created = False
    if cam_obj is None or getattr(cam_obj, "type", "") != "CAMERA":
        cam_obj = bpy.data.objects.get(PANEL_CAMERA_NAME)
    if cam_obj is None or getattr(cam_obj, "type", "") != "CAMERA":
        cam_data = bpy.data.cameras.new(PANEL_CAMERA_NAME)
        cam_obj = bpy.data.objects.new(PANEL_CAMERA_NAME, cam_data)
        scene.collection.objects.link(cam_obj)
        created = True
    cam_obj.name = PANEL_CAMERA_NAME
    cam_obj["bname_panel_camera"] = True
    if created:
        try:
            cam_obj.location = (0.0, -DEFAULT_CAMERA_DISTANCE, 0.0)
            cam_obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)
        except Exception:  # noqa: BLE001
            pass
    cam_data = cam_obj.data
    if getattr(cam_data, "clip_start", 0.0) <= 0.0:
        cam_data.clip_start = 0.01
    cam_data.clip_end = max(float(getattr(cam_data, "clip_end", 100.0)), 1000.0)
    if hasattr(cam_data, "show_background_images"):
        cam_data.show_background_images = True
    return cam_obj


def configure_render_for_current_panel(scene, work, page_id: str, panel_stem: str) -> None:
    """選択コマのbbox比率に合わせてカメラ出力解像度を初期化する."""
    if (
        int(getattr(scene, "bname_panel_camera_original_resolution_x", 0)) > 0
        and int(getattr(scene, "bname_panel_camera_original_resolution_y", 0)) > 0
    ):
        return
    panel = _resolve_panel(work, page_id, panel_stem)
    width_mm, height_mm = _panel_bbox_size(panel)
    if width_mm <= 0.0 or height_mm <= 0.0:
        width_mm, height_mm = 16.0, 9.0
    long_edge = 1920
    if width_mm >= height_mm:
        res_x = long_edge
        res_y = max(1, round(long_edge * height_mm / width_mm))
    else:
        res_y = long_edge
        res_x = max(1, round(long_edge * width_mm / height_mm))
    scene.render.resolution_x = int(res_x)
    scene.render.resolution_y = int(res_y)
    if int(getattr(scene, "bname_panel_camera_original_resolution_x", 0)) <= 0:
        scene.bname_panel_camera_original_resolution_x = int(res_x)
    if int(getattr(scene, "bname_panel_camera_original_resolution_y", 0)) <= 0:
        scene.bname_panel_camera_original_resolution_y = int(res_y)


def ensure_default_resolution_settings(scene) -> None:
    settings = getattr(scene, "bname_panel_camera_resolution_settings", None)
    if settings is None or len(settings) > 0:
        return
    item = settings.add()
    item.name = "現在のコマ"
    item.resolution_x = int(getattr(scene.render, "resolution_x", 1920))
    item.resolution_y = int(getattr(scene.render, "resolution_y", 1080))


class ReferenceImage:
    def __init__(self, path: Path, label: str, kind: str, page_id: str, visible: bool) -> None:
        self.path = Path(path)
        self.label = label
        self.kind = kind
        self.page_id = page_id
        self.visible = visible


def ensure_reference_images(work, current_page_id: str, panel_stem: str) -> list[ReferenceImage]:
    """全ページプレビューと現在コマ用クロップを生成して返す."""
    if not export_pipeline.has_pillow():
        _logger.warning("panel camera references require Pillow")
        return _collect_existing_reference_images(work, current_page_id, panel_stem)
    work_dir = Path(work.work_dir)
    ref_dir = reference_dir(work_dir)
    ref_dir.mkdir(parents=True, exist_ok=True)

    refs: list[ReferenceImage] = []
    current_page_ref: Path | None = None
    include_work_blend_mtime = _has_master_gpencil()
    for page in getattr(work, "pages", []):
        if not getattr(page, "id", ""):
            continue
        out = ref_dir / f"{NAME_REF_PREFIX}_{page.id}.png"
        if _reference_is_stale(work_dir, page, out, include_work_blend=include_work_blend_mtime):
            _render_page_reference(work, page, out)
        if out.is_file():
            refs.append(
                ReferenceImage(
                    out,
                    f"{NAME_REF_PREFIX}_{page.id}",
                    "name",
                    page.id,
                    visible=page.id == current_page_id,
                )
            )
            if page.id == current_page_id:
                current_page_ref = out

    page = find_page_by_id(work, current_page_id)
    panel = _resolve_panel(work, current_page_id, panel_stem)
    if page is not None and panel is not None:
        if current_page_ref is None or not current_page_ref.is_file():
            current_page_ref = ref_dir / f"{NAME_REF_PREFIX}_{page.id}.png"
            _render_page_reference(work, page, current_page_ref)
        crop = ref_dir / f"{KOMA_REF_PREFIX}_{page.id}_{panel_stem}.png"
        if _crop_is_stale(current_page_ref, crop):
            _render_current_panel_crop(work, panel, current_page_ref, crop)
        if crop.is_file():
            refs.insert(
                0,
                ReferenceImage(
                    crop,
                    f"{KOMA_REF_PREFIX}_{page.id}_{panel_stem}",
                    "koma",
                    page.id,
                    visible=True,
                ),
            )
    return refs


def _collect_existing_reference_images(work, current_page_id: str, panel_stem: str) -> list[ReferenceImage]:
    """Pillow が無い環境でも、既存PNGやコマプレビューを下絵として拾う."""
    work_dir = Path(getattr(work, "work_dir", "") or "")
    ref_dir = reference_dir(work_dir)
    refs: list[ReferenceImage] = []
    for page in getattr(work, "pages", []):
        page_id = str(getattr(page, "id", "") or "")
        if not page_id:
            continue
        path = ref_dir / f"{NAME_REF_PREFIX}_{page_id}.png"
        if path.is_file():
            refs.append(
                ReferenceImage(
                    path,
                    f"{NAME_REF_PREFIX}_{page_id}",
                    "name",
                    page_id,
                    visible=page_id == current_page_id,
                )
            )
    crop = ref_dir / f"{KOMA_REF_PREFIX}_{current_page_id}_{panel_stem}.png"
    if crop.is_file():
        refs.insert(
            0,
            ReferenceImage(
                crop,
                f"{KOMA_REF_PREFIX}_{current_page_id}_{panel_stem}",
                "koma",
                current_page_id,
                visible=True,
            ),
        )
        return refs
    panel = _resolve_panel(work, current_page_id, panel_stem)
    source = panel_preview.panel_preview_source_path(work_dir, current_page_id, panel)
    if source is not None and source.is_file():
        refs.insert(
            0,
            ReferenceImage(
                source,
                f"{KOMA_REF_PREFIX}_{current_page_id}_{panel_stem}",
                "koma",
                current_page_id,
                visible=True,
            ),
        )
    return refs


def reference_dir(work_dir: Path) -> Path:
    return paths.assets_dir(Path(work_dir)) / REFERENCE_DIR_NAME


def configure_camera_backgrounds(scene, camera, refs: Iterable[ReferenceImage], page_id: str, panel_stem: str) -> None:
    ref_list = list(refs)
    if not ref_list:
        # 下絵生成に失敗した場合でも、既存のカメラ下絵を消さない。
        return
    settings = getattr(scene, "bname_panel_camera_settings", None)
    name_visible = bool(getattr(settings, "name_visible", False))
    name_show_all_pages = bool(getattr(settings, "name_show_all_pages", False))
    koma_visible = bool(getattr(settings, "koma_visible", True))
    name_alpha = float(getattr(settings, "name_bg_images_opacity", 0.5))
    koma_alpha = float(getattr(settings, "koma_bg_images_opacity", 1.0))
    scale = float(getattr(settings, "bg_images_scale", 1.0))
    koma_depth_back = bool(getattr(settings, "koma_depth", False))

    data = getattr(camera, "data", None)
    if data is None:
        return
    _clear_managed_backgrounds(data)
    for ref in ref_list:
        img = _load_reference_image(ref.path, ref.label)
        if img is None:
            continue
        try:
            img["bname_kind"] = ref.kind
            img["bname_page_id"] = ref.page_id
            img["bname_panel_stem"] = panel_stem if ref.kind == "koma" else ""
        except Exception:  # noqa: BLE001
            pass
        bg = data.background_images.new()
        bg.image = img
        alpha = koma_alpha if ref.kind == "koma" else name_alpha
        if ref.kind == "koma":
            visible = koma_visible and ref.visible
        else:
            visible = name_visible and (ref.visible or name_show_all_pages)
        depth = "BACK" if ref.kind == "koma" and koma_depth_back else "FRONT"
        _set_bg_attr(bg, "alpha", alpha)
        _set_bg_attr(bg, "scale", scale)
        _set_bg_attr(bg, "rotation", 0.0)
        _set_bg_attr(bg, "offset", (0.0, 0.0))
        _set_bg_attr(bg, "display_depth", depth)
        _set_bg_attr(bg, "frame_method", "FIT")
        _set_bg_attr(bg, "show_background_image", bool(visible))
    if hasattr(data, "show_background_images"):
        data.show_background_images = True


def _clear_managed_backgrounds(camera_data) -> None:
    for bg in reversed(tuple(getattr(camera_data, "background_images", []))):
        if not _is_managed_background(bg):
            continue
        try:
            camera_data.background_images.remove(bg)
        except Exception:  # noqa: BLE001
            pass


def _is_managed_background(bg) -> bool:
    img = getattr(bg, "image", None)
    try:
        return bool(img and img.get(MANAGED_IMAGE_PROP, False))
    except Exception:  # noqa: BLE001
        return False


def _load_reference_image(path: Path, label: str):
    abspath = str(Path(path).resolve())
    try:
        img = bpy.data.images.load(abspath, check_existing=True)
        img.name = label
        img[MANAGED_IMAGE_PROP] = True
        return img
    except Exception:  # noqa: BLE001
        _logger.warning("panel camera reference load failed: %s", path, exc_info=True)
        return None


def set_background_images_opacity(context, opacity: float) -> None:
    for bg in _iter_camera_backgrounds(context):
        _set_bg_attr(bg, "alpha", opacity)


def set_background_images_scale(context, scale: float) -> None:
    for bg in _iter_camera_backgrounds(context):
        _set_bg_attr(bg, "scale", scale)


def camera_background_count(context) -> int:
    return len(_iter_camera_backgrounds(context))


def can_render_references() -> bool:
    return export_pipeline.has_pillow()


def capture_managed_background_visibility(context) -> list[tuple[object, bool]]:
    state: list[tuple[object, bool]] = []
    for bg in _iter_camera_backgrounds(context):
        if _is_managed_background(bg):
            state.append((bg, bool(getattr(bg, "show_background_image", False))))
    return state


def set_managed_background_visibility(context, visible: bool) -> None:
    for bg in _iter_camera_backgrounds(context):
        if _is_managed_background(bg):
            _set_bg_attr(bg, "show_background_image", bool(visible))


def restore_background_visibility(state: Iterable[tuple[object, bool]]) -> None:
    for bg, visible in state:
        _set_bg_attr(bg, "show_background_image", bool(visible))


def toggle_all_backgrounds(context) -> bool:
    backgrounds = _iter_camera_backgrounds(context)
    visible = not all(bool(getattr(bg, "show_background_image", False)) for bg in backgrounds)
    for bg in backgrounds:
        _set_bg_attr(bg, "show_background_image", visible)
    return visible


def set_background_images_properties(context, name_filter: str, *, opacity=None, scale=None) -> None:
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        if img is None or name_filter not in getattr(img, "name", ""):
            continue
        if opacity is not None:
            _set_bg_attr(bg, "alpha", float(opacity))
        if scale is not None:
            _set_bg_attr(bg, "scale", float(scale))


def set_background_image_visibility(context, name_filter: str, visible: bool) -> None:
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        if img is not None and name_filter in getattr(img, "name", ""):
            _set_bg_attr(bg, "show_background_image", bool(visible))


def set_background_image_rotation(context, name_filter: str, rotation: float) -> None:
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        if img is not None and name_filter in getattr(img, "name", ""):
            _set_bg_attr(bg, "rotation", float(rotation))


def set_koma_background_depth(context, *, back: bool) -> None:
    depth = "BACK" if back else "FRONT"
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        if img is not None and "コマ" in getattr(img, "name", ""):
            _set_bg_attr(bg, "display_depth", depth)


def toggle_backgrounds_by_kind(context, kind: str) -> bool:
    settings = getattr(context.scene, "bname_panel_camera_settings", None)
    if settings is None:
        return False
    if kind == "name":
        settings.name_visible = not bool(settings.name_visible)
        name_filter = "ネーム"
        visible = settings.name_visible
    else:
        settings.koma_visible = not bool(settings.koma_visible)
        name_filter = "コマ"
        visible = settings.koma_visible
    set_background_image_visibility(context, name_filter, visible)
    return visible


def set_page_reference_visibility(context, *, show_all: bool) -> None:
    settings = getattr(context.scene, "bname_panel_camera_settings", None)
    name_visible = bool(getattr(settings, "name_visible", False))
    current_page_id = str(getattr(context.scene, "bname_current_panel_page_id", "") or "")
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        if img is None or "ネーム" not in getattr(img, "name", ""):
            continue
        page_id = ""
        try:
            page_id = str(img.get("bname_page_id", "") or "")
        except Exception:  # noqa: BLE001
            page_id = ""
        _set_bg_attr(
            bg,
            "show_background_image",
            bool(name_visible and (show_all or page_id == current_page_id)),
        )


def reload_background_images(context) -> int:
    count = 0
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        if img is None:
            continue
        try:
            img.reload()
            count += 1
        except Exception:  # noqa: BLE001
            pass
    return count


def update_view(context) -> None:
    for mat in bpy.data.materials:
        node_tree = getattr(mat, "node_tree", None)
        if node_tree is not None:
            _update_node_tree(node_tree)
    try:
        context.view_layer.update()
    except Exception:  # noqa: BLE001
        pass
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except Exception:  # noqa: BLE001
        pass


def update_render_border_from_current_panel(context) -> None:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    if scene is None or scene.camera is None:
        return
    target = None
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        if img is not None and "コマ" in getattr(img, "name", ""):
            target = bg
            break
    if target is None or target.image is None:
        scene.render.use_border = False
        return
    scale = float(getattr(target, "scale", 1.0))
    image_width = max(1, int(target.image.size[0]))
    image_height = max(1, int(target.image.size[1]))
    res_x = max(1, int(scene.render.resolution_x))
    res_y = max(1, int(scene.render.resolution_y))
    aspect_image = image_width / image_height
    aspect_render = res_x / res_y
    if aspect_image > aspect_render:
        border_width = scale
        border_height = scale * (res_x / image_width) * (image_height / res_y)
    else:
        border_height = scale
        border_width = scale * (res_y / image_height) * (image_width / res_x)
    border_width = max(0.0, min(1.0, border_width))
    border_height = max(0.0, min(1.0, border_height))
    scene.render.use_border = True
    scene.render.border_min_x = (1.0 - border_width) * 0.5
    scene.render.border_max_x = scene.render.border_min_x + border_width
    scene.render.border_min_y = (1.0 - border_height) * 0.5
    scene.render.border_max_y = scene.render.border_min_y + border_height


def apply_selected_resolution_setting(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    coll = getattr(scene, "bname_panel_camera_resolution_settings", None)
    idx = int(getattr(scene, "bname_panel_camera_resolution_settings_index", 0))
    if coll is None or not (0 <= idx < len(coll)):
        return
    item = coll[idx]
    scene.bname_panel_camera_original_resolution_x = int(item.resolution_x)
    scene.bname_panel_camera_original_resolution_y = int(item.resolution_y)
    scene.render.resolution_x = int(item.resolution_x)
    scene.render.resolution_y = int(item.resolution_y)
    if bool(getattr(scene, "bname_panel_camera_fisheye_layout_mode", False)):
        _apply_fisheye_layout(scene)
    if bool(getattr(scene, "bname_panel_camera_reduction_mode", False)):
        _apply_reduction_layout(scene)


def apply_fisheye_mode(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    cam = scene.camera
    if cam is None or getattr(cam, "type", "") != "CAMERA":
        cam = ensure_panel_camera(scene)
        scene.camera = cam
    enabled = bool(getattr(scene, "bname_panel_camera_fisheye_layout_mode", False))
    if enabled:
        if int(getattr(scene, "bname_panel_camera_original_resolution_x", 0)) <= 0:
            scene.bname_panel_camera_original_resolution_x = int(scene.render.resolution_x)
        if int(getattr(scene, "bname_panel_camera_original_resolution_y", 0)) <= 0:
            scene.bname_panel_camera_original_resolution_y = int(scene.render.resolution_y)
        scene.bname_panel_camera_lens = float(cam.data.lens)
        cam.data.type = "PANO"
        if hasattr(cam.data, "panorama_type"):
            try:
                cam.data.panorama_type = "FISHEYE_EQUISOLID"
            except TypeError:
                pass
        cam.data.fisheye_fov = float(getattr(scene, "bname_panel_camera_fisheye_fov", math.pi))
        scene.render.engine = "CYCLES"
        _apply_fisheye_layout(scene)
    else:
        scene.bname_panel_camera_fisheye_fov = float(getattr(cam.data, "fisheye_fov", math.pi))
        cam.data.type = "PERSP"
        cam.data.lens = float(getattr(scene, "bname_panel_camera_lens", cam.data.lens))
        if bool(getattr(scene, "bname_panel_camera_reduction_mode", False)):
            _apply_reduction_layout(scene)
        else:
            _restore_original_resolution(scene)
    settings = getattr(scene, "bname_panel_camera_settings", None)
    if settings is not None:
        set_background_images_scale(context, float(settings.bg_images_scale))
    update_render_border_from_current_panel(context)


def apply_reduction_mode(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    if int(getattr(scene, "bname_panel_camera_original_resolution_x", 0)) <= 0:
        scene.bname_panel_camera_original_resolution_x = int(scene.render.resolution_x)
    if int(getattr(scene, "bname_panel_camera_original_resolution_y", 0)) <= 0:
        scene.bname_panel_camera_original_resolution_y = int(scene.render.resolution_y)
    if bool(getattr(scene, "bname_panel_camera_reduction_mode", False)):
        _adjust_pencil4_line_width(scene, float(scene.bname_panel_camera_preview_scale_percentage) / 100.0)
        _apply_reduction_layout(scene)
    else:
        _restore_pencil4_line_widths(scene)
        if bool(getattr(scene, "bname_panel_camera_fisheye_layout_mode", False)):
            _apply_fisheye_layout(scene)
        else:
            _restore_original_resolution(scene)
    update_view(context)


def view_camera_in_viewports(context) -> None:
    screen = getattr(context, "screen", None)
    if screen is None or get_mode(context) != MODE_PANEL:
        return
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for space in area.spaces:
            if space.type == "VIEW_3D" and getattr(space, "region_3d", None) is not None:
                try:
                    space.region_3d.view_perspective = "CAMERA"
                except Exception:  # noqa: BLE001
                    pass


def _apply_fisheye_layout(scene) -> None:
    ox = int(getattr(scene, "bname_panel_camera_original_resolution_x", 0)) or int(scene.render.resolution_x)
    oy = int(getattr(scene, "bname_panel_camera_original_resolution_y", 0)) or int(scene.render.resolution_y)
    edge = max(1, ox, oy)
    if bool(getattr(scene, "bname_panel_camera_reduction_mode", False)):
        scale = float(getattr(scene, "bname_panel_camera_preview_scale_percentage", 100.0)) / 100.0
        edge = max(1, int(edge * scale))
    scene.render.resolution_x = edge
    scene.render.resolution_y = edge


def _apply_reduction_layout(scene) -> None:
    ox = int(getattr(scene, "bname_panel_camera_original_resolution_x", 0)) or int(scene.render.resolution_x)
    oy = int(getattr(scene, "bname_panel_camera_original_resolution_y", 0)) or int(scene.render.resolution_y)
    scale = float(getattr(scene, "bname_panel_camera_preview_scale_percentage", 100.0)) / 100.0
    if bool(getattr(scene, "bname_panel_camera_fisheye_layout_mode", False)):
        edge = max(1, ox, oy)
        scene.render.resolution_x = max(1, int(edge * scale))
        scene.render.resolution_y = max(1, int(edge * scale))
    else:
        scene.render.resolution_x = max(1, int(ox * scale))
        scene.render.resolution_y = max(1, int(oy * scale))


def _restore_original_resolution(scene) -> None:
    ox = int(getattr(scene, "bname_panel_camera_original_resolution_x", 0))
    oy = int(getattr(scene, "bname_panel_camera_original_resolution_y", 0))
    if ox > 0 and oy > 0:
        scene.render.resolution_x = ox
        scene.render.resolution_y = oy


def _iter_camera_backgrounds(context):
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    cam = getattr(scene, "camera", None) if scene is not None else None
    data = getattr(cam, "data", None)
    if data is None:
        return []
    return list(getattr(data, "background_images", []))


def _set_bg_attr(bg, attr: str, value) -> None:
    try:
        setattr(bg, attr, value)
    except Exception:  # noqa: BLE001
        pass


def _update_node_tree(node_tree) -> None:
    for node in getattr(node_tree, "nodes", []):
        child_tree = getattr(node, "node_tree", None)
        if child_tree is not None:
            _update_node_tree(child_tree)
    try:
        node_tree.update_tag()
    except Exception:  # noqa: BLE001
        pass


def _adjust_pencil4_line_width(scene, scale: float) -> None:
    for group in bpy.data.node_groups:
        if not group.name.startswith("Pencil+ 4 Line Node Tree"):
            continue
        for node in group.nodes:
            if not node.name.startswith("Brush Settings"):
                continue
            if "original_size" not in node:
                try:
                    node["original_size"] = node.size
                except Exception:  # noqa: BLE001
                    continue
            try:
                node.size = float(node["original_size"]) * scale
            except Exception:  # noqa: BLE001
                pass


def _restore_pencil4_line_widths(_scene) -> None:
    for group in bpy.data.node_groups:
        if not group.name.startswith("Pencil+ 4 Line Node Tree"):
            continue
        for node in group.nodes:
            if node.name.startswith("Brush Settings") and "original_size" in node:
                try:
                    node.size = float(node["original_size"])
                except Exception:  # noqa: BLE001
                    pass


def _render_page_reference(work, page, out: Path) -> bool:
    try:
        options = export_pipeline.ExportOptions(
            format="png",
            color_mode="rgb",
            area="canvas",
            dpi_override=DEFAULT_REF_DPI,
            include_tombo=False,
            include_paper_color=True,
        )
        img = export_pipeline.render_page(work, page, options)
        if img is None:
            return False
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out))
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("panel camera page reference render failed: %s", getattr(page, "id", ""))
        return False


def _render_current_panel_crop(work, panel, page_ref: Path, out: Path) -> bool:
    Image = export_pipeline.Image
    if Image is None:
        return False
    bbox = _panel_bbox(panel)
    if bbox is None:
        return False
    try:
        img = Image.open(str(page_ref)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return False
    min_x, min_y, max_x, max_y = bbox
    dpi = DEFAULT_REF_DPI
    left = int(math.floor(mm_to_px(min_x, dpi)))
    right = int(math.ceil(mm_to_px(max_x, dpi)))
    top = img.height - int(math.ceil(mm_to_px(max_y, dpi)))
    bottom = img.height - int(math.floor(mm_to_px(min_y, dpi)))
    left = max(0, min(img.width - 1, left))
    right = max(left + 1, min(img.width, right))
    top = max(0, min(img.height - 1, top))
    bottom = max(top + 1, min(img.height, bottom))
    crop = img.crop((left, top, right, bottom))
    out.parent.mkdir(parents=True, exist_ok=True)
    crop.save(str(out))
    return True


def _reference_is_stale(work_dir: Path, page, out: Path, *, include_work_blend: bool) -> bool:
    if not out.is_file():
        return True
    latest = _path_mtime(paths.work_meta_path(work_dir))
    if include_work_blend:
        latest = max(latest, _path_mtime(paths.work_blend_path(work_dir)))
    latest = max(latest, _path_mtime(paths.pages_meta_path(work_dir)))
    latest = max(latest, _path_mtime(paths.page_meta_path(work_dir, page.id)))
    for panel in getattr(page, "panels", []):
        source = panel_preview.panel_preview_source_path(work_dir, page.id, panel)
        if source is not None:
            latest = max(latest, _path_mtime(source))
    return _path_mtime(out) < latest


def _has_master_gpencil() -> bool:
    try:
        from . import gpencil as gp_utils

        return gp_utils.get_master_gpencil() is not None
    except Exception:  # noqa: BLE001
        return False


def _crop_is_stale(page_ref: Path | None, crop: Path) -> bool:
    if page_ref is None or not page_ref.is_file():
        return False
    if not crop.is_file():
        return True
    return _path_mtime(crop) < _path_mtime(page_ref)


def _path_mtime(path: Path) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def _resolve_panel(work, page_id: str, panel_stem: str):
    page = find_page_by_id(work, page_id) if work is not None else None
    if page is None:
        return None
    for panel in getattr(page, "panels", []):
        if getattr(panel, "panel_stem", "") == panel_stem:
            return panel
    return None


def _panel_bbox_size(panel) -> tuple[float, float]:
    bbox = _panel_bbox(panel)
    if bbox is None:
        return 0.0, 0.0
    return max(0.0, bbox[2] - bbox[0]), max(0.0, bbox[3] - bbox[1])


def _panel_bbox(panel) -> tuple[float, float, float, float] | None:
    if panel is None:
        return None
    if getattr(panel, "shape_type", "") == "rect":
        x = float(getattr(panel, "rect_x_mm", 0.0))
        y = float(getattr(panel, "rect_y_mm", 0.0))
        w = float(getattr(panel, "rect_width_mm", 0.0))
        h = float(getattr(panel, "rect_height_mm", 0.0))
        if w <= 0.0 or h <= 0.0:
            return None
        return x, y, x + w, y + h
    verts = [(float(v.x_mm), float(v.y_mm)) for v in getattr(panel, "vertices", [])]
    if not verts:
        return None
    xs = [p[0] for p in verts]
    ys = [p[1] for p in verts]
    return min(xs), min(ys), max(xs), max(ys)
