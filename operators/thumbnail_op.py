"""コマのソリッドカメラサムネイル/高品質プレビュー生成 Operator.

計画書 3.4.3 / 8.8 参照。コマ編集モード終了時に cNN_thumb.png を
カメラ基準のソリッド表示のコマ領域切り出しで更新。ユーザー手動で
cNN_preview.png を高解像度ソリッド画像として生成。
"""

from __future__ import annotations

from pathlib import Path
import math
import tempfile

import bpy
from bpy.types import Operator

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_active_page, get_work
from ..utils import image_transparency, log, paths

_logger = log.get_logger(__name__)


def _is_coma_mode(context) -> bool:
    return get_mode(context) == MODE_COMA


def take_area_screenshot(context, out_path: Path) -> bool:
    """選択コマのソリッドカメラ画像をページ座標で切り出して保存する.

    UI込みの VIEW_3D スクリーンショットではなく、カメラからページ全体の
    OpenGL/Workbench ソリッド画像を出し、対象コマbboxだけを切る。
    これによりビューポート操作状態に依存せず、紙面座標と一致する。
    """
    return render_coma_camera_crop(context, out_path, resolution_percentage=100)


def render_coma_camera_crop(context, out_path: Path, *, resolution_percentage: int = 100) -> bool:
    if not _is_coma_mode(context):
        return False
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return False
    page, entry = _resolve_coma_entry(context, work)
    if page is None or entry is None:
        return False
    if not getattr(work, "work_dir", ""):
        return False
    scene = getattr(context, "scene", None)
    if scene is None:
        return False
    prev_filepath = scene.render.filepath
    prev_res_x = int(scene.render.resolution_x)
    prev_res_y = int(scene.render.resolution_y)
    prev_percent = int(scene.render.resolution_percentage)
    prev_engine = scene.render.engine
    prev_format = scene.render.image_settings.file_format
    prev_film_transparent = bool(getattr(scene.render, "film_transparent", False))
    prev_use_border = bool(getattr(scene.render, "use_border", False))
    prev_use_crop = bool(getattr(scene.render, "use_crop_to_border", False))
    prev_border = (
        float(getattr(scene.render, "border_min_x", 0.0)),
        float(getattr(scene.render, "border_max_x", 1.0)),
        float(getattr(scene.render, "border_min_y", 0.0)),
        float(getattr(scene.render, "border_max_y", 1.0)),
    )
    shading = getattr(getattr(scene, "display", None), "shading", None)
    shading_state = _capture_attr_state(
        shading,
        (
            "type",
            "light",
            "color_type",
            "background_type",
            "background_color",
            "show_cavity",
            "show_shadows",
        ),
    )
    try:
        from ..utils import coma_camera

        coma_camera.ensure_coma_camera_scene(
            context,
            work=work,
            page_id=str(getattr(page, "id", "") or ""),
            coma_id=str(getattr(entry, "coma_id", "") or ""),
            generate_references=False,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("panel camera setup failed before preview render")
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg_state = None
    try:
        from ..io import export_pipeline
        from ..utils import coma_camera

        if not export_pipeline.has_pillow():
            return False
        bg_state = coma_camera.capture_managed_background_visibility(context)
        coma_camera.set_managed_background_visibility(context, False)
        with tempfile.TemporaryDirectory() as td:
            full_path = Path(td) / "coma_full.png"
            scene.render.filepath = str(full_path)
            scene.render.image_settings.file_format = "PNG"
            scene.render.resolution_percentage = max(1, min(100, int(resolution_percentage)))
            scene.render.film_transparent = image_transparency.coma_background_is_transparent(entry)
            scene.render.use_border = False
            if hasattr(scene.render, "use_crop_to_border"):
                scene.render.use_crop_to_border = False
            _configure_scene_solid_shading(scene)
            if not _write_solid_camera_image(context, scene):
                return False
            source = _resolve_render_output_path(full_path)
            if source is None:
                return False
            if not _crop_render_to_panel(source, out_path, work, page, entry):
                return False
            return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("panel camera crop render failed: %s", exc, exc_info=True)
        return False
    finally:
        if bg_state is not None:
            try:
                from ..utils import coma_camera

                coma_camera.restore_background_visibility(bg_state)
            except Exception:  # noqa: BLE001
                pass
        scene.render.filepath = prev_filepath
        scene.render.resolution_x = prev_res_x
        scene.render.resolution_y = prev_res_y
        scene.render.resolution_percentage = prev_percent
        scene.render.engine = prev_engine
        scene.render.image_settings.file_format = prev_format
        scene.render.film_transparent = prev_film_transparent
        scene.render.use_border = prev_use_border
        if hasattr(scene.render, "use_crop_to_border"):
            scene.render.use_crop_to_border = prev_use_crop
        scene.render.border_min_x = prev_border[0]
        scene.render.border_max_x = prev_border[1]
        scene.render.border_min_y = prev_border[2]
        scene.render.border_max_y = prev_border[3]
        _restore_attr_state(shading, shading_state)


def _write_solid_camera_image(context, scene) -> bool:
    """Write a camera-aligned solid-mode image to scene.render.filepath."""
    try:
        with context.temp_override(scene=scene):
            bpy.ops.render.opengl(write_still=True, view_context=False)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("OpenGL solid preview failed, falling back to Workbench render: %s", exc)
    try:
        scene.render.engine = "BLENDER_WORKBENCH"
        with context.temp_override(scene=scene):
            bpy.ops.render.render(write_still=True)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Workbench solid preview failed: %s", exc, exc_info=True)
        return False


def _configure_scene_solid_shading(scene) -> None:
    shading = getattr(getattr(scene, "display", None), "shading", None)
    if shading is None:
        return
    _set_attr_safe(shading, "type", "SOLID")
    _set_attr_safe(shading, "light", "STUDIO")
    _set_attr_safe(shading, "color_type", "MATERIAL")
    _set_attr_safe(shading, "show_cavity", False)
    _set_attr_safe(shading, "show_shadows", False)
    settings = getattr(scene, "bname_coma_camera_settings", None)
    if settings is not None and bool(getattr(settings, "use_solid_background_color", False)):
        _set_attr_safe(shading, "background_type", "VIEWPORT")
        color = getattr(settings, "solid_background_color", (0.05, 0.05, 0.05))
        _set_attr_safe(shading, "background_color", (float(color[0]), float(color[1]), float(color[2])))


def _capture_attr_state(obj, attrs: tuple[str, ...]) -> dict[str, object]:
    state: dict[str, object] = {}
    if obj is None:
        return state
    for attr in attrs:
        try:
            value = getattr(obj, attr)
            if attr == "background_color":
                value = tuple(value)
            state[attr] = value
        except Exception:  # noqa: BLE001
            pass
    return state


def _restore_attr_state(obj, state: dict[str, object]) -> None:
    if obj is None:
        return
    for attr, value in state.items():
        _set_attr_safe(obj, attr, value)


def _set_attr_safe(obj, attr: str, value) -> None:
    try:
        setattr(obj, attr, value)
    except Exception:  # noqa: BLE001
        pass


def _resolve_coma_entry(context, work):
    scene = getattr(context, "scene", None)
    page_id = str(getattr(scene, "bname_current_coma_page_id", "") or "") if scene else ""
    stem = str(getattr(scene, "bname_current_coma_id", "") or "") if scene else ""
    if page_id and stem:
        for page in getattr(work, "pages", []):
            if str(getattr(page, "id", "") or "") != page_id:
                continue
            for entry in getattr(page, "comas", []):
                if str(getattr(entry, "coma_id", "") or "") == stem:
                    return page, entry
    page = get_active_page(context)
    if page is None:
        return None, None
    idx = int(getattr(page, "active_coma_index", -1))
    if not (0 <= idx < len(page.comas)):
        return None, None
    return page, page.comas[idx]


def _resolve_render_output_path(path: Path) -> Path | None:
    if path.is_file():
        return path
    matches = sorted(path.parent.glob(f"{path.stem}*.png"))
    return matches[-1] if matches else None


def _crop_render_to_panel(source: Path, out_path: Path, work, page, entry) -> bool:
    from ..io import export_pipeline

    Image = export_pipeline.Image
    if Image is None:
        return False
    bbox = _coma_bbox_on_camera_page(work, page, entry)
    if bbox is None:
        return False
    try:
        with Image.open(str(source)) as opened:
            image = opened.convert("RGBA")
    except Exception:  # noqa: BLE001
        return False
    page_width = max(0.001, float(getattr(work.paper, "canvas_width_mm", 0.0) or 0.0))
    page_height = max(0.001, float(getattr(work.paper, "canvas_height_mm", 0.0) or 0.0))
    min_x, min_y, max_x, max_y = bbox
    px_per_mm_x = image.width / page_width
    px_per_mm_y = image.height / page_height
    left = int(math.floor(min_x * px_per_mm_x))
    right = int(math.ceil(max_x * px_per_mm_x))
    top = image.height - int(math.ceil(max_y * px_per_mm_y))
    bottom = image.height - int(math.floor(min_y * px_per_mm_y))
    left = max(0, min(image.width - 1, left))
    right = max(left + 1, min(image.width, right))
    top = max(0, min(image.height - 1, top))
    bottom = max(top + 1, min(image.height, bottom))
    cropped = image.crop((left, top, right, bottom))
    if image_transparency.coma_background_is_transparent(entry):
        cropped = image_transparency.make_background_transparent(cropped)
    cropped.save(str(out_path))
    return True


def _coma_bbox_on_camera_page(work, page, entry) -> tuple[float, float, float, float] | None:
    bbox = _coma_bbox(entry)
    if bbox is None:
        return None
    page_width = float(getattr(work.paper, "canvas_width_mm", 0.0) or 0.0)
    if bool(getattr(page, "spread", False)) and page_width > 0.0:
        center_x = (bbox[0] + bbox[2]) * 0.5
        if center_x >= page_width:
            return (bbox[0] - page_width, bbox[1], bbox[2] - page_width, bbox[3])
    return bbox


def _coma_bbox(entry) -> tuple[float, float, float, float] | None:
    if getattr(entry, "shape_type", "") == "rect":
        x = float(getattr(entry, "rect_x_mm", 0.0))
        y = float(getattr(entry, "rect_y_mm", 0.0))
        w = float(getattr(entry, "rect_width_mm", 0.0))
        h = float(getattr(entry, "rect_height_mm", 0.0))
        if w <= 0.0 or h <= 0.0:
            return None
        return x, y, x + w, y + h
    verts = [(float(v.x_mm), float(v.y_mm)) for v in getattr(entry, "vertices", [])]
    if not verts:
        return None
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    return min(xs), min(ys), max(xs), max(ys)


class BNAME_OT_coma_update_thumb(Operator):
    """選択中コマのソリッドカメラサムネを生成."""

    bl_idname = "bname.coma_update_thumb"
    bl_label = "コマサムネイルを更新"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return _is_coma_mode(context) and page is not None and 0 <= page.active_coma_index < len(page.comas)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        entry = page.comas[page.active_coma_index]
        paths.validate_coma_id(entry.coma_id)
        out = paths.coma_thumb_path(Path(work.work_dir), page.id, entry.coma_id)
        if take_area_screenshot(context, out):
            self.report({"INFO"}, f"サムネイル保存: {out.name}")
            return {"FINISHED"}
        self.report({"WARNING"}, "サムネイル取得に失敗しました")
        return {"CANCELLED"}


class BNAME_OT_coma_generate_preview(Operator):
    """選択中コマのソリッドカメラ画像から高品質プレビューを生成."""

    bl_idname = "bname.coma_generate_preview"
    bl_label = "高品質プレビュー生成"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return _is_coma_mode(context) and page is not None and 0 <= page.active_coma_index < len(page.comas)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        entry = page.comas[page.active_coma_index]
        paths.validate_coma_id(entry.coma_id)
        out = paths.coma_preview_path(Path(work.work_dir), page.id, entry.coma_id)
        out.parent.mkdir(parents=True, exist_ok=True)

        scene = context.scene
        prev_filepath = scene.render.filepath
        prev_percent = scene.render.resolution_percentage
        try:
            if not render_coma_camera_crop(context, out, resolution_percentage=100):
                raise RuntimeError("カメラプレビューの生成に失敗しました")
        except Exception as exc:  # noqa: BLE001
            _logger.exception("coma_generate_preview failed")
            self.report({"ERROR"}, f"プレビュー生成失敗: {exc}")
            return {"CANCELLED"}
        finally:
            scene.render.filepath = prev_filepath
            scene.render.resolution_percentage = prev_percent

        self.report({"INFO"}, f"プレビュー保存: {out.name}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_coma_update_thumb,
    BNAME_OT_coma_generate_preview,
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
