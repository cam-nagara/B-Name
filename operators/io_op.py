"""書き出し Operator (Phase 6a).

- 単一ページ書き出し (PNG/JPEG/TIFF)
- 複数ページ一括書き出し

Pillow が同梱されていないと動作しない。ポップアップで通知する。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..io import export_pipeline
from ..io.export_pipeline import ExportOptions
from ..utils import log, paths


def _save_image(img, out_path: Path, image_format: str) -> None:
    """Pillow Image を format 別の互換モードで保存."""
    if image_format == "jpeg":
        # JPEG は RGB / L / CMYK のみサポート。RGBA / "1" は RGB に変換
        if img.mode not in ("RGB", "L", "CMYK"):
            img = img.convert("RGB")
        img.save(str(out_path), quality=95)
    elif image_format == "tiff":
        img.save(str(out_path))
    else:
        img.save(str(out_path))

_logger = log.get_logger(__name__)

_COLOR_MODE_ITEMS = (
    ("rgb", "RGB", ""),
    ("grayscale", "グレースケール", ""),
    ("monochrome", "モノクロ", ""),
    ("cmyk", "CMYK", ""),
)

_FORMAT_ITEMS = (
    ("png", "PNG", ""),
    ("jpeg", "JPEG", ""),
    ("tiff", "TIFF", ""),
)

_AREA_ITEMS = (
    ("finish", "仕上がり枠", ""),
    ("withBleed", "裁ち落とし込み", ""),
    ("innerFrame", "基本枠", ""),
    ("canvas", "キャンバス全体", ""),
)


def _resolve_filename(template: str, work, page, index: int) -> str:
    info = work.work_info
    return (
        template
        .replace("{workName}", info.work_name or "work")
        .replace("{episode}", f"{info.episode_number:02d}")
        .replace("{page}", f"{index:04d}")
        .replace("{pageId}", page.id if page else "")
    )


class BNAME_OT_export_page(Operator):
    """現在のページを画像書き出し."""

    bl_idname = "bname.export_page"
    bl_label = "現在のページを書き出し"
    bl_options = {"REGISTER"}

    format: EnumProperty(name="形式", items=_FORMAT_ITEMS, default="png")  # type: ignore[valid-type]
    color_mode: EnumProperty(name="カラーモード", items=_COLOR_MODE_ITEMS, default="rgb")  # type: ignore[valid-type]
    area: EnumProperty(name="範囲", items=_AREA_ITEMS, default="finish")  # type: ignore[valid-type]
    dpi_override: IntProperty(name="DPI 上書き (0 で既定)", default=0, min=0, soft_max=1200)  # type: ignore[valid-type]
    include_border: BoolProperty(name="コマ枠線", default=True)  # type: ignore[valid-type]
    include_white_margin: BoolProperty(name="白フチ", default=True)  # type: ignore[valid-type]
    include_nombre: BoolProperty(name="ノンブル", default=True)  # type: ignore[valid-type]
    include_paper_color: BoolProperty(name="用紙色", default=True)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        page = get_active_page(context)
        return w is not None and w.loaded and page is not None and export_pipeline.has_pillow()

    def invoke(self, context, event):
        if not export_pipeline.has_pillow():
            self.report({"ERROR"}, "Pillow が同梱されていません (Phase 6 の wheels 同梱後に利用可能)")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        options = ExportOptions(
            color_mode=self.color_mode,
            format=self.format,
            area=self.area,
            dpi_override=self.dpi_override,
            include_border=self.include_border,
            include_white_margin=self.include_white_margin,
            include_nombre=self.include_nombre,
            include_paper_color=self.include_paper_color,
        )
        try:
            img = export_pipeline.render_page(work, page, options)
            if img is None:
                return {"CANCELLED"}
            work_dir = Path(work.work_dir)
            out_dir = paths.exports_dir(work_dir) / datetime.now().strftime("%Y-%m-%d_%H%M%S")
            out_dir.mkdir(parents=True, exist_ok=True)
            idx = int(page.id.split("-", 1)[0]) if page.id else 1
            name = _resolve_filename("{workName}_{episode}_{page}", work, page, idx)
            ext = self.format.replace("jpeg", "jpg")
            out = out_dir / f"{name}.{ext}"
            _save_image(img, out, self.format)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("export_page failed")
            self.report({"ERROR"}, f"書き出し失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"書き出し完了: {out}")
        return {"FINISHED"}


class BNAME_OT_export_all_pages(Operator):
    """全ページを一括書き出し (Phase 6a、進捗は簡易)."""

    bl_idname = "bname.export_all_pages"
    bl_label = "全ページを一括書き出し"
    bl_options = {"REGISTER"}

    format: EnumProperty(name="形式", items=_FORMAT_ITEMS, default="png")  # type: ignore[valid-type]
    color_mode: EnumProperty(name="カラーモード", items=_COLOR_MODE_ITEMS, default="rgb")  # type: ignore[valid-type]
    area: EnumProperty(name="範囲", items=_AREA_ITEMS, default="finish")  # type: ignore[valid-type]
    filename_template: StringProperty(  # type: ignore[valid-type]
        name="ファイル名",
        default="{workName}_{episode}_{page}",
    )

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return w is not None and w.loaded and len(w.pages) > 0 and export_pipeline.has_pillow()

    def invoke(self, context, event):
        if not export_pipeline.has_pillow():
            self.report({"ERROR"}, "Pillow が同梱されていません")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        out_dir = paths.exports_dir(work_dir) / datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        options = ExportOptions(
            color_mode=self.color_mode,
            format=self.format,
            area=self.area,
        )
        success = 0
        errors: list[str] = []
        for i, page in enumerate(work.pages, start=1):
            try:
                img = export_pipeline.render_page(work, page, options)
                if img is None:
                    errors.append(f"{page.id}: 描画失敗")
                    continue
                name = _resolve_filename(self.filename_template, work, page, i)
                ext = self.format.replace("jpeg", "jpg")
                out = out_dir / f"{name}.{ext}"
                _save_image(img, out, self.format)
                success += 1
            except Exception as exc:  # noqa: BLE001
                _logger.exception("export failed for %s", page.id)
                errors.append(f"{page.id}: {exc}")
        msg = f"書き出し完了: {success}/{len(work.pages)} ページ"
        if errors:
            msg += f" (エラー {len(errors)} 件)"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_export_page,
    BNAME_OT_export_all_pages,
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
