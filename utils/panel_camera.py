"""コマ編集モード用カメラ・下絵管理ヘルパ."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

import bpy

from ..core.mode import MODE_PANEL, get_mode
from ..core.work import find_page_by_id, get_work
from ..io import export_pipeline
from . import log, paths, panel_preview, page_grid
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
    try:
        from . import display_settings

        display_settings.apply_standard_color_management(scene)
    except Exception:  # noqa: BLE001
        pass
    configure_render_for_current_panel(scene, work, page_id, panel_stem)
    ensure_default_resolution_settings(scene)
    sync_world_background_color(context, work=work, page_id=page_id, panel_stem=panel_stem)

    refs: list[ReferenceImage] = []
    if generate_references and work is not None and getattr(work, "work_dir", ""):
        refs = ensure_reference_images(work, page_id, panel_stem)
    configure_camera_backgrounds(scene, camera, refs, page_id, panel_stem)
    update_render_border_from_current_panel(context)
    view_camera_in_viewports(context)
    schedule_panel_view_camera()


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
    """ページ/見開き下絵の比率に合わせてカメラ出力解像度を設定する."""
    _page_count, _render_side, width_mm, height_mm = _reference_frame_info(work, page_id, panel_stem)
    if width_mm <= 0.0 or height_mm <= 0.0:
        width_mm, height_mm = 16.0, 9.0
    long_edge = 1920
    if width_mm >= height_mm:
        res_x = long_edge
        res_y = max(1, round(long_edge * height_mm / width_mm))
    else:
        res_y = long_edge
        res_x = max(1, round(long_edge * width_mm / height_mm))
    if hasattr(scene, "bname_panel_camera_original_resolution_x"):
        scene.bname_panel_camera_original_resolution_x = int(res_x)
    if hasattr(scene, "bname_panel_camera_original_resolution_y"):
        scene.bname_panel_camera_original_resolution_y = int(res_y)
    if not (
        bool(getattr(scene, "bname_panel_camera_fisheye_layout_mode", False))
        or bool(getattr(scene, "bname_panel_camera_reduction_mode", False))
    ):
        scene.render.resolution_x = int(res_x)
        scene.render.resolution_y = int(res_y)


def ensure_default_resolution_settings(scene) -> None:
    settings = getattr(scene, "bname_panel_camera_resolution_settings", None)
    if settings is None or len(settings) > 0:
        return
    item = settings.add()
    item.name = "現在のコマ"
    item.resolution_x = int(getattr(scene.render, "resolution_x", 1920))
    item.resolution_y = int(getattr(scene.render, "resolution_y", 1080))


class ReferenceImage:
    def __init__(
        self,
        path: Path,
        label: str,
        kind: str,
        page_id: str,
        visible: bool,
        *,
        full_page_mask: bool = False,
        page_count: int = 1,
        render_side: str = "full",
    ) -> None:
        self.path = Path(path)
        self.label = label
        self.kind = kind
        self.page_id = page_id
        self.visible = visible
        self.full_page_mask = full_page_mask
        self.page_count = max(1, int(page_count))
        self.render_side = render_side if render_side in {"left", "right", "full"} else "full"


def ensure_reference_images(work, current_page_id: str, panel_stem: str) -> list[ReferenceImage]:
    """現在コマ用のページ全体マスク下絵を生成して返す."""
    if not export_pipeline.has_pillow():
        _logger.warning("panel camera references require Pillow")
        return _collect_existing_reference_images(work, current_page_id, panel_stem)
    work_dir = Path(work.work_dir)
    ref_dir = reference_dir(work_dir)
    ref_dir.mkdir(parents=True, exist_ok=True)

    refs: list[ReferenceImage] = []
    include_work_blend_mtime = _has_master_gpencil()
    page = find_page_by_id(work, current_page_id)
    panel = _resolve_panel(work, current_page_id, panel_stem)
    if page is not None and panel is not None:
        page_count, render_side, _width_mm, _height_mm = _reference_frame_info(work, current_page_id, panel_stem)
        current_page_ref = _ensure_page_reference(work, work_dir, page, ref_dir, include_work_blend_mtime)
        mate_page = _find_spread_mate_page(work, current_page_id)
        mate_page_ref = None
        if mate_page is not None:
            mate_page_ref = _ensure_page_reference(work, work_dir, mate_page, ref_dir, include_work_blend_mtime)
        masked_page = _koma_ref_path(ref_dir, page.id, panel_stem)
        if _panel_mask_is_stale((current_page_ref, mate_page_ref), masked_page):
            _render_current_panel_page_mask(work, page, panel, current_page_ref, mate_page, mate_page_ref, masked_page)
        if masked_page.is_file():
            refs.insert(
                0,
                ReferenceImage(
                    masked_page,
                    f"{KOMA_REF_PREFIX}_{page.id}_{panel_stem}",
                    "koma",
                    page.id,
                    visible=True,
                    full_page_mask=True,
                    page_count=page_count,
                    render_side=render_side,
                ),
            )
    return refs


def _collect_existing_reference_images(work, current_page_id: str, panel_stem: str) -> list[ReferenceImage]:
    """Pillow が無い環境でも、既存PNGやコマプレビューを下絵として拾う."""
    work_dir = Path(getattr(work, "work_dir", "") or "")
    ref_dir = reference_dir(work_dir)
    refs: list[ReferenceImage] = []
    page_count, render_side, _width_mm, _height_mm = _reference_frame_info(work, current_page_id, panel_stem)
    masked_page = _koma_ref_path(ref_dir, current_page_id, panel_stem)
    legacy_crop = ref_dir / f"{KOMA_REF_PREFIX}_{current_page_id}_{panel_stem}.png"
    crop = masked_page if masked_page.is_file() else legacy_crop
    if crop.is_file():
        refs.insert(
            0,
            ReferenceImage(
                crop,
                f"{KOMA_REF_PREFIX}_{current_page_id}_{panel_stem}",
                "koma",
                current_page_id,
                visible=True,
                full_page_mask=crop == masked_page,
                page_count=page_count,
                render_side=render_side,
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
            img["bname_full_page_mask"] = bool(ref.full_page_mask)
            img["bname_page_count"] = int(ref.page_count)
            img["bname_render_side"] = ref.render_side
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
        bg_scale, bg_offset = _background_scale_offset_for_ref(ref, scale)
        _set_bg_attr(bg, "alpha", alpha)
        _set_bg_attr(bg, "scale", bg_scale)
        _set_bg_attr(bg, "rotation", 0.0)
        _set_bg_attr(bg, "offset", bg_offset)
        _set_bg_attr(bg, "display_depth", depth)
        _set_bg_attr(bg, "frame_method", "FIT")
        _set_bg_attr(bg, "show_background_image", bool(visible))
    if hasattr(data, "show_background_images"):
        data.show_background_images = True


def sync_world_background_color(context, *, panel=None, work=None, page_id: str = "", panel_stem: str = "") -> None:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    if scene is None:
        return
    if panel is None:
        if work is None:
            work = get_work(context)
        if not page_id:
            page_id = str(getattr(scene, "bname_current_panel_page_id", "") or "")
        if not panel_stem:
            panel_stem = str(getattr(scene, "bname_current_panel_stem", "") or "")
        panel = _resolve_panel(work, page_id, panel_stem)
    if panel is None:
        return
    color = getattr(panel, "background_color", None)
    if color is None or len(color) < 3:
        return
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    world = scene.world
    settings = getattr(scene, "bname_panel_camera_settings", None)
    camera_only = bool(getattr(settings, "world_background_camera_only", False))
    rgba = (
        float(color[0]),
        float(color[1]),
        float(color[2]),
        1.0,
    )
    try:
        world.color = rgba[:3]
    except Exception:  # noqa: BLE001
        pass
    _configure_world_background_nodes(world, rgba, camera_only)


def _configure_world_background_nodes(world, rgba, camera_only: bool) -> None:
    try:
        world.use_nodes = True
    except Exception:  # noqa: BLE001
        return
    node_tree = getattr(world, "node_tree", None)
    if node_tree is None:
        return
    nodes = node_tree.nodes
    links = node_tree.links
    try:
        nodes.clear()
    except Exception:  # noqa: BLE001
        return
    out = nodes.new("ShaderNodeOutputWorld")
    out.location = (420, 0)
    if not camera_only:
        bg = nodes.new("ShaderNodeBackground")
        bg.location = (160, 0)
        bg.inputs["Color"].default_value = rgba
        bg.inputs["Strength"].default_value = 1.0
        links.new(bg.outputs["Background"], out.inputs["Surface"])
        return
    light_path = nodes.new("ShaderNodeLightPath")
    light_path.location = (-520, 0)
    bg_neutral = nodes.new("ShaderNodeBackground")
    bg_neutral.location = (-120, 120)
    bg_neutral.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    bg_neutral.inputs["Strength"].default_value = 0.0
    bg_camera = nodes.new("ShaderNodeBackground")
    bg_camera.location = (-120, -80)
    bg_camera.inputs["Color"].default_value = rgba
    bg_camera.inputs["Strength"].default_value = 1.0
    mix = nodes.new("ShaderNodeMixShader")
    mix.location = (160, 0)
    links.new(light_path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    links.new(bg_neutral.outputs["Background"], mix.inputs[1])
    links.new(bg_camera.outputs["Background"], mix.inputs[2])
    links.new(mix.outputs["Shader"], out.inputs["Surface"])


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
        try:
            img.reload()
        except Exception:  # noqa: BLE001
            pass
        img.name = label
        img[MANAGED_IMAGE_PROP] = True
        return img
    except Exception:  # noqa: BLE001
        _logger.warning("panel camera reference load failed: %s", path, exc_info=True)
        return None


def _background_scale_offset_for_ref(ref: ReferenceImage, base_scale: float) -> tuple[float, tuple[float, float]]:
    if ref.full_page_mask and ref.page_count >= 2 and ref.render_side in {"left", "right"}:
        return float(base_scale) * 2.0, (0.5 if ref.render_side == "left" else -0.5, 0.0)
    return float(base_scale), (0.0, 0.0)


def _background_scale_offset_for_image(img, base_scale: float) -> tuple[float, tuple[float, float]]:
    page_count = 1
    side = "full"
    try:
        if img is not None:
            page_count = int(img.get("bname_page_count", 1))
            side = str(img.get("bname_render_side", "full") or "full")
    except Exception:  # noqa: BLE001
        pass
    if page_count >= 2 and side in {"left", "right"}:
        return float(base_scale) * 2.0, (0.5 if side == "left" else -0.5, 0.0)
    return float(base_scale), (0.0, 0.0)


def set_background_images_opacity(context, opacity: float) -> None:
    for bg in _iter_camera_backgrounds(context):
        _set_bg_attr(bg, "alpha", opacity)


def set_background_images_scale(context, scale: float) -> None:
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        bg_scale, bg_offset = _background_scale_offset_for_image(img, float(scale))
        _set_bg_attr(bg, "scale", bg_scale)
        _set_bg_attr(bg, "offset", bg_offset)


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
            bg_scale, bg_offset = _background_scale_offset_for_image(img, float(scale))
            _set_bg_attr(bg, "scale", bg_scale)
            _set_bg_attr(bg, "offset", bg_offset)


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
        _disable_render_border(scene)
        return
    try:
        if bool(target.image.get("bname_full_page_mask", False)):
            _apply_page_side_render_border(scene, target.image)
            return
    except Exception:  # noqa: BLE001
        pass
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
    if hasattr(scene.render, "use_crop_to_border"):
        scene.render.use_crop_to_border = False
    scene.render.border_min_x = (1.0 - border_width) * 0.5
    scene.render.border_max_x = scene.render.border_min_x + border_width
    scene.render.border_min_y = (1.0 - border_height) * 0.5
    scene.render.border_max_y = scene.render.border_min_y + border_height


def _disable_render_border(scene) -> None:
    scene.render.use_border = False
    if hasattr(scene.render, "use_crop_to_border"):
        scene.render.use_crop_to_border = False


def _apply_page_side_render_border(scene, image) -> None:
    page_count = 1
    side = "full"
    try:
        page_count = int(image.get("bname_page_count", 1))
        side = str(image.get("bname_render_side", "full") or "full")
    except Exception:  # noqa: BLE001
        pass
    if page_count < 2 or side not in {"left", "right"}:
        _disable_render_border(scene)
        return
    _disable_render_border(scene)


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
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    if scene is None or get_mode(context) != MODE_PANEL:
        return
    for space in _iter_view3d_spaces(context):
        _configure_panel_camera_view(space, scene)


def schedule_panel_view_camera(retries: int = 8, interval: float = 0.15) -> None:
    """Re-apply camera view after Blender has rebuilt UI areas on file load."""
    state = {"left": max(1, int(retries))}

    def _tick():
        try:
            view_camera_in_viewports(bpy.context)
        except Exception:  # noqa: BLE001
            pass
        state["left"] -= 1
        return interval if state["left"] > 0 else None

    try:
        bpy.app.timers.register(_tick, first_interval=interval)
    except Exception:  # noqa: BLE001
        pass


def _iter_view3d_spaces(context):
    seen: set[int] = set()
    screens = []
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is not None:
        screens.append(screen)
    wm = getattr(bpy.context, "window_manager", None)
    if wm is not None:
        for window in getattr(wm, "windows", []):
            screen = getattr(window, "screen", None)
            if screen is not None:
                screens.append(screen)
    for screen in screens:
        sid = id(screen)
        if sid in seen:
            continue
        seen.add(sid)
        for area in getattr(screen, "areas", []):
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D" and getattr(space, "region_3d", None) is not None:
                    yield space


def _configure_panel_camera_view(space, scene=None) -> None:
    if scene is None:
        scene = bpy.context.scene
    camera = getattr(scene, "camera", None) if scene is not None else None
    if camera is not None:
        try:
            space.camera = camera
        except Exception:  # noqa: BLE001
            pass
    try:
        space.region_3d.view_perspective = "CAMERA"
    except Exception:  # noqa: BLE001
        pass
    try:
        space.lock_camera = True
    except Exception:  # noqa: BLE001
        pass
    shading = getattr(space, "shading", None)
    if shading is None:
        return
    try:
        shading.type = "SOLID"
    except Exception:  # noqa: BLE001
        pass
    try:
        shading.light = "STUDIO"
    except Exception:  # noqa: BLE001
        pass
    _apply_panel_solid_background(space, scene)


def _apply_panel_solid_background(space, scene) -> None:
    shading = getattr(space, "shading", None)
    settings = getattr(scene, "bname_panel_camera_settings", None) if scene is not None else None
    if shading is None or settings is None:
        return
    if bool(getattr(settings, "use_solid_background_color", False)):
        try:
            shading.background_type = "VIEWPORT"
            color = getattr(settings, "solid_background_color", (0.05, 0.05, 0.05))
            shading.background_color = (float(color[0]), float(color[1]), float(color[2]))
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            shading.background_type = "THEME"
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


def _page_ref_path(ref_dir: Path, page_id: str) -> Path:
    return ref_dir / f"{NAME_REF_PREFIX}_pageclean_{page_id}.png"


def _koma_ref_path(ref_dir: Path, page_id: str, panel_stem: str) -> Path:
    return ref_dir / f"{KOMA_REF_PREFIX}_{page_id}_{panel_stem}_page.png"


def _reference_frame_info(work, page_id: str, panel_stem: str = "") -> tuple[int, str, float, float]:
    paper = getattr(work, "paper", None) if work is not None else None
    page = find_page_by_id(work, page_id) if work is not None and page_id else None
    page_width = float(getattr(paper, "canvas_width_mm", 0.0) or 0.0)
    page_height = float(getattr(paper, "canvas_height_mm", 0.0) or 0.0)
    if page is None or page_width <= 0.0 or page_height <= 0.0:
        return 1, "full", page_width, page_height
    if bool(getattr(page, "spread", False)):
        panel = _resolve_panel(work, page_id, panel_stem)
        return 2, _spread_panel_side(panel, page_width), page_width, page_height
    mate = _find_spread_mate_page(work, page_id)
    if mate is None:
        return 1, "full", page_width, page_height
    side = "left" if _is_page_left_half(work, page_id) else "right"
    return 2, side, page_width, page_height


def _spread_panel_side(panel, page_width_mm: float) -> str:
    bbox = _panel_bbox(panel)
    if bbox is None or page_width_mm <= 0.0:
        return "full"
    center_x = (bbox[0] + bbox[2]) * 0.5
    return "left" if center_x < page_width_mm else "right"


def _ensure_page_reference(work, work_dir: Path, page, ref_dir: Path, include_work_blend_mtime: bool) -> Path | None:
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return None
    out = _page_ref_path(ref_dir, page_id)
    if _reference_is_stale(work_dir, page, out, include_work_blend=include_work_blend_mtime):
        _render_page_reference(work, page, out)
    return out if out.is_file() else None


def _find_spread_mate_page(work, current_page_id: str):
    pages = list(getattr(work, "pages", []) or [])
    current_index = next(
        (i for i, page in enumerate(pages) if str(getattr(page, "id", "") or "") == current_page_id),
        -1,
    )
    if current_index < 0:
        return None
    current_page = pages[current_index]
    if bool(getattr(current_page, "spread", False)):
        return None
    paper = getattr(work, "paper", None)
    start_side = str(getattr(paper, "start_side", "right") or "right")
    read_direction = str(getattr(paper, "read_direction", "left") or "left")
    if read_direction == "down":
        return None
    current_slot = page_grid._logical_slot_index(current_index, start_side, read_direction)
    mate_slot = current_slot - 1 if current_slot % 2 else current_slot + 1
    for index, page in enumerate(pages):
        if index == current_index or bool(getattr(page, "spread", False)):
            continue
        slot = page_grid._logical_slot_index(index, start_side, read_direction)
        if slot == mate_slot:
            return page
    return None


def _is_page_left_half(work, page_id: str) -> bool:
    pages = list(getattr(work, "pages", []) or [])
    page_index = next((i for i, page in enumerate(pages) if str(getattr(page, "id", "") or "") == page_id), 0)
    paper = getattr(work, "paper", None)
    start_side = str(getattr(paper, "start_side", "right") or "right")
    read_direction = str(getattr(paper, "read_direction", "left") or "left")
    return page_grid.is_left_half_page(page_index, start_side, read_direction)


def _render_page_reference(work, page, out: Path) -> bool:
    try:
        options = export_pipeline.ExportOptions(
            format="png",
            color_mode="rgb",
            area="canvas",
            dpi_override=DEFAULT_REF_DPI,
            include_tombo=False,
            include_paper_color=True,
            include_panel_previews=False,
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


def _render_current_panel_page_mask(work, page, panel, page_ref: Path | None, mate_page, mate_ref: Path | None, out: Path) -> bool:
    Image = export_pipeline.Image
    ImageDraw = export_pipeline.ImageDraw
    if Image is None or ImageDraw is None or page_ref is None:
        return False
    try:
        with Image.open(str(page_ref)) as opened:
            page_img = opened.convert("RGBA")
    except Exception:  # noqa: BLE001
        return False
    mate_img = None
    if mate_page is not None and mate_ref is not None and mate_ref.is_file():
        try:
            with Image.open(str(mate_ref)) as opened:
                mate_img = opened.convert("RGBA")
        except Exception:  # noqa: BLE001
            mate_img = None
    canvas, panel_offset_x = _compose_page_reference_pair(work, page, page_img, mate_img)
    points = _panel_points_px(panel, page_img.height, DEFAULT_REF_DPI, panel_offset_x)
    if len(points) < 3:
        return False
    try:
        alpha = canvas.getchannel("A")
        draw = ImageDraw.Draw(alpha)
        draw.polygon(points, fill=0)
        canvas.putalpha(alpha)
        out.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(str(out))
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("panel camera page mask render failed: %s", getattr(page, "id", ""))
        return False


def _compose_page_reference_pair(work, page, page_img, mate_img):
    Image = export_pipeline.Image
    if Image is None:
        return page_img.copy(), 0
    if mate_img is None:
        return page_img.copy(), 0
    page_is_left = _is_page_left_half(work, str(getattr(page, "id", "") or ""))
    width = page_img.width + mate_img.width
    height = max(page_img.height, mate_img.height)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if page_is_left:
        canvas.paste(page_img, (0, 0))
        canvas.paste(mate_img, (page_img.width, 0))
        return canvas, 0
    canvas.paste(mate_img, (0, 0))
    canvas.paste(page_img, (mate_img.width, 0))
    return canvas, mate_img.width


def _panel_points_px(panel, image_height: int, dpi: int, offset_x: int) -> list[tuple[int, int]]:
    points = _panel_points_mm(panel)
    out: list[tuple[int, int]] = []
    for x_mm, y_mm in points:
        x = offset_x + int(round(mm_to_px(x_mm, dpi)))
        y = image_height - int(round(mm_to_px(y_mm, dpi)))
        out.append((x, y))
    return out


def _panel_points_mm(panel) -> list[tuple[float, float]]:
    if panel is None:
        return []
    if getattr(panel, "shape_type", "") == "rect":
        x = float(getattr(panel, "rect_x_mm", 0.0))
        y = float(getattr(panel, "rect_y_mm", 0.0))
        w = float(getattr(panel, "rect_width_mm", 0.0))
        h = float(getattr(panel, "rect_height_mm", 0.0))
        if w <= 0.0 or h <= 0.0:
            return []
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return [(float(v.x_mm), float(v.y_mm)) for v in getattr(panel, "vertices", [])]


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


def _panel_mask_is_stale(page_refs: Iterable[Path | None], out: Path) -> bool:
    valid_refs = [Path(ref) for ref in page_refs if ref is not None and Path(ref).is_file()]
    if not valid_refs:
        return False
    if not out.is_file():
        return True
    out_mtime = _path_mtime(out)
    return any(out_mtime < _path_mtime(ref) for ref in valid_refs)


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
