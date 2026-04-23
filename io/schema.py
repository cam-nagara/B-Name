"""JSON スキーマの定義・バージョン・シリアライザ.

work.json / pages.json / page.json / panel_NNN.json の構造を 1 箇所に
集約する。将来のフォーマット変更に備えて ``schemaVersion`` フィールドを
各 JSON のトップレベルに付与する。

PropertyGroup ↔ dict の変換は to_dict / from_dict で行い、dict は
utils.json_io で書き出す。
"""

from __future__ import annotations

from typing import Any

# ファイルフォーマットのバージョン (破壊的変更があったら繰り上げる)
WORK_SCHEMA_VERSION = 1
PAGES_SCHEMA_VERSION = 1
PAGE_SCHEMA_VERSION = 1

# ---------- 共通変換 ----------


def color_to_hex(rgba: tuple[float, float, float, float]) -> str:
    """(r,g,b,a) 浮動小数 → "#RRGGBB" (alpha は別管理)."""
    r, g, b = rgba[0], rgba[1], rgba[2]
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
    )


def hex_to_rgba(code: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """"#RRGGBB" または "#RRGGBBAA" → (r,g,b,a) 浮動小数."""
    code = code.strip()
    if code.startswith("#"):
        code = code[1:]
    if len(code) == 6:
        r = int(code[0:2], 16) / 255.0
        g = int(code[2:4], 16) / 255.0
        b = int(code[4:6], 16) / 255.0
        return (r, g, b, alpha)
    if len(code) == 8:
        r = int(code[0:2], 16) / 255.0
        g = int(code[2:4], 16) / 255.0
        b = int(code[4:6], 16) / 255.0
        a = int(code[6:8], 16) / 255.0
        return (r, g, b, a)
    raise ValueError(f"invalid color hex: {code}")


# ---------- PaperSettings ----------


def paper_to_dict(paper) -> dict[str, Any]:
    return {
        "canvasWidthMm": round(paper.canvas_width_mm, 3),
        "canvasHeightMm": round(paper.canvas_height_mm, 3),
        "dpi": paper.dpi,
        "unit": paper.unit,
        "finishWidthMm": round(paper.finish_width_mm, 3),
        "finishHeightMm": round(paper.finish_height_mm, 3),
        "bleedMm": round(paper.bleed_mm, 3),
        "innerFrameWidthMm": round(paper.inner_frame_width_mm, 3),
        "innerFrameHeightMm": round(paper.inner_frame_height_mm, 3),
        "innerFrameOffsetXMm": round(paper.inner_frame_offset_x_mm, 3),
        "innerFrameOffsetYMm": round(paper.inner_frame_offset_y_mm, 3),
        "safeTopMm": round(paper.safe_top_mm, 3),
        "safeBottomMm": round(paper.safe_bottom_mm, 3),
        "safeGutterMm": round(paper.safe_gutter_mm, 3),
        "safeForeEdgeMm": round(paper.safe_fore_edge_mm, 3),
        "colorMode": paper.color_mode,
        "defaultLineCount": round(paper.default_line_count, 2),
        "paperColor": color_to_hex(paper.paper_color),
        "paperColorAlpha": round(paper.paper_color[3], 3),
        "colorProfile": paper.color_profile,
        "isSpreadLayout": bool(paper.is_spread_layout),
        "presetName": paper.preset_name,
    }


def paper_from_dict(paper, data: dict[str, Any]) -> None:
    data = data or {}
    paper.canvas_width_mm = float(data.get("canvasWidthMm", 257.00))
    paper.canvas_height_mm = float(data.get("canvasHeightMm", 364.00))
    paper.dpi = int(data.get("dpi", 600))
    paper.unit = data.get("unit", "mm")
    paper.finish_width_mm = float(data.get("finishWidthMm", 221.81))
    paper.finish_height_mm = float(data.get("finishHeightMm", 328.78))
    paper.bleed_mm = float(data.get("bleedMm", 7.00))
    paper.inner_frame_width_mm = float(data.get("innerFrameWidthMm", 180.00))
    paper.inner_frame_height_mm = float(data.get("innerFrameHeightMm", 270.00))
    paper.inner_frame_offset_x_mm = float(data.get("innerFrameOffsetXMm", 0.0))
    paper.inner_frame_offset_y_mm = float(data.get("innerFrameOffsetYMm", 0.0))
    paper.safe_top_mm = float(data.get("safeTopMm", 17.49))
    paper.safe_bottom_mm = float(data.get("safeBottomMm", 17.49))
    paper.safe_gutter_mm = float(data.get("safeGutterMm", 20.90))
    paper.safe_fore_edge_mm = float(data.get("safeForeEdgeMm", 17.23))
    paper.color_mode = data.get("colorMode", "monochrome")
    paper.default_line_count = float(data.get("defaultLineCount", 60.0))
    hex_code = data.get("paperColor", "#FFFFFF")
    alpha = float(data.get("paperColorAlpha", 1.0))
    paper.paper_color = hex_to_rgba(hex_code, alpha)
    paper.color_profile = data.get("colorProfile", "sRGB IEC61966-2.1")
    paper.is_spread_layout = bool(data.get("isSpreadLayout", False))
    paper.preset_name = data.get("presetName", "集英社マンガ誌汎用")


# ---------- WorkInfo / DisplayItem / Nombre ----------


def display_item_to_dict(item) -> dict[str, Any]:
    return {
        "enabled": bool(item.enabled),
        "position": item.position,
        "fontSizePt": round(item.font_size_pt, 2),
        "color": color_to_hex(item.color),
    }


def display_item_from_dict(item, data: dict[str, Any]) -> None:
    data = data or {}
    item.enabled = bool(data.get("enabled", False))
    item.position = data.get("position", "bottom-left")
    item.font_size_pt = float(data.get("fontSizePt", 9.0))
    item.color = hex_to_rgba(data.get("color", "#000000"))


def work_info_to_dict(info) -> dict[str, Any]:
    return {
        "workName": info.work_name,
        "episodeNumber": int(info.episode_number),
        "subtitle": info.subtitle,
        "author": info.author,
        "displayOnCanvas": {
            "workName": display_item_to_dict(info.display_work_name),
            "episode": display_item_to_dict(info.display_episode),
            "subtitle": display_item_to_dict(info.display_subtitle),
            "author": display_item_to_dict(info.display_author),
        },
    }


def work_info_from_dict(info, data: dict[str, Any]) -> None:
    data = data or {}
    info.work_name = data.get("workName", "")
    info.episode_number = int(data.get("episodeNumber", 1))
    info.subtitle = data.get("subtitle", "")
    info.author = data.get("author", "")
    disp = data.get("displayOnCanvas", {})
    display_item_from_dict(info.display_work_name, disp.get("workName", {}))
    display_item_from_dict(info.display_episode, disp.get("episode", {}))
    display_item_from_dict(info.display_subtitle, disp.get("subtitle", {}))
    display_item_from_dict(info.display_author, disp.get("author", {}))


def nombre_to_dict(n) -> dict[str, Any]:
    return {
        "enabled": bool(n.enabled),
        "format": n.format,
        "font": n.font,
        "fontSizePt": round(n.font_size_pt, 2),
        "position": n.position,
        "gapVerticalMm": round(n.gap_vertical_mm, 3),
        "gapHorizontalMm": round(n.gap_horizontal_mm, 3),
        "color": color_to_hex(n.color),
        "border": {
            "enabled": bool(n.border_enabled),
            "widthMm": round(n.border_width_mm, 3),
            "color": color_to_hex(n.border_color),
        },
        "startNumber": int(n.start_number),
        "hiddenNombre": bool(n.hidden_nombre),
    }


def nombre_from_dict(n, data: dict[str, Any]) -> None:
    data = data or {}
    n.enabled = bool(data.get("enabled", True))
    n.format = data.get("format", "{page}")
    n.font = data.get("font", "I-OTFアンチックStd B")
    n.font_size_pt = float(data.get("fontSizePt", 9.0))
    n.position = data.get("position", "bottom-center")
    n.gap_vertical_mm = float(data.get("gapVerticalMm", 5.0))
    n.gap_horizontal_mm = float(data.get("gapHorizontalMm", 0.0))
    n.color = hex_to_rgba(data.get("color", "#000000"))
    border = data.get("border", {})
    n.border_enabled = bool(border.get("enabled", False))
    n.border_width_mm = float(border.get("widthMm", 0.3))
    n.border_color = hex_to_rgba(border.get("color", "#FFFFFF"))
    n.start_number = int(data.get("startNumber", 1))
    n.hidden_nombre = bool(data.get("hiddenNombre", False))


# ---------- SafeAreaOverlay ----------


def safe_area_to_dict(sa) -> dict[str, Any]:
    return {
        "enabled": bool(sa.enabled),
        "color": color_to_hex(sa.color),
        "opacity": round(sa.opacity, 3),
        "blendMode": sa.blend_mode,
    }


def safe_area_from_dict(sa, data: dict[str, Any]) -> None:
    data = data or {}
    sa.enabled = bool(data.get("enabled", True))
    sa.color = hex_to_rgba(data.get("color", "#808080"))
    sa.opacity = float(data.get("opacity", 0.3))
    sa.blend_mode = data.get("blendMode", "multiply")


# ---------- PanelGap ----------


def panel_gap_to_dict(pg) -> dict[str, Any]:
    return {
        "verticalMm": round(pg.vertical_mm, 3),
        "horizontalMm": round(pg.horizontal_mm, 3),
    }


def panel_gap_from_dict(pg, data: dict[str, Any]) -> None:
    data = data or {}
    pg.vertical_mm = float(data.get("verticalMm", 7.3))
    pg.horizontal_mm = float(data.get("horizontalMm", 2.1))


# ---------- WorkData (root) ----------


def work_to_dict(work) -> dict[str, Any]:
    """BNameWorkData → work.json dict."""
    return {
        "schemaVersion": WORK_SCHEMA_VERSION,
        "workInfo": work_info_to_dict(work.work_info),
        "nombre": nombre_to_dict(work.nombre),
        "paper": paper_to_dict(work.paper),
        "panelGap": panel_gap_to_dict(work.panel_gap),
        "safeAreaOverlay": safe_area_to_dict(work.safe_area_overlay),
    }


def work_from_dict(work, data: dict[str, Any]) -> None:
    """work.json dict → BNameWorkData.

    schemaVersion が将来上がった場合はここでマイグレーションを挟む。
    """
    data = data or {}
    # 現状は v1 のみ対応。未知バージョンは読み込もうとするが警告は呼出側で。
    work_info_from_dict(work.work_info, data.get("workInfo", {}))
    nombre_from_dict(work.nombre, data.get("nombre", {}))
    paper_from_dict(work.paper, data.get("paper", {}))
    panel_gap_from_dict(work.panel_gap, data.get("panelGap", {}))
    safe_area_from_dict(work.safe_area_overlay, data.get("safeAreaOverlay", {}))


# ---------- PageEntry / pages.json ----------


def page_entry_to_dict(entry) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": entry.id,
        "title": entry.title,
        "dir": entry.dir_rel,
        "spread": bool(entry.spread),
    }
    if entry.spread:
        d["originalPages"] = [ref.page_id for ref in entry.original_pages]
        d["tombo"] = {
            "aligned": bool(entry.tombo_aligned),
            "gapMm": round(entry.tombo_gap_mm, 3),
        }
    if entry.thumbnail_rel:
        d["thumbnail"] = entry.thumbnail_rel
    if entry.panel_count:
        d["panelCount"] = int(entry.panel_count)
    return d


def page_entry_from_dict(entry, data: dict[str, Any]) -> None:
    data = data or {}
    entry.id = data.get("id", "")
    entry.title = data.get("title", "")
    entry.dir_rel = data.get("dir", "")
    entry.spread = bool(data.get("spread", False))
    entry.original_pages.clear()
    for ref_id in data.get("originalPages", []):
        ref = entry.original_pages.add()
        ref.page_id = ref_id
    tombo = data.get("tombo", {})
    entry.tombo_aligned = bool(tombo.get("aligned", True))
    entry.tombo_gap_mm = float(tombo.get("gapMm", -9.6))
    entry.thumbnail_rel = data.get("thumbnail", "")
    entry.panel_count = int(data.get("panelCount", 0))


def pages_to_dict(work, *, last_modified: str = "") -> dict[str, Any]:
    return {
        "schemaVersion": PAGES_SCHEMA_VERSION,
        "pages": [page_entry_to_dict(p) for p in work.pages],
        "totalPages": len(work.pages),
        "activePageIndex": int(work.active_page_index),
        "lastModified": last_modified,
    }


def pages_from_dict(work, data: dict[str, Any]) -> None:
    data = data or {}
    work.pages.clear()
    for entry_data in data.get("pages", []):
        entry = work.pages.add()
        page_entry_from_dict(entry, entry_data)
    idx = int(data.get("activePageIndex", -1))
    if idx < -1 or idx >= len(work.pages):
        idx = 0 if len(work.pages) > 0 else -1
    work.active_page_index = idx
