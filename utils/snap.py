"""コマ枠のスナップ計算 (計画書 3.2.5.3).

スナップ対象:
- 仕上がり枠 (finish)
- 基本枠 (inner_frame)
- セーフライン (safe)
- トンボ (tombo) — Phase 2 では仕上がり枠の四隅と同じ扱い
- 他のコマ枠 (作品共通のコマ間隔を保って)

純粋関数のみ。ユーザー入力 (Shift 押下時の無効化等) は呼出側が制御。
"""

from __future__ import annotations

from dataclasses import dataclass

from .geom import Rect, canvas_rect, finish_rect, inner_frame_rect, safe_rect

DEFAULT_SNAP_RADIUS_MM = 2.0


@dataclass(frozen=True)
class SnapCandidate:
    """スナップ対象の 1 エッジ."""

    axis: str  # "x" | "y"
    value_mm: float
    label: str  # "finish.left" 等、デバッグ用


def collect_paper_snap_candidates(paper) -> list[SnapCandidate]:
    """用紙関連のスナップエッジ一覧を作成."""
    out: list[SnapCandidate] = []
    canvas = canvas_rect(paper)
    finish = finish_rect(paper)
    inner = inner_frame_rect(paper)
    safe = safe_rect(paper)
    for r, label in (
        (canvas, "canvas"),
        (finish, "finish"),
        (inner, "inner"),
        (safe, "safe"),
    ):
        out.append(SnapCandidate("x", r.x, f"{label}.left"))
        out.append(SnapCandidate("x", r.x2, f"{label}.right"))
        out.append(SnapCandidate("y", r.y, f"{label}.bottom"))
        out.append(SnapCandidate("y", r.y2, f"{label}.top"))
    return out


def collect_panel_snap_candidates(panels, exclude_stem: str = "") -> list[SnapCandidate]:
    """他のコマ枠のエッジをスナップ候補として追加.

    exclude_stem: 編集中のコマ自身を除外する stem 名。
    """
    out: list[SnapCandidate] = []
    for entry in panels:
        if entry.panel_stem == exclude_stem:
            continue
        if entry.shape_type != "rect":
            continue
        out.append(SnapCandidate("x", entry.rect_x_mm, f"{entry.panel_stem}.left"))
        out.append(SnapCandidate("x", entry.rect_x_mm + entry.rect_width_mm, f"{entry.panel_stem}.right"))
        out.append(SnapCandidate("y", entry.rect_y_mm, f"{entry.panel_stem}.bottom"))
        out.append(SnapCandidate("y", entry.rect_y_mm + entry.rect_height_mm, f"{entry.panel_stem}.top"))
    return out


def collect_panel_snap_with_gap(
    panels,
    gap_h_mm: float,
    gap_v_mm: float,
    exclude_stem: str = "",
) -> list[SnapCandidate]:
    """コマ間隔を保ったスナップ候補 (作品共通のスキマ)."""
    out: list[SnapCandidate] = []
    for entry in panels:
        if entry.panel_stem == exclude_stem:
            continue
        if entry.shape_type != "rect":
            continue
        left = entry.rect_x_mm
        right = entry.rect_x_mm + entry.rect_width_mm
        bottom = entry.rect_y_mm
        top = entry.rect_y_mm + entry.rect_height_mm
        out.append(SnapCandidate("x", left - gap_h_mm, f"{entry.panel_stem}.gap.left"))
        out.append(SnapCandidate("x", right + gap_h_mm, f"{entry.panel_stem}.gap.right"))
        out.append(SnapCandidate("y", bottom - gap_v_mm, f"{entry.panel_stem}.gap.bottom"))
        out.append(SnapCandidate("y", top + gap_v_mm, f"{entry.panel_stem}.gap.top"))
    return out


def snap_value(
    target_mm: float,
    axis: str,
    candidates: list[SnapCandidate],
    radius_mm: float = DEFAULT_SNAP_RADIUS_MM,
) -> tuple[float, SnapCandidate | None]:
    """対象値に最も近い候補へスナップ.

    Returns: (スナップ後の値, ヒットした候補) — 半径内に候補が無ければ (target, None)。
    """
    best: SnapCandidate | None = None
    best_dist = radius_mm + 1.0
    for cand in candidates:
        if cand.axis != axis:
            continue
        d = abs(cand.value_mm - target_mm)
        if d < best_dist:
            best_dist = d
            best = cand
    if best is not None and best_dist <= radius_mm:
        return best.value_mm, best
    return target_mm, None


def snap_rect(
    rect: Rect,
    paper,
    other_panels,
    gap_h_mm: float,
    gap_v_mm: float,
    exclude_stem: str = "",
    radius_mm: float = DEFAULT_SNAP_RADIUS_MM,
) -> Rect:
    """矩形の 4 辺をスナップして返す."""
    candidates = (
        collect_paper_snap_candidates(paper)
        + collect_panel_snap_candidates(other_panels, exclude_stem)
        + collect_panel_snap_with_gap(other_panels, gap_h_mm, gap_v_mm, exclude_stem)
    )
    left, _ = snap_value(rect.x, "x", candidates, radius_mm)
    right, _ = snap_value(rect.x2, "x", candidates, radius_mm)
    bottom, _ = snap_value(rect.y, "y", candidates, radius_mm)
    top, _ = snap_value(rect.y2, "y", candidates, radius_mm)
    return Rect(left, bottom, max(1.0, right - left), max(1.0, top - bottom))
