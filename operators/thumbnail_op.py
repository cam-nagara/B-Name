"""コマのカメラサムネイル/高品質プレビュー生成 Operator.

計画書 3.4.3 / 8.8 参照。コマ編集モード終了時に panel_NNN_thumb.png を
カメラレンダーのコマ領域切り出しで更新。ユーザー手動で
panel_NNN_preview.png を高解像度レンダリング。
"""

from __future__ import annotations

from pathlib import Path
import math
import tempfile

import bpy
from bpy.types import Operator

from ..core.mode import MODE_PANEL, get_mode
from ..core.work import get_active_page, get_work
from ..utils import log, paths

_logger = log.get_logger(__name__)


def _is_panel_mode(context) -> bool:
    return get_mode(context) == MODE_PANEL


def take_area_screenshot(context, out_path: Path) -> bool:
    """選択コマのカメラレンダーをページ座標で切り出して保存する.

    旧実装は VIEW_3D スクリーンショットだったため、UIや下絵表示状態が
    そのまま紙面プレビューに混入した。コマファイルの下絵合わせと紙面表示を
    一致させるため、カメラのページ全体レンダーから対象コマbboxだけを切る。
    """
    return render_panel_camera_crop(context, out_path, resolution_percentage=25)


def render_panel_camera_crop(context, out_path: Path, *, resolution_percentage: int = 100) -> bool:
    if not _is_panel_mode(context):
        return False
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return False
    page, entry = _resolve_panel_entry(context, work)
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
    try:
        from ..utils import panel_camera

        panel_camera.ensure_panel_camera_scene(
            context,
            work=work,
            page_id=str(getattr(page, "id", "") or ""),
            panel_stem=str(getattr(entry, "panel_stem", "") or ""),
            generate_references=False,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("panel camera setup failed before preview render")
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg_state = None
    try:
        from ..io import export_pipeline
        from ..utils import panel_camera

        if not export_pipeline.has_pillow():
            return False
        bg_state = panel_camera.capture_managed_background_visibility(context)
        panel_camera.set_managed_background_visibility(context, False)
        with tempfile.TemporaryDirectory() as td:
            full_path = Path(td) / "panel_full.png"
            scene.render.filepath = str(full_path)
            scene.render.image_settings.file_format = "PNG"
            scene.render.resolution_percentage = max(1, min(100, int(resolution_percentage)))
            scene.render.film_transparent = False
            scene.render.use_border = False
            if hasattr(scene.render, "use_crop_to_border"):
                scene.render.use_crop_to_border = False
            with context.temp_override(scene=scene):
                bpy.ops.render.render(write_still=True)
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
                from ..utils import panel_camera

                panel_camera.restore_background_visibility(bg_state)
            except Exception:  # noqa: BLE001
                pass
        scene.render.filepath = prev_filepath
        scene.render.resolution_x = prev_res_x
        scene.render.resolution_y = prev_res_y
        scene.render.resolution_percentage = prev_percent
        scene.render.image_settings.file_format = prev_format
        scene.render.film_transparent = prev_film_transparent
        scene.render.use_border = prev_use_border
        if hasattr(scene.render, "use_crop_to_border"):
            scene.render.use_crop_to_border = prev_use_crop
        scene.render.border_min_x = prev_border[0]
        scene.render.border_max_x = prev_border[1]
        scene.render.border_min_y = prev_border[2]
        scene.render.border_max_y = prev_border[3]


def _resolve_panel_entry(context, work):
    scene = getattr(context, "scene", None)
    page_id = str(getattr(scene, "bname_current_panel_page_id", "") or "") if scene else ""
    stem = str(getattr(scene, "bname_current_panel_stem", "") or "") if scene else ""
    if page_id and stem:
        for page in getattr(work, "pages", []):
            if str(getattr(page, "id", "") or "") != page_id:
                continue
            for entry in getattr(page, "panels", []):
                if str(getattr(entry, "panel_stem", "") or "") == stem:
                    return page, entry
    page = get_active_page(context)
    if page is None:
        return None, None
    idx = int(getattr(page, "active_panel_index", -1))
    if not (0 <= idx < len(page.panels)):
        return None, None
    return page, page.panels[idx]


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
    bbox = _panel_bbox_on_camera_page(work, page, entry)
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
    image.crop((left, top, right, bottom)).save(str(out_path))
    return True


def _panel_bbox_on_camera_page(work, page, entry) -> tuple[float, float, float, float] | None:
    bbox = _panel_bbox(entry)
    if bbox is None:
        return None
    page_width = float(getattr(work.paper, "canvas_width_mm", 0.0) or 0.0)
    if bool(getattr(page, "spread", False)) and page_width > 0.0:
        center_x = (bbox[0] + bbox[2]) * 0.5
        if center_x >= page_width:
            return (bbox[0] - page_width, bbox[1], bbox[2] - page_width, bbox[3])
    return bbox


def _panel_bbox(entry) -> tuple[float, float, float, float] | None:
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


class BNAME_OT_panel_update_thumb(Operator):
    """選択中コマのカメラサムネを生成."""

    bl_idname = "bname.panel_update_thumb"
    bl_label = "コマサムネイルを更新"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return _is_panel_mode(context) and page is not None and 0 <= page.active_panel_index < len(page.panels)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        entry = page.panels[page.active_panel_index]
        paths.validate_panel_stem(entry.panel_stem)
        index = int(entry.panel_stem.split("_", 1)[1])
        out = paths.panel_thumb_path(Path(work.work_dir), page.id, index)
        if take_area_screenshot(context, out):
            self.report({"INFO"}, f"サムネイル保存: {out.name}")
            return {"FINISHED"}
        self.report({"WARNING"}, "サムネイル取得に失敗しました")
        return {"CANCELLED"}


class BNAME_OT_panel_generate_preview(Operator):
    """選択中コマをカメラレンダリングして高品質プレビューを生成."""

    bl_idname = "bname.panel_generate_preview"
    bl_label = "高品質プレビュー生成"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return _is_panel_mode(context) and page is not None and 0 <= page.active_panel_index < len(page.panels)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        entry = page.panels[page.active_panel_index]
        paths.validate_panel_stem(entry.panel_stem)
        index = int(entry.panel_stem.split("_", 1)[1])
        out = paths.panel_preview_path(Path(work.work_dir), page.id, index)
        out.parent.mkdir(parents=True, exist_ok=True)

        scene = context.scene
        prev_filepath = scene.render.filepath
        prev_percent = scene.render.resolution_percentage
        try:
            if not render_panel_camera_crop(context, out, resolution_percentage=100):
                raise RuntimeError("カメラプレビューの生成に失敗しました")
        except Exception as exc:  # noqa: BLE001
            _logger.exception("panel_generate_preview failed")
            self.report({"ERROR"}, f"プレビュー生成失敗: {exc}")
            return {"CANCELLED"}
        finally:
            scene.render.filepath = prev_filepath
            scene.render.resolution_percentage = prev_percent

        self.report({"INFO"}, f"プレビュー保存: {out.name}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_panel_update_thumb,
    BNAME_OT_panel_generate_preview,
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
