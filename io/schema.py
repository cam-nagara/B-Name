"""JSON スキーマの定義・バージョン・シリアライザ.

work.json / pages.json / page.json / panel_NNN.json の構造を 1 箇所に
集約する。将来のフォーマット変更に備えて ``schemaVersion`` フィールドを
各 JSON のトップレベルに付与する。

PropertyGroup ↔ dict の変換は to_dict / from_dict で行い、dict は
utils.json_io で書き出す。
"""

from __future__ import annotations

from typing import Any

from ..utils import color_space

# ファイルフォーマットのバージョン (破壊的変更があったら繰り上げる)
WORK_SCHEMA_VERSION = 1
PAGES_SCHEMA_VERSION = 1
PAGE_SCHEMA_VERSION = 1
PANEL_SCHEMA_VERSION = 1

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
        "startSide": paper.start_side,
        "readDirection": paper.read_direction,
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
    paper.start_side = data.get("startSide", "left")
    paper.read_direction = data.get("readDirection", "left")
    paper.preset_name = data.get("presetName", "集英社マンガ誌汎用")


# ---------- WorkInfo / DisplayItem / Nombre ----------


def display_item_to_dict(item) -> dict[str, Any]:
    return {
        "enabled": bool(item.enabled),
        "position": item.position,
        "fontSizeQ": round(item.font_size_q, 2),
        "color": color_to_hex(item.color),
    }


_DISPLAY_POSITION_MIGRATE = {
    # middle 段は廃止 (仕上がり枠外への配置でアンカーが不自然なため)
    "middle-left": "bottom-left",
    "middle-center": "bottom-center",
    "middle-right": "bottom-right",
}


def display_item_from_dict(item, data: dict[str, Any]) -> None:
    data = data or {}
    item.enabled = bool(data.get("enabled", False))
    pos = data.get("position", "bottom-left")
    item.position = _DISPLAY_POSITION_MIGRATE.get(pos, pos)
    # フォントサイズ: Q 数優先 (新)、旧 fontSizePt があれば pt → Q に変換
    if "fontSizeQ" in data:
        item.font_size_q = float(data["fontSizeQ"])
    elif "fontSizePt" in data:
        from ..utils.geom import pt_to_q
        item.font_size_q = float(pt_to_q(float(data["fontSizePt"])))
    else:
        item.font_size_q = 20.0
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
            "pageNumber": display_item_to_dict(info.display_page_number),
        },
        "pageNumberStart": int(info.page_number_start),
        "pageNumberEnd": int(getattr(info, "page_number_end", info.page_number_start)),
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
    display_item_from_dict(info.display_page_number, disp.get("pageNumber", {}))
    start = int(data.get("pageNumberStart", 1))
    info.page_number_start = start
    if hasattr(info, "page_number_end"):
        info.page_number_end = int(data.get("pageNumberEnd", start))


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
    # opacity / blend_mode は仕様変更で常に 1.0 / multiply 固定 (PG から削除)
    raw_color = tuple(float(c) for c in sa.color[:3])
    color = color_space.linear_to_srgb_rgb(raw_color)
    if all(abs(c - 0.7) < 1e-4 for c in raw_color):
        # 旧実装は COLOR プロパティに 0.7 を直接入れていたため、
        # UI上では約 0.854 に見える。未変更の旧既定は現行既定として保存する。
        color_hex = "#B3B3B3"
    elif all(abs(c - 0.7) < 1e-4 for c in color):
        color_hex = "#B3B3B3"
    else:
        color_hex = color_to_hex(color)
    return {
        "enabled": bool(sa.enabled),
        "color": color_hex,
    }


def safe_area_from_dict(sa, data: dict[str, Any]) -> None:
    data = data or {}
    sa.enabled = bool(data.get("enabled", True))
    # color は size=3 の RGB のみ (旧データの alpha は無視)。
    # 未保存時の既定値は明度 0.7 のグレーに揃える。
    if "color" in data:
        color_code = str(data["color"]).strip().upper()
        # 旧版の既定値は #808080 だった。保存済み作品の「旧既定」が
        # 新規既定に見えてしまうため、読み込み時に現行既定へ移行する。
        if color_code in {
            "#808080", "808080",
            "#7F7F7F", "7F7F7F",
            "#B2B2B2", "B2B2B2",
            "#B3B3B3", "B3B3B3",
            "#D9D9D9", "D9D9D9",
            "#DADADA", "DADADA",
        }:
            sa.color = color_space.srgb_to_linear_rgb((0.7, 0.7, 0.7))
        else:
            rgba = hex_to_rgba(color_code)
            sa.color = color_space.srgb_to_linear_rgb(rgba[:3])
    else:
        sa.color = color_space.srgb_to_linear_rgb((0.7, 0.7, 0.7))
    # 旧 opacity / blendMode フィールドが残っていても無視 (互換読込)


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
        "visible": bool(getattr(entry, "visible", True)),
        "offsetXMm": round(float(getattr(entry, "offset_x_mm", 0.0)), 3),
        "offsetYMm": round(float(getattr(entry, "offset_y_mm", 0.0)), 3),
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
    if hasattr(entry, "visible"):
        entry.visible = bool(data.get("visible", True))
    entry.offset_x_mm = float(data.get("offsetXMm", 0.0))
    entry.offset_y_mm = float(data.get("offsetYMm", 0.0))
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


# ---------- Panel (border/white_margin/entry) ----------


def _edge_override_to_dict(edge) -> dict[str, Any]:
    if not edge.use_override:
        return {"useOverride": False}
    out: dict[str, Any] = {"useOverride": True}
    # border edge / white margin edge で持つフィールドが違うため hasattr で判定
    if hasattr(edge, "style"):
        out["style"] = edge.style
    if hasattr(edge, "width_mm"):
        out["widthMm"] = round(edge.width_mm, 3)
    if hasattr(edge, "color"):
        out["color"] = color_to_hex(edge.color)
    if hasattr(edge, "visible"):
        out["visible"] = bool(edge.visible)
    if hasattr(edge, "enabled"):
        out["enabled"] = bool(edge.enabled)
    return out


def _edge_override_from_dict(edge, data: dict[str, Any]) -> None:
    data = data or {}
    edge.use_override = bool(data.get("useOverride", False))
    if "style" in data and hasattr(edge, "style"):
        edge.style = data["style"]
    if "widthMm" in data and hasattr(edge, "width_mm"):
        edge.width_mm = float(data["widthMm"])
    if "color" in data and hasattr(edge, "color"):
        edge.color = hex_to_rgba(data["color"])
    if "visible" in data and hasattr(edge, "visible"):
        edge.visible = bool(data["visible"])
    if "enabled" in data and hasattr(edge, "enabled"):
        edge.enabled = bool(data["enabled"])


def panel_border_to_dict(border) -> dict[str, Any]:
    return {
        "style": border.style,
        "widthMm": round(border.width_mm, 3),
        "color": color_to_hex(border.color),
        "corner": {
            "type": border.corner_type,
            "radiusMm": round(border.corner_radius_mm, 3),
        },
        "visible": bool(border.visible),
        "perEdge": {
            "top": _edge_override_to_dict(border.edge_top),
            "right": _edge_override_to_dict(border.edge_right),
            "bottom": _edge_override_to_dict(border.edge_bottom),
            "left": _edge_override_to_dict(border.edge_left),
        },
    }


def panel_border_from_dict(border, data: dict[str, Any]) -> None:
    data = data or {}
    border.style = data.get("style", "solid")
    border.width_mm = float(data.get("widthMm", 0.8))
    border.color = hex_to_rgba(data.get("color", "#000000"))
    corner = data.get("corner", {})
    border.corner_type = corner.get("type", "square")
    border.corner_radius_mm = float(corner.get("radiusMm", 0.0))
    border.visible = bool(data.get("visible", True))
    per = data.get("perEdge", {})
    _edge_override_from_dict(border.edge_top, per.get("top", {}))
    _edge_override_from_dict(border.edge_right, per.get("right", {}))
    _edge_override_from_dict(border.edge_bottom, per.get("bottom", {}))
    _edge_override_from_dict(border.edge_left, per.get("left", {}))


def panel_white_margin_to_dict(wm) -> dict[str, Any]:
    return {
        "enabled": bool(wm.enabled),
        "widthMm": round(wm.width_mm, 3),
        "color": color_to_hex(wm.color),
        "perEdge": {
            "top": _edge_override_to_dict(wm.edge_top),
            "right": _edge_override_to_dict(wm.edge_right),
            "bottom": _edge_override_to_dict(wm.edge_bottom),
            "left": _edge_override_to_dict(wm.edge_left),
        },
    }


def panel_white_margin_from_dict(wm, data: dict[str, Any]) -> None:
    data = data or {}
    wm.enabled = bool(data.get("enabled", False))
    wm.width_mm = float(data.get("widthMm", 0.37))
    wm.color = hex_to_rgba(data.get("color", "#FFFFFF"))
    per = data.get("perEdge", {})
    _edge_override_from_dict(wm.edge_top, per.get("top", {}))
    _edge_override_from_dict(wm.edge_right, per.get("right", {}))
    _edge_override_from_dict(wm.edge_bottom, per.get("bottom", {}))
    _edge_override_from_dict(wm.edge_left, per.get("left", {}))


def panel_entry_to_dict(entry) -> dict[str, Any]:
    d: dict[str, Any] = {
        "schemaVersion": PANEL_SCHEMA_VERSION,
        "id": entry.id,
        "title": entry.title,
        "panelStem": entry.panel_stem,
        "shape": {
            "type": entry.shape_type,
            "rect": {
                "x": round(entry.rect_x_mm, 3),
                "y": round(entry.rect_y_mm, 3),
                "widthMm": round(entry.rect_width_mm, 3),
                "heightMm": round(entry.rect_height_mm, 3),
            },
            "vertices": [[round(v.x_mm, 3), round(v.y_mm, 3)] for v in entry.vertices],
        },
        "zOrder": int(entry.z_order),
        "overlapClipping": bool(entry.overlap_clipping),
        "visible": bool(getattr(entry, "visible", True)),
        "backgroundColor": color_to_hex(entry.background_color),
        "backgroundColorAlpha": round(entry.background_color[3], 3),
        "border": panel_border_to_dict(entry.border),
        "whiteMargin": panel_white_margin_to_dict(entry.white_margin),
        "edgeStyles": [
            {
                "edgeIndex": int(s.edge_index),
                "widthMm": round(s.width_mm, 3),
                "color": color_to_hex(s.color),
                "colorAlpha": round(s.color[3], 3),
            }
            for s in entry.edge_styles
        ],
        "layerRefs": [r.layer_id for r in entry.layer_refs],
        "panelGap": {
            "verticalMm": round(entry.panel_gap_vertical_mm, 3),
            "horizontalMm": round(entry.panel_gap_horizontal_mm, 3),
        },
    }
    return d


def panel_entry_from_dict(entry, data: dict[str, Any]) -> None:
    data = data or {}
    entry.id = data.get("id", "")
    entry.title = data.get("title", "")
    entry.panel_stem = data.get("panelStem", "")
    shape = data.get("shape", {})
    entry.shape_type = shape.get("type", "rect")
    rect = shape.get("rect", {})
    entry.rect_x_mm = float(rect.get("x", 0.0))
    entry.rect_y_mm = float(rect.get("y", 0.0))
    entry.rect_width_mm = float(rect.get("widthMm", 50.0))
    entry.rect_height_mm = float(rect.get("heightMm", 50.0))
    entry.vertices.clear()
    for pair in shape.get("vertices", []):
        v = entry.vertices.add()
        v.x_mm = float(pair[0]) if len(pair) > 0 else 0.0
        v.y_mm = float(pair[1]) if len(pair) > 1 else 0.0
    entry.z_order = int(data.get("zOrder", 0))
    entry.overlap_clipping = bool(data.get("overlapClipping", True))
    if hasattr(entry, "visible"):
        entry.visible = bool(data.get("visible", True))
    bg_alpha = float(data.get("backgroundColorAlpha", 0.0))
    entry.background_color = hex_to_rgba(data.get("backgroundColor", "#FFFFFF"), bg_alpha)
    panel_border_from_dict(entry.border, data.get("border", {}))
    panel_white_margin_from_dict(entry.white_margin, data.get("whiteMargin", {}))
    entry.edge_styles.clear()
    for st in data.get("edgeStyles", []) or []:
        es = entry.edge_styles.add()
        es.edge_index = int(st.get("edgeIndex", 0))
        es.width_mm = float(st.get("widthMm", 0.5))
        alpha = float(st.get("colorAlpha", 1.0))
        es.color = hex_to_rgba(st.get("color", "#000000"), alpha)
    entry.layer_refs.clear()
    for lid in data.get("layerRefs", []):
        ref = entry.layer_refs.add()
        ref.layer_id = str(lid)
    gap = data.get("panelGap", {})
    entry.panel_gap_vertical_mm = float(gap.get("verticalMm", -1.0))
    entry.panel_gap_horizontal_mm = float(gap.get("horizontalMm", -1.0))


# ---------- Balloon / Text (Phase 3) ----------


def balloon_entry_to_dict(entry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "shape": entry.shape,
        "customPresetName": entry.custom_preset_name,
        "xMm": round(entry.x_mm, 3),
        "yMm": round(entry.y_mm, 3),
        "widthMm": round(entry.width_mm, 3),
        "heightMm": round(entry.height_mm, 3),
        "rotationDeg": round(entry.rotation_deg, 3),
        "roundedCornerEnabled": bool(entry.rounded_corner_enabled),
        "roundedCornerRadiusMm": round(entry.rounded_corner_radius_mm, 3),
        "lineStyle": entry.line_style,
        "lineWidthMm": round(entry.line_width_mm, 3),
        "lineColor": color_to_hex(entry.line_color),
        "lineColorAlpha": round(entry.line_color[3], 3),
        "fillColor": color_to_hex(entry.fill_color),
        "fillColorAlpha": round(entry.fill_color[3], 3),
        "tails": [
            {
                "type": t.type,
                "directionDeg": round(t.direction_deg, 3),
                "lengthMm": round(t.length_mm, 3),
                "rootWidthMm": round(t.root_width_mm, 3),
                "tipWidthMm": round(t.tip_width_mm, 3),
                "curveBend": round(t.curve_bend, 3),
            }
            for t in entry.tails
        ],
        "shapeParams": {
            "cloudWaveCount": int(entry.shape_params.cloud_wave_count),
            "cloudWaveAmplitudeMm": round(entry.shape_params.cloud_wave_amplitude_mm, 3),
            "spikeCount": int(entry.shape_params.spike_count),
            "spikeDepthMm": round(entry.shape_params.spike_depth_mm, 3),
            "spikeJitter": round(entry.shape_params.spike_jitter, 3),
        },
        "textId": entry.text_id,
    }


def balloon_entry_from_dict(entry, data: dict[str, Any]) -> None:
    data = data or {}
    entry.id = data.get("id", entry.id)
    entry.shape = data.get("shape", entry.shape)
    entry.custom_preset_name = data.get("customPresetName", "")
    entry.x_mm = float(data.get("xMm", 0.0))
    entry.y_mm = float(data.get("yMm", 0.0))
    entry.width_mm = float(data.get("widthMm", 40.0))
    entry.height_mm = float(data.get("heightMm", 20.0))
    entry.rotation_deg = float(data.get("rotationDeg", 0.0))
    entry.rounded_corner_enabled = bool(data.get("roundedCornerEnabled", False))
    entry.rounded_corner_radius_mm = float(data.get("roundedCornerRadiusMm", 3.0))
    entry.line_style = data.get("lineStyle", "solid")
    entry.line_width_mm = float(data.get("lineWidthMm", 0.6))
    alpha = float(data.get("lineColorAlpha", 1.0))
    entry.line_color = hex_to_rgba(data.get("lineColor", "#000000"), alpha)
    alpha = float(data.get("fillColorAlpha", 1.0))
    entry.fill_color = hex_to_rgba(data.get("fillColor", "#FFFFFF"), alpha)
    entry.tails.clear()
    for td in data.get("tails", []):
        tail = entry.tails.add()
        tail.type = td.get("type", "straight")
        tail.direction_deg = float(td.get("directionDeg", 270.0))
        tail.length_mm = float(td.get("lengthMm", 6.0))
        tail.root_width_mm = float(td.get("rootWidthMm", 3.0))
        tail.tip_width_mm = float(td.get("tipWidthMm", 0.0))
        tail.curve_bend = float(td.get("curveBend", 0.0))
    sp = data.get("shapeParams", {})
    entry.shape_params.cloud_wave_count = int(sp.get("cloudWaveCount", 12))
    entry.shape_params.cloud_wave_amplitude_mm = float(sp.get("cloudWaveAmplitudeMm", 3.0))
    entry.shape_params.spike_count = int(sp.get("spikeCount", 24))
    entry.shape_params.spike_depth_mm = float(sp.get("spikeDepthMm", 6.0))
    entry.shape_params.spike_jitter = float(sp.get("spikeJitter", 0.2))
    entry.text_id = data.get("textId", "")


def text_entry_to_dict(entry) -> dict[str, Any]:
    from ..utils.geom import pt_to_q

    font_size_q = float(
        getattr(entry, "font_size_q", pt_to_q(float(getattr(entry, "font_size_pt", 9.0))))
    )
    return {
        "id": entry.id,
        "body": entry.body,
        "speakerType": entry.speaker_type,
        "speakerName": entry.speaker_name,
        "font": entry.font,
        "fontSizeQ": round(font_size_q, 3),
        "color": color_to_hex(entry.color),
        "colorAlpha": round(entry.color[3], 3),
        "writingMode": entry.writing_mode,
        "lineHeight": round(entry.line_height, 3),
        "letterSpacing": round(entry.letter_spacing, 3),
        "strokeEnabled": bool(entry.stroke_enabled),
        "strokeWidthMm": round(entry.stroke_width_mm, 3),
        "strokeColor": color_to_hex(entry.stroke_color),
        "strokeColorAlpha": round(entry.stroke_color[3], 3),
        "xMm": round(entry.x_mm, 3),
        "yMm": round(entry.y_mm, 3),
        "widthMm": round(entry.width_mm, 3),
        "heightMm": round(entry.height_mm, 3),
        "parentBalloonId": entry.parent_balloon_id,
    }


def text_entry_from_dict(entry, data: dict[str, Any]) -> None:
    from ..utils.geom import pt_to_q, q_to_pt

    data = data or {}
    entry.id = data.get("id", entry.id)
    entry.body = data.get("body", "")
    entry.speaker_type = data.get("speakerType", "normal")
    entry.speaker_name = data.get("speakerName", "")
    entry.font = data.get("font", "")
    if "fontSizeQ" in data:
        entry.font_size_q = float(data["fontSizeQ"])
    elif "fontSizePt" in data:
        entry.font_size_q = float(pt_to_q(float(data["fontSizePt"])))
    else:
        entry.font_size_q = 20.0
    entry.font_size_pt = float(q_to_pt(float(entry.font_size_q)))
    alpha = float(data.get("colorAlpha", 1.0))
    entry.color = hex_to_rgba(data.get("color", "#000000"), alpha)
    entry.writing_mode = data.get("writingMode", "vertical")
    entry.line_height = float(data.get("lineHeight", 1.4))
    entry.letter_spacing = float(data.get("letterSpacing", 0.0))
    entry.stroke_enabled = bool(data.get("strokeEnabled", False))
    entry.stroke_width_mm = float(data.get("strokeWidthMm", 0.2))
    alpha = float(data.get("strokeColorAlpha", 1.0))
    entry.stroke_color = hex_to_rgba(data.get("strokeColor", "#FFFFFF"), alpha)
    entry.x_mm = float(data.get("xMm", 0.0))
    entry.y_mm = float(data.get("yMm", 0.0))
    entry.width_mm = float(data.get("widthMm", 30.0))
    entry.height_mm = float(data.get("heightMm", 15.0))
    entry.parent_balloon_id = data.get("parentBalloonId", "")


# ---------- page.json ----------


def page_to_dict(page_entry) -> dict[str, Any]:
    """page.json (個別ページメタ) を書き出す.

    page_entry は BNamePageEntry。panels / balloons / texts をシリアライズする。
    """
    return {
        "schemaVersion": PAGE_SCHEMA_VERSION,
        "id": page_entry.id,
        "title": page_entry.title,
        "spread": bool(page_entry.spread),
        "offsetXMm": round(float(getattr(page_entry, "offset_x_mm", 0.0)), 3),
        "offsetYMm": round(float(getattr(page_entry, "offset_y_mm", 0.0)), 3),
        "activePanelIndex": int(page_entry.active_panel_index),
        "activeBalloonIndex": int(page_entry.active_balloon_index),
        "activeTextIndex": int(page_entry.active_text_index),
        "panels": [panel_entry_to_dict(p) for p in page_entry.panels],
        "balloons": [balloon_entry_to_dict(b) for b in page_entry.balloons],
        "texts": [text_entry_to_dict(t) for t in page_entry.texts],
    }


def page_from_dict(page_entry, data: dict[str, Any]) -> None:
    data = data or {}
    page_entry.id = data.get("id", page_entry.id)
    if "title" in data:
        page_entry.title = data["title"]
    page_entry.offset_x_mm = float(data.get("offsetXMm", getattr(page_entry, "offset_x_mm", 0.0)))
    page_entry.offset_y_mm = float(data.get("offsetYMm", getattr(page_entry, "offset_y_mm", 0.0)))
    page_entry.panels.clear()
    for panel_data in data.get("panels", []):
        entry = page_entry.panels.add()
        panel_entry_from_dict(entry, panel_data)
    page_entry.balloons.clear()
    for b_data in data.get("balloons", []):
        entry = page_entry.balloons.add()
        balloon_entry_from_dict(entry, b_data)
    page_entry.texts.clear()
    for t_data in data.get("texts", []):
        entry = page_entry.texts.add()
        text_entry_from_dict(entry, t_data)
    idx = int(data.get("activePanelIndex", -1))
    if idx < -1 or idx >= len(page_entry.panels):
        idx = 0 if len(page_entry.panels) > 0 else -1
    page_entry.active_panel_index = idx
    idx = int(data.get("activeBalloonIndex", -1))
    if idx < -1 or idx >= len(page_entry.balloons):
        idx = 0 if len(page_entry.balloons) > 0 else -1
    page_entry.active_balloon_index = idx
    idx = int(data.get("activeTextIndex", -1))
    if idx < -1 or idx >= len(page_entry.texts):
        idx = 0 if len(page_entry.texts) > 0 else -1
    page_entry.active_text_index = idx


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
