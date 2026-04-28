"""書き出し Operator (Phase 6a).

- 単一ページ書き出し (PNG/JPEG/TIFF)
- 複数ページ一括書き出し

Pillow が同梱されていないと動作しない。ポップアップで通知する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.mode import MODE_PAGE, get_mode
from ..core.work import get_active_page, get_work
from ..io import export_page_regions, export_pipeline
from ..io.export_pipeline import ExportOptions
from ..utils import log, page_range, paths


def _save_image(img, out_path: Path, image_format: str) -> None:
    """Pillow Image を format 別の互換モードで保存."""
    if image_format == "jpeg":
        # JPEG は RGB / L / CMYK のみサポート。RGBA / "1" は RGB に変換
        if img.mode == "RGBA":
            bg = export_pipeline.Image.new("RGBA", img.size, (255, 255, 255, 255))
            bg.alpha_composite(img)
            img = bg.convert("RGB")
        elif img.mode not in ("RGB", "L", "CMYK"):
            img = img.convert("RGB")
        img.save(str(out_path), quality=95)
    elif image_format == "tiff":
        img.save(str(out_path))
    elif image_format == "psd":
        ok = export_pipeline.save_as_psd(img, out_path)
        if not ok:
            raise RuntimeError("PSD 保存に失敗しました")
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
    ("psd", "PSD", ""),
)

_FLAT_FORMAT_ITEMS = (
    ("png", "PNG", ""),
    ("jpeg", "JPEG", ""),
)

_OUTPUT_MODE_ITEMS = (
    ("flat", "統合画像", ""),
    ("layered", "レイヤー構成を維持", ""),
)

_AREA_ITEMS = (
    ("finish", "仕上がり枠", ""),
    ("withBleed", "裁ち落とし込み", ""),
    ("innerFrame", "基本枠", ""),
    ("canvas", "キャンバス全体", ""),
)

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _resolve_filename(
    template: str,
    work,
    page,
    index: int,
    *,
    page_end: int | None = None,
    side: str = "",
) -> str:
    info = work.work_info
    return (
        template
        .replace("{workName}", info.work_name or "work")
        .replace("{episode}", f"{info.episode_number:02d}")
        .replace("{page}", f"{index:04d}")
        .replace("{pageEnd}", f"{(page_end if page_end is not None else index):04d}")
        .replace("{side}", side)
        .replace("{pageId}", page.id if page else "")
    )


def _safe_filename(name: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("_", str(name or "").strip())
    cleaned = cleaned.rstrip(" .")
    return cleaned or "page"


def _unit_filename(template: str, work, unit: "_ExportUnit") -> str:
    name = _resolve_filename(
        template,
        work,
        unit.page,
        unit.page_number,
        page_end=unit.page_end,
        side=unit.side_suffix,
    )
    if unit.side_suffix and "{side}" not in template:
        name = f"{name}_{unit.side_suffix}"
    if unit.page_end is not None and "{pageEnd}" not in template:
        name = f"{name}-{unit.page_end:04d}"
    return _safe_filename(name)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10000):
        candidate = path.with_name(f"{path.stem}_{index:03d}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"出力ファイル名が重複しています: {path.name}")


def _default_export_dir(work) -> Path:
    work_dir = Path(work.work_dir)
    return paths.exports_dir(work_dir) / datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _resolve_output_dir(path_text: str, work) -> Path:
    text = str(path_text or "").strip()
    if not text:
        return _default_export_dir(work)
    try:
        text = bpy.path.abspath(text)
    except Exception:  # noqa: BLE001
        pass
    return Path(text)


@dataclass(frozen=True)
class _ExportUnit:
    page_index: int
    page: object
    page_number: int
    page_end: int | None = None
    spread_side: str | None = None
    side_suffix: str = ""


def _selected_export_units(
    work,
    start_number: int,
    end_number: int,
    *,
    split_spreads: bool,
) -> list[_ExportUnit]:
    units: list[_ExportUnit] = []
    current_number = int(getattr(work.work_info, "page_number_start", 1))
    for page_index, page in page_range.iter_in_range_pages(work):
        is_spread = bool(getattr(page, "spread", False))
        numbers = [current_number, current_number + 1] if is_spread else [current_number]
        current_number += len(numbers)
        selected = [number for number in numbers if start_number <= number <= end_number]
        if not selected:
            continue
        if split_spreads and is_spread:
            side_units = (
                ("right", "R", numbers[0]),
                ("left", "L", numbers[1]),
            )
            for side, suffix, number in side_units:
                if start_number <= number <= end_number:
                    units.append(
                        _ExportUnit(
                            page_index=page_index,
                            page=page,
                            page_number=number,
                            spread_side=side,
                            side_suffix=suffix,
                        )
                    )
            continue
        units.append(
            _ExportUnit(
                page_index=page_index,
                page=page,
                page_number=selected[0],
                page_end=max(selected) if len(selected) > 1 else None,
            )
        )
    return units


def _scaled_dpi(work, scale_percent: int) -> int:
    base = int(getattr(work.paper, "dpi", 600))
    scale = max(1, int(scale_percent)) / 100.0
    return max(1, int(round(base * scale)))


def _export_all_page_unit(
    operator,
    work,
    unit: _ExportUnit,
    out_dir: Path,
    output_format: str,
    options: ExportOptions,
) -> bool:
    name = _unit_filename(operator.filename_template, work, unit)
    ext = output_format.replace("jpeg", "jpg")
    out = _unique_path(out_dir / f"{name}.{ext}")
    if operator.output_mode == "layered":
        export_page_regions.save_page_region_as_psd(
            work,
            unit.page,
            options,
            out,
            spread_side=unit.spread_side,
        )
        return True
    img = export_page_regions.render_page_region(
        work,
        unit.page,
        options,
        spread_side=unit.spread_side,
    )
    if img is None:
        return False
    _save_image(img, out, output_format)
    return True


def _export_all_page_units(
    operator,
    work,
    out_dir: Path,
    output_format: str,
    options: ExportOptions,
    export_units: list[_ExportUnit],
) -> tuple[int, list[str]]:
    success = 0
    errors: list[str] = []
    for unit in export_units:
        try:
            if _export_all_page_unit(operator, work, unit, out_dir, output_format, options):
                success += 1
            else:
                errors.append(f"{getattr(unit.page, 'id', '?')}: 描画失敗")
        except Exception as exc:  # noqa: BLE001
            _logger.exception("export failed for %s", getattr(unit.page, "id", "?"))
            errors.append(f"{getattr(unit.page, 'id', '?')}: {exc}")
    return success, errors


def _export_all_output_settings(operator, work) -> tuple[str, ExportOptions]:
    output_format = "psd" if operator.output_mode == "layered" else operator.flat_format
    dpi_override = (
        _scaled_dpi(work, operator.flat_scale_percent)
        if operator.output_mode == "flat"
        else 0
    )
    return output_format, ExportOptions(
        color_mode=operator.color_mode,
        format=output_format,
        area=operator.area,
        dpi_override=dpi_override,
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
        if self.format == "psd" and not export_pipeline.can_write_layered_psd():
            self.report({"ERROR"}, "PSD レイヤー出力を利用できません")
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
            work_dir = Path(work.work_dir)
            out_dir = paths.exports_dir(work_dir) / datetime.now().strftime("%Y-%m-%d_%H%M%S")
            out_dir.mkdir(parents=True, exist_ok=True)
            idx = int(page.id.split("-", 1)[0]) if page.id else 1
            name = _resolve_filename("{workName}_{episode}_{page}", work, page, idx)
            ext = self.format.replace("jpeg", "jpg")
            out = out_dir / f"{name}.{ext}"
            if self.format == "psd":
                export_pipeline.save_page_as_psd(work, page, options, out)
            else:
                img = export_pipeline.render_page(work, page, options)
                if img is None:
                    return {"CANCELLED"}
                _save_image(img, out, self.format)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("export_page failed")
            self.report({"ERROR"}, f"書き出し失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"書き出し完了: {out}")
        return {"FINISHED"}


class BNAME_OT_export_all_pages(Operator):
    """全ページを一括書き出し."""

    bl_idname = "bname.export_all_pages"
    bl_label = "全ページを一括書き出し"
    bl_options = {"REGISTER"}

    filepath: StringProperty(name="保存先", subtype="DIR_PATH", default="")  # type: ignore[valid-type]
    output_start: IntProperty(name="開始ページ", default=1, min=0, soft_max=9999)  # type: ignore[valid-type]
    output_end: IntProperty(name="終了ページ", default=1, min=0, soft_max=9999)  # type: ignore[valid-type]
    split_spreads: BoolProperty(name="見開きを分ける", default=False)  # type: ignore[valid-type]
    output_mode: EnumProperty(name="出力", items=_OUTPUT_MODE_ITEMS, default="flat")  # type: ignore[valid-type]
    flat_format: EnumProperty(name="統合画像形式", items=_FLAT_FORMAT_ITEMS, default="png")  # type: ignore[valid-type]
    flat_scale_percent: IntProperty(  # type: ignore[valid-type]
        name="統合画像倍率",
        default=25,
        min=1,
        soft_max=400,
        subtype="PERCENTAGE",
    )
    color_mode: EnumProperty(name="カラーモード", items=_COLOR_MODE_ITEMS, default="rgb")  # type: ignore[valid-type]
    area: EnumProperty(name="範囲", items=_AREA_ITEMS, default="finish")  # type: ignore[valid-type]
    filename_template: StringProperty(  # type: ignore[valid-type]
        name="ファイル名",
        default="{workName}_{episode}_{page}",
    )

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return (
            w is not None
            and w.loaded
            and len(w.pages) > 0
            and get_mode(context) == MODE_PAGE
            and export_pipeline.has_pillow()
        )

    def invoke(self, context, event):
        if not export_pipeline.has_pillow():
            self.report({"ERROR"}, "Pillow が同梱されていません")
            return {"CANCELLED"}
        work = get_work(context)
        if work is not None:
            self.filepath = str(_default_export_dir(work))
            self.output_start = int(getattr(work.work_info, "page_number_start", 1))
            self.output_end = int(getattr(work.work_info, "page_number_end", self.output_start))
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "filepath")
        row = layout.row(align=True)
        row.prop(self, "output_start")
        row.prop(self, "output_end")
        layout.prop(self, "split_spreads")
        layout.prop(self, "output_mode", expand=True)
        if self.output_mode == "flat":
            box = layout.box()
            box.prop(self, "flat_format")
            box.prop(self, "flat_scale_percent")
        else:
            if not export_pipeline.can_write_layered_psd():
                layout.label(text="PSD レイヤー出力を利用できません", icon="ERROR")
        layout.prop(self, "color_mode")
        layout.prop(self, "area")
        layout.prop(self, "filename_template")

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        if self.output_start > self.output_end:
            self.report({"ERROR"}, "ページ範囲の開始が終了より後になっています")
            return {"CANCELLED"}
        if self.output_mode == "layered" and not export_pipeline.can_write_layered_psd():
            self.report({"ERROR"}, "PSD レイヤー出力を利用できません")
            return {"CANCELLED"}
        if self.output_mode == "layered" and self.color_mode == "cmyk":
            self.report({"ERROR"}, "PSD レイヤー出力での CMYK は未対応です")
            return {"CANCELLED"}
        out_dir = _resolve_output_dir(self.filepath, work)
        if out_dir.exists() and not out_dir.is_dir():
            self.report({"ERROR"}, f"保存先がフォルダではありません: {out_dir}")
            return {"CANCELLED"}
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.report({"ERROR"}, f"保存先を作成できません: {exc}")
            return {"CANCELLED"}
        output_format, options = _export_all_output_settings(self, work)
        export_units = _selected_export_units(
            work,
            int(self.output_start),
            int(self.output_end),
            split_spreads=bool(self.split_spreads),
        )
        if not export_units:
            self.report({"ERROR"}, "書き出せるページがありません")
            return {"CANCELLED"}
        success, errors = _export_all_page_units(
            self,
            work,
            out_dir,
            output_format,
            options,
            export_units,
        )
        msg = f"書き出し完了: {success}/{len(export_units)} ページ"
        if errors:
            msg += f" (エラー {len(errors)} 件)"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class BNAME_OT_export_pdf(Operator):
    """全ページを 1 つの PDF に結合書き出し (Phase 6b)."""

    bl_idname = "bname.export_pdf"
    bl_label = "PDF 結合書き出し"
    bl_options = {"REGISTER"}

    area: EnumProperty(name="範囲", items=_AREA_ITEMS, default="finish")  # type: ignore[valid-type]
    color_mode: EnumProperty(name="カラーモード", items=_COLOR_MODE_ITEMS, default="rgb")  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return (
            w is not None
            and w.loaded
            and len(w.pages) > 0
            and export_pipeline.has_pillow()
        )

    def invoke(self, context, event):
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
            format="png",  # 中間画像は PNG、最終 PDF へ結合
            area=self.area,
        )
        export_pages = list(page_range.iter_in_range_pages(work))
        tmp_images: list[Path] = []
        for _page_index, page in export_pages:
            i = int(getattr(work.work_info, "page_number_start", 1)) + _page_index
            try:
                img = export_pipeline.render_page(work, page, options)
                if img is None:
                    continue
                name = _resolve_filename("{workName}_{episode}_{page}", work, page, i)
                tmp = out_dir / f"_tmp_{name}.png"
                _save_image(img, tmp, "png")
                tmp_images.append(tmp)
            except Exception:  # noqa: BLE001
                _logger.exception("pdf intermediate render failed for %s", page.id)
        if not tmp_images:
            self.report({"ERROR"}, "書き出せるページがありません")
            return {"CANCELLED"}
        pdf_path = out_dir / f"{work.work_info.work_name or 'work'}.pdf"
        ok = export_pipeline.merge_pdf(tmp_images, pdf_path)
        # 中間 PNG を削除
        for p in tmp_images:
            try:
                p.unlink()
            except OSError:
                pass
        if not ok:
            self.report({"ERROR"}, "PDF 結合に失敗しました")
            return {"CANCELLED"}
        self.report({"INFO"}, f"PDF 書き出し: {pdf_path.name}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_export_page,
    BNAME_OT_export_all_pages,
    BNAME_OT_export_pdf,
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
