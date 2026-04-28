"""ページ/コマを統合レイヤーリストへ載せるための階層ヘルパ."""

from __future__ import annotations

PAGE_KIND = "page"
COMA_KIND = "coma"


def page_stack_key(page) -> str:
    return str(getattr(page, "id", "") or "")


def coma_stack_key(page, panel) -> str:
    stem = getattr(panel, "coma_id", "") or getattr(panel, "id", "")
    return f"{page_stack_key(page)}:{stem}"


def split_child_key(key: str) -> tuple[str, str]:
    page_id, sep, child_id = str(key or "").partition(":")
    return page_id, child_id if sep else ""


def coma_polygon(panel) -> list[tuple[float, float]]:
    if getattr(panel, "shape_type", "") == "rect":
        x = float(getattr(panel, "rect_x_mm", 0.0))
        y = float(getattr(panel, "rect_y_mm", 0.0))
        w = float(getattr(panel, "rect_width_mm", 0.0))
        h = float(getattr(panel, "rect_height_mm", 0.0))
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    verts = getattr(panel, "vertices", None)
    if verts is None or len(verts) < 3:
        return []
    return [(float(v.x_mm), float(v.y_mm)) for v in verts]


def point_in_polygon(point: tuple[float, float], poly: list[tuple[float, float]]) -> bool:
    if len(poly) < 3:
        return False
    x, y = point
    inside = False
    j = len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi + 1.0e-30) + xi
            if x <= x_cross:
                inside = not inside
        j = i
    return inside


def coma_containing_point(page, x_mm: float, y_mm: float):
    """ページローカル座標を含む最前面コマを返す。見つからなければ None."""
    best = None
    best_z = None
    for panel in getattr(page, "comas", []):
        if not point_in_polygon((x_mm, y_mm), coma_polygon(panel)):
            continue
        z = int(getattr(panel, "z_order", 0))
        if best is None or z > (best_z if best_z is not None else z - 1):
            best = panel
            best_z = z
    return best


def entry_center(entry) -> tuple[float, float]:
    return (
        float(getattr(entry, "x_mm", 0.0)) + float(getattr(entry, "width_mm", 0.0)) * 0.5,
        float(getattr(entry, "y_mm", 0.0)) + float(getattr(entry, "height_mm", 0.0)) * 0.5,
    )
