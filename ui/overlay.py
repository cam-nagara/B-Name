"""ビューポート上の原稿オーバーレイ描画 (draw_handler_add + gpu).

計画書 3.4.3a に従い、以下を gpu + blf でオーバーレイ描画する:
- キャンバス (用紙) 枠
- 仕上がり枠 / 基本枠 / セーフライン枠
- セーフライン外側オーバーレイ (乗算)
- ノンブル / 作品情報 (blf)
- 各ページ上部のページ識別番号 (001 形式、ビューポート用ガイド)

書き出し結果には焼き込まれない (書き出し時は export_renderer が同じ
overlay_shared ロジックを Pillow で再実装する、Phase 6 で実装)。

座標系:
- 原稿座標は mm 基準 (キャンバス左下が原点)
- Blender ビューポートへの描画は 3D ワールド空間の XY 平面上 (z=0)
- 1 mm = 0.001 Blender unit で配置。カメラリグは Phase 2 で実装予定のため、
  現段階ではワールド XY 平面への配置のみで、カメラがこの平面を写す想定。
"""

from __future__ import annotations

from typing import Optional

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

try:
    import gpu.texture as gpu_texture  # type: ignore
    _HAS_GPU_TEXTURE = True
except ImportError:  # pragma: no cover - 古い Blender
    gpu_texture = None  # type: ignore
    _HAS_GPU_TEXTURE = False

from ..core.mode import MODE_PAGE, MODE_PANEL, get_mode
from ..core.work import get_active_page, get_work
from ..utils import border_geom, color_space, log, page_browser, text_style, viewport_colors
from ..utils.geom import Rect, bleed_rect, mm_to_m
from . import overlay_balloon
from . import overlay_effect_line
from . import overlay_panel_selection
from . import overlay_shared
from . import overlay_text
from . import overlay_visibility
from . import panel_preview_overlay

_logger = log.get_logger(__name__)

# draw_handler_add の戻り値 (ハンドラ識別子)
_handle: Optional[object] = None
# blf テキスト描画は POST_VIEW では view/projection matrix が適用されて
# screen 座標が world 座標扱いになり画面外に飛ぶため、POST_PIXEL で別 handler。
_handle_pixel: Optional[object] = None

# 作品情報描画の診断ログ用 tick (60 フレーム毎に 1 回出力)
_WORK_INFO_DEBUG_TICK = 0

# 日本語対応フォントの font_id キャッシュ (起動時 1 回ロード).
# blf.draw で font_id=0 を使うと ASCII しか描けず日本語が文字化けるため、
# OS のシステムフォントから日本語対応フォントを load しておく。
# 値が None = 未試行、-1 = ロード失敗 (font_id=0 fallback)、0 以上 = ロード済み。
_JP_FONT_ID: Optional[int] = None
_FONT_ID_BY_PATH: dict[str, int] = {}

# 作品情報とは独立した、ビューポート用のページ識別番号。
_PAGE_HEADER_GAP_MM = 6.0
_PAGE_HEADER_FONT_SIZE_PX = 34
_PAGE_HEADER_COLOR = (0.0, 0.0, 0.0, 0.95)
_PAGE_HEADER_OUTLINE_COLOR = (1.0, 1.0, 1.0, 0.9)


def _get_jp_font_id() -> int:
    """日本語表示用 blf font_id を返す (load 失敗時は 0).

    起動時に Windows / macOS / Linux の代表的な日本語フォントから 1 つ
    ロードを試みる。失敗なら font_id=0 (ASCII のみ) を返す。
    """
    global _JP_FONT_ID
    if _JP_FONT_ID is not None:
        return _JP_FONT_ID if _JP_FONT_ID >= 0 else 0
    import os
    candidates = []
    if os.name == "nt":
        candidates.extend([
            r"C:\Windows\Fonts\YuGothM.ttc",
            r"C:\Windows\Fonts\meiryo.ttc",
            r"C:\Windows\Fonts\msgothic.ttc",
            r"C:\Windows\Fonts\YuGothR.ttc",
        ])
    else:
        # macOS / Linux 候補
        candidates.extend([
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        ])
    for path in candidates:
        try:
            if not os.path.isfile(path):
                continue
            fid = blf.load(path)
            if fid is not None and fid != -1:
                _JP_FONT_ID = int(fid)
                _logger.info("blf JP font loaded: %s -> id=%d", path, fid)
                return _JP_FONT_ID
        except Exception:  # noqa: BLE001
            continue
    _JP_FONT_ID = -1
    _logger.warning("blf JP font load failed; falling back to font_id=0 (ASCII only)")
    return 0


def _get_font_id_for_path(font_path: str) -> int:
    resolved = text_style.resolve_font_path(font_path)
    if not resolved:
        return _get_jp_font_id()
    key = resolved.lower()
    cached = _FONT_ID_BY_PATH.get(key)
    if cached is not None:
        return cached if cached >= 0 else _get_jp_font_id()
    try:
        fid = blf.load(resolved)
        if fid is not None and fid != -1:
            _FONT_ID_BY_PATH[key] = int(fid)
            return int(fid)
    except Exception:  # noqa: BLE001
        pass
    _FONT_ID_BY_PATH[key] = -1
    return _get_jp_font_id()


# ---------- 低レベル描画ヘルパ ----------


def _draw_rect_fill(rect: Rect, color: tuple[float, float, float, float]) -> None:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [
        (mm_to_m(rect.x), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y2), 0.0),
        (mm_to_m(rect.x), mm_to_m(rect.y2), 0.0),
    ]
    indices = [(0, 1, 2), (0, 2, 3)]
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_rect_outline(
    rect: Rect,
    color: tuple[float, float, float, float],
    line_width: float = 1.0,
    width_mm: float | None = None,
) -> None:
    """矩形の枠線を描画.

    ``width_mm`` を指定すると mm 単位の太さで 4 本の塗り帯を描画する
    (= ズームに連動して画面上の太さが変わる、紙に追従する線)。
    既定 (None) は ``line_width`` (px 単位) で従来の LINE_STRIP 描画
    (画面上一定の太さ、紙に追従しない)。
    """
    if width_mm is not None and width_mm > 0.0:
        _draw_rect_outline_mm(rect, color, width_mm)
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [
        (mm_to_m(rect.x), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y2), 0.0),
        (mm_to_m(rect.x), mm_to_m(rect.y2), 0.0),
        (mm_to_m(rect.x), mm_to_m(rect.y), 0.0),
    ]
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    try:
        gpu.state.line_width_set(max(1.0, float(line_width)))
        batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.0)


def _draw_rect_outline_mm(
    rect: Rect,
    color: tuple[float, float, float, float],
    width_mm: float,
) -> None:
    """mm 単位の太さで矩形枠を 4 本の塗り帯として描画 (ズーム連動)."""
    w = max(0.001, float(width_mm))
    half = w * 0.5
    # 4 本の帯 (上下左右、コーナーで矩形を共有して overlap)
    top = Rect(rect.x - half, rect.y2 - half, rect.width + w, w)
    bottom = Rect(rect.x - half, rect.y - half, rect.width + w, w)
    left = Rect(rect.x - half, rect.y - half, w, rect.height + w)
    right = Rect(rect.x2 - half, rect.y - half, w, rect.height + w)
    for r in (top, bottom, left, right):
        if r.width > 0 and r.height > 0:
            _draw_rect_fill(r, color)


def _draw_segments_mm(
    segs: list[tuple[tuple[float, float], tuple[float, float]]],
    color: tuple[float, float, float, float],
    width_mm: float,
) -> None:
    """mm 単位の太さで線分群を塗りポリゴンとして描画 (ズーム連動).

    各線分は太さ ``width_mm`` の細長い矩形 (端は square cap、両端で
    width_mm/2 ずつ伸びる) として描画する。
    """
    if not segs:
        return
    import math as _math
    w = max(0.001, float(width_mm))
    half = w * 0.5
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []
    for (x1, y1), (x2, y2) in segs:
        dx = x2 - x1
        dy = y2 - y1
        length = _math.hypot(dx, dy)
        if length <= 0.0:
            continue
        # 単位ベクトルと法線
        ux, uy = dx / length, dy / length
        nx, ny = -uy, ux
        # square cap で両端を half だけ延長
        ex1, ey1 = x1 - ux * half, y1 - uy * half
        ex2, ey2 = x2 + ux * half, y2 + uy * half
        # 4 頂点
        p0 = (ex1 + nx * half, ey1 + ny * half)
        p1 = (ex2 + nx * half, ey2 + ny * half)
        p2 = (ex2 - nx * half, ey2 - ny * half)
        p3 = (ex1 - nx * half, ey1 - ny * half)
        base = len(verts)
        for px, py in (p0, p1, p2, p3):
            verts.append((mm_to_m(px), mm_to_m(py), 0.0))
        indices.append((base, base + 1, base + 2))
        indices.append((base, base + 2, base + 3))
    if not verts:
        return
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_line_segments(
    segs: list[tuple[tuple[float, float], tuple[float, float]]],
    color: tuple[float, float, float, float],
    line_width: float = 1.0,
) -> None:
    """複数の独立した線分 ((x1,y1)-(x2,y2) の集合、mm 単位) を一括描画."""
    if not segs:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts: list[tuple[float, float, float]] = []
    for (x1, y1), (x2, y2) in segs:
        verts.append((mm_to_m(x1), mm_to_m(y1), 0.0))
        verts.append((mm_to_m(x2), mm_to_m(y2), 0.0))
    batch = batch_for_shader(shader, "LINES", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    try:
        gpu.state.line_width_set(max(1.0, float(line_width)))
        batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.0)


def _draw_trim_marks(
    finish: Rect,
    bleed: Rect,
    color: tuple[float, float, float, float] = viewport_colors.PAPER_GUIDE_LIGHT,
    corner_arm_mm: float = 10.0,
    center_size_mm: float = 10.0,
    center_gap_mm: float = 5.0,
    line_width: float = 1.0,
) -> None:
    """トンボ (コーナー + センタートンボ) を CLIP STUDIO PAINT と同じ仕様で描画.

    コーナートンボ (4 隅): 二重 L 字 (裁ち落とし枠の角の外側にのみ描画)
      - 内側 L: 仕上がり枠の辺の延長線。裁ち落とし枠の角から外側へ
        ``corner_arm_mm`` 伸びる横線・縦線で、座標は finish 辺と同じ
      - 外側 L: 裁ち落とし枠の辺の延長線。bleed 角から外側へ ``corner_arm_mm``
        伸びる横線・縦線で、座標は bleed 辺と同じ
      - 仕上がり枠と裁ち落とし枠の間 (= 裁ち落とし領域内側) には線を描かない

    センタートンボ (4 辺中央): 十字 (+) マーク
      - 各辺中央に + 字、裁ち落とし枠の外側 ``center_gap_mm`` 離れた位置に
        配置。十字の腕長 = ``center_size_mm`` の半分。
    """
    fr, br = finish, bleed
    A = corner_arm_mm
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []

    # --- コーナートンボ (4 隅) ---
    # 各コーナーで 4 本: 内側 L (仕上がり延長 H/V) + 外側 L (裁ち落とし延長 H/V)
    # Bottom-Left: 仕上がり線を左/下方向に延長、裁ち落とし線も左/下方向に延長
    segs.append(((br.x - A, fr.y), (br.x, fr.y)))    # 内 L 横 (仕上がり Y = fr.y)
    segs.append(((fr.x, br.y - A), (fr.x, br.y)))    # 内 L 縦 (仕上がり X = fr.x)
    segs.append(((br.x - A, br.y), (br.x, br.y)))    # 外 L 横 (裁ち落とし Y = br.y)
    segs.append(((br.x, br.y - A), (br.x, br.y)))    # 外 L 縦 (裁ち落とし X = br.x)
    # Bottom-Right
    segs.append(((br.x2, fr.y), (br.x2 + A, fr.y)))
    segs.append(((fr.x2, br.y - A), (fr.x2, br.y)))
    segs.append(((br.x2, br.y), (br.x2 + A, br.y)))
    segs.append(((br.x2, br.y - A), (br.x2, br.y)))
    # Top-Left
    segs.append(((br.x - A, fr.y2), (br.x, fr.y2)))
    segs.append(((fr.x, br.y2), (fr.x, br.y2 + A)))
    segs.append(((br.x - A, br.y2), (br.x, br.y2)))
    segs.append(((br.x, br.y2), (br.x, br.y2 + A)))
    # Top-Right
    segs.append(((br.x2, fr.y2), (br.x2 + A, fr.y2)))
    segs.append(((fr.x2, br.y2), (fr.x2, br.y2 + A)))
    segs.append(((br.x2, br.y2), (br.x2 + A, br.y2)))
    segs.append(((br.x2, br.y2), (br.x2, br.y2 + A)))

    # --- センタートンボ (4 辺中央の十字) ---
    cx_mid = (fr.x + fr.x2) * 0.5
    cy_mid = (fr.y + fr.y2) * 0.5
    half = center_size_mm * 0.5
    gap = center_gap_mm
    # 上辺中央: 裁ち落とし枠の上側に + 字
    cy_top = br.y2 + gap + half
    segs.append(((cx_mid, cy_top - half), (cx_mid, cy_top + half)))
    segs.append(((cx_mid - half, cy_top), (cx_mid + half, cy_top)))
    # 下辺中央
    cy_bot = br.y - gap - half
    segs.append(((cx_mid, cy_bot - half), (cx_mid, cy_bot + half)))
    segs.append(((cx_mid - half, cy_bot), (cx_mid + half, cy_bot)))
    # 左辺中央
    cx_left = br.x - gap - half
    segs.append(((cx_left, cy_mid - half), (cx_left, cy_mid + half)))
    segs.append(((cx_left - half, cy_mid), (cx_left + half, cy_mid)))
    # 右辺中央
    cx_right = br.x2 + gap + half
    segs.append(((cx_right, cy_mid - half), (cx_right, cy_mid + half)))
    segs.append(((cx_right - half, cy_mid), (cx_right + half, cy_mid)))

    _draw_line_segments(segs, color, line_width=line_width)


def _draw_frame_with_hole(outer: Rect, inner: Rect, color: tuple[float, float, float, float]) -> None:
    """外側 outer を塗って内側 inner を穴抜きした「額縁」形状を描画."""
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    # 4 本の帯で外周を塗る (上下左右)
    top = Rect(outer.x, inner.y2, outer.width, outer.y2 - inner.y2)
    bottom = Rect(outer.x, outer.y, outer.width, inner.y - outer.y)
    left = Rect(outer.x, inner.y, inner.x - outer.x, inner.height)
    right = Rect(inner.x2, inner.y, outer.x2 - inner.x2, inner.height)
    verts: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []
    for r in (top, bottom, left, right):
        if r.width <= 0 or r.height <= 0:
            continue
        base = len(verts)
        verts.extend(
            [
                (mm_to_m(r.x), mm_to_m(r.y), 0.0),
                (mm_to_m(r.x2), mm_to_m(r.y), 0.0),
                (mm_to_m(r.x2), mm_to_m(r.y2), 0.0),
                (mm_to_m(r.x), mm_to_m(r.y2), 0.0),
            ]
        )
        indices.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])
    if not verts:
        return
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


# ---------- draw_handler 本体 ----------


def _apply_blend_mode(_mode: str) -> None:
    """ブレンドモード指定を GPU state に反映する (Phase 1 暫定).

    GPU state API の ``blend_set`` には MULTIPLY 相当が無く、現段階では
    すべて ALPHA ブレンドにフォールバックする。正確な乗算合成は Phase 6 の
    書き出しパイプライン (Pillow) で実装する。
    """
    gpu.state.blend_set("ALPHA")


def _draw_image_layers(scene) -> None:
    """画像レイヤーを gpu.texture 経由でオーバーレイ描画.

    Blender 4.x では gpu.texture モジュール + IMAGE ビルトインシェーダを
    使うが、ここでは最も簡単な方法として bpy.types.Image.gl_load() が
    無い環境を想定し、Blender が自動で管理する Image から
    gpu.texture.from_image を取得する。
    """
    coll = getattr(scene, "bname_image_layers", None)
    if coll is None or not len(coll):
        return
    if not _HAS_GPU_TEXTURE:
        return
    shader = gpu.shader.from_builtin("IMAGE")
    for entry in coll:
        if not entry.visible or not entry.filepath:
            continue
        img = _ensure_bpy_image(entry.filepath)
        if img is None:
            continue
        try:
            tex = gpu_texture.from_image(img)
        except Exception:  # noqa: BLE001
            continue
        # 矩形 (mm) を Blender unit に変換して頂点を組む
        x0 = mm_to_m(entry.x_mm)
        y0 = mm_to_m(entry.y_mm)
        x1 = mm_to_m(entry.x_mm + entry.width_mm)
        y1 = mm_to_m(entry.y_mm + entry.height_mm)
        # flip 対応
        u0, u1 = (1.0, 0.0) if entry.flip_x else (0.0, 1.0)
        v0, v1 = (1.0, 0.0) if entry.flip_y else (0.0, 1.0)
        verts = [(x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0)]
        uvs = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        indices = [(0, 1, 2), (0, 2, 3)]
        batch = batch_for_shader(
            shader,
            "TRIS",
            {"pos": verts, "texCoord": uvs},
            indices=indices,
        )
        shader.bind()
        shader.uniform_sampler("image", tex)
        gpu.state.blend_set("ALPHA")
        batch.draw(shader)


def _ensure_bpy_image(filepath: str):
    """bpy.data.images に対象画像を読み込み (check_existing でキャッシュ)."""
    if not filepath:
        return None
    try:
        # check_existing=True は filepath で既存画像を再利用する。basename での
        # 自前ルックアップは同名異パスで誤判定するので使わない。
        return bpy.data.images.load(bpy.path.abspath(filepath), check_existing=True)
    except Exception:  # noqa: BLE001
        return None


def _draw_balloons(page, ox_mm: float = 0.0, oy_mm: float = 0.0) -> None:
    """ページ内のフキダシをオーバーレイ描画する."""
    context = bpy.context
    work = get_work(context)
    active_guides = False
    if work is not None and getattr(context.scene, "bname_active_layer_kind", "") == "balloon":
        active_idx = int(getattr(work, "active_page_index", -1))
        if 0 <= active_idx < len(work.pages):
            active_page = work.pages[active_idx]
            active_guides = (
                active_page == page
                or str(getattr(active_page, "id", "") or "")
                == str(getattr(page, "id", "") or "")
            )
    overlay_balloon.draw_balloons(
        page,
        ox_mm=ox_mm,
        oy_mm=oy_mm,
        draw_rect_outline=_draw_rect_outline,
        draw_polygon_fill=_draw_polygon_fill,
        draw_polyline_loop=_draw_polyline_loop,
        is_entry_visible=lambda entry: overlay_visibility.entry_in_visible_panel(page, entry),
        active=active_guides,
    )


def _draw_polygon_fill(pts: list[tuple[float, float]], color) -> None:
    if len(pts) < 3:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in pts]
    indices = [(0, i, i + 1) for i in range(1, len(pts) - 1)]
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_stroke_band_fill(
    outer_pts: list[tuple[float, float]],
    inner_pts: list[tuple[float, float]],
    color,
) -> None:
    if len(outer_pts) < 3 or len(inner_pts) != len(outer_pts):
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in outer_pts + inner_pts]
    n = len(outer_pts)
    indices: list[tuple[int, int, int]] = []
    for i in range(n):
        j = (i + 1) % n
        indices.append((i, j, n + j))
        indices.append((i, n + j, n + i))
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_polyline_loop(pts: list[tuple[float, float]], color, line_width: float = 1.0) -> None:
    if len(pts) < 2:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in pts] + [(mm_to_m(pts[0][0]), mm_to_m(pts[0][1]), 0.0)]
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    try:
        gpu.state.line_width_set(max(1.0, float(line_width)))
        batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.0)


def _resolve_active_region(context):
    """draw_handler 用に WINDOW region と rv3d を確実に取得.

    Blender 5.x の POST_VIEW callback 内では ``context.region`` /
    ``context.region_data`` が None になるケースがあるため、
    context.area からスキャンして WINDOW region と rv3d を取得する fallback。
    """
    region = getattr(context, "region", None)
    rv3d = getattr(context, "region_data", None)
    if region is not None and rv3d is not None and getattr(region, "type", "") == "WINDOW":
        return region, rv3d
    area = getattr(context, "area", None)
    if area is None or area.type != "VIEW_3D":
        # 全 screen を巡回して最初の VIEW_3D area を探す (callback 中の area が
        # 別タイプだった場合のフォールバック)
        screen = getattr(context, "screen", None)
        if screen is not None:
            for a in screen.areas:
                if a.type == "VIEW_3D":
                    area = a
                    break
    if area is None:
        return None, None
    found_region = None
    for r in area.regions:
        if r.type == "WINDOW":
            found_region = r
            break
    if found_region is None:
        return None, None
    space = area.spaces.active
    found_rv3d = getattr(space, "region_3d", None)
    return found_region, found_rv3d


def _draw_text_in_rect(context, rect, entry_or_text, color=(0, 0, 0, 1)) -> None:
    """``rect`` (mm) の中にテキストレイヤーを blf で描画する."""
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector

    region, rv3d = _resolve_active_region(context)
    if region is None or rv3d is None:
        return
    font_id = _get_jp_font_id()

    if isinstance(entry_or_text, str):
        text = entry_or_text
        world = Vector((mm_to_m(rect.x + 1.0), mm_to_m(rect.y2 - 1.0), 0.0))
        coord = location_3d_to_region_2d(region, rv3d, world)
        if coord is None:
            return
        try:
            blf.size(font_id, 14.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            blf.color(font_id, color[0], color[1], color[2], color[3])
        except Exception:  # noqa: BLE001
            pass
        try:
            _, th = blf.dimensions(font_id, text)
        except Exception:  # noqa: BLE001
            th = 14.0
        blf.position(font_id, float(coord.x), float(coord.y) - th, 0.0)
        blf.draw(font_id, text)
        return

    entry = entry_or_text
    padded = rect.inset(1.0)
    if padded.width <= 0.0 or padded.height <= 0.0:
        padded = rect
    try:
        from ..typography import layout as text_layout

        result = text_layout.typeset(
            entry,
            padded.x,
            padded.y,
            padded.width,
            padded.height,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("text layout failed")
        return

    o0 = location_3d_to_region_2d(region, rv3d, Vector((0.0, 0.0, 0.0)))
    o1 = location_3d_to_region_2d(region, rv3d, Vector((mm_to_m(1.0), 0.0, 0.0)))
    if o0 is not None and o1 is not None:
        px_per_mm = abs(float(o1.x) - float(o0.x))
    else:
        px_per_mm = 3.78
    entry_color = getattr(entry, "color", color)
    try:
        blf.color(
            font_id,
            float(entry_color[0]),
            float(entry_color[1]),
            float(entry_color[2]),
            float(entry_color[3]),
        )
    except Exception:  # noqa: BLE001
        pass
    for glyph in result.placements:
        glyph_font_id = _get_font_id_for_path(text_style.font_for_index(entry, glyph.index))
        coord = location_3d_to_region_2d(
            region,
            rv3d,
            Vector((mm_to_m(glyph.x_mm), mm_to_m(glyph.y_mm), 0.0)),
        )
        if coord is None:
            continue
        size_px = glyph.size_pt * px_per_mm * 25.4 / 72.0
        try:
            blf.size(glyph_font_id, max(1, int(size_px)))
        except Exception:  # noqa: BLE001
            pass
        try:
            blf.color(
                glyph_font_id,
                float(entry_color[0]),
                float(entry_color[1]),
                float(entry_color[2]),
                float(entry_color[3]),
            )
        except Exception:  # noqa: BLE001
            pass
        blf.position(glyph_font_id, float(coord.x), float(coord.y), 0.0)
        blf.draw(glyph_font_id, glyph.ch)


def _draw_panels(
    work,
    page,
    ox_mm: float = 0.0,
    oy_mm: float = 0.0,
    *,
    skip_preview_stem: str = "",
) -> None:
    """ページ内のコマ枠・白フチを Z 順に従って描画.

    Z順序昇順 (背面→手前) で描画することで重なり時も正しく表示される。
    rect / polygon の両形状をサポート (枠線カット後は polygon になる)。
    自動くり抜きは Phase 2 段階では未実装。
    """
    active_stem = ""
    scene = getattr(bpy.context, "scene", None)
    active_kind = getattr(scene, "bname_active_layer_kind", "") if scene is not None else ""
    active_page_idx = int(getattr(work, "active_page_index", -1))
    active_page = work.pages[active_page_idx] if 0 <= active_page_idx < len(work.pages) else None
    wm = getattr(bpy.context, "window_manager", None)
    edge_selection_matches = False
    if wm is not None and getattr(wm, "bname_edge_select_kind", "none") in {"edge", "border", "vertex"}:
        edge_selection_matches = (
            int(getattr(wm, "bname_edge_select_page", -1)) == active_page_idx
            and active_page is page
            and int(getattr(wm, "bname_edge_select_panel", -1))
            == int(getattr(page, "active_panel_index", -1))
        )
    is_active_page = (
        active_kind == "panel"
        and not edge_selection_matches
        and active_page is not None
        and str(getattr(active_page, "id", "") or "") == str(getattr(page, "id", "") or "")
    )
    if is_active_page:
        active_idx = int(getattr(page, "active_panel_index", -1))
        if 0 <= active_idx < len(page.panels):
            active_stem = str(getattr(page.panels[active_idx], "panel_stem", "") or "")
    sorted_panels = sorted(page.panels, key=lambda p: p.z_order)
    for entry in sorted_panels:
        if not overlay_visibility.panel_visible(entry):
            continue
        # ポリゴン頂点リスト (mm) を取得 — rect なら 4 隅、polygon なら vertices
        if entry.shape_type == "rect":
            poly = [
                (entry.rect_x_mm, entry.rect_y_mm),
                (entry.rect_x_mm + entry.rect_width_mm, entry.rect_y_mm),
                (entry.rect_x_mm + entry.rect_width_mm,
                 entry.rect_y_mm + entry.rect_height_mm),
                (entry.rect_x_mm, entry.rect_y_mm + entry.rect_height_mm),
            ]
        elif entry.shape_type == "polygon" and len(entry.vertices) >= 3:
            poly = [(v.x_mm, v.y_mm) for v in entry.vertices]
        else:
            continue
        # ページオフセットを加算
        if ox_mm != 0.0 or oy_mm != 0.0:
            poly = [(x + ox_mm, y + oy_mm) for x, y in poly]
        bg = getattr(entry, "background_color", None)
        if bg is not None and len(bg) >= 4 and float(bg[3]) > 0.0:
            _draw_polygon_fill(
                poly,
                (float(bg[0]), float(bg[1]), float(bg[2]), float(bg[3])),
            )
        if getattr(entry, "panel_stem", "") != skip_preview_stem:
            panel_preview_overlay.draw_panel_preview(
                work, page, entry, ox_mm=ox_mm, oy_mm=oy_mm
            )
        # 白フチ (枠線の外側) — 矩形のみ簡易対応 (polygon は外接矩形で近似)
        wm = entry.white_margin
        if wm.enabled and wm.width_mm > 0.0:
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            outer = Rect(
                min(xs) - wm.width_mm, min(ys) - wm.width_mm,
                (max(xs) - min(xs)) + 2 * wm.width_mm,
                (max(ys) - min(ys)) + 2 * wm.width_mm,
            )
            color = (
                float(wm.color[0]), float(wm.color[1]),
                float(wm.color[2]), float(wm.color[3]),
            )
            _draw_rect_fill(outer, color)
        is_active_panel = (
            bool(active_stem)
            and str(getattr(entry, "panel_stem", "") or "") == active_stem
        )
        # 枠線 (mm 単位の太さ = ズーム連動、紙に追従)
        # 辺ごとに描画 (edge_styles に個別 override があればそれを優先)
        b = entry.border
        if b.visible:
            base_color = (
                float(b.color[0]), float(b.color[1]),
                float(b.color[2]), float(b.color[3]),
            )
            base_width = max(0.1, float(b.width_mm))
            rect_edge_override = (
                getattr(b.edge_top, "use_override", False)
                or getattr(b.edge_right, "use_override", False)
                or getattr(b.edge_bottom, "use_override", False)
                or getattr(b.edge_left, "use_override", False)
            )
            if (
                len(entry.edge_styles) == 0
                and not rect_edge_override
                and getattr(b, "style", "solid") == "solid"
            ):
                path = border_geom.styled_closed_path_mm(
                    poly,
                    getattr(b, "corner_type", "square"),
                    float(getattr(b, "corner_radius_mm", 0.0)),
                )
                loops = border_geom.stroke_loops_mm(path, base_width)
                if loops is not None:
                    outer_loop, inner_loop = loops
                    _draw_stroke_band_fill(outer_loop, inner_loop, base_color)
                    if is_active_panel:
                        segs = [
                            (poly[i], poly[(i + 1) % len(poly)])
                            for i in range(len(poly))
                        ]
                        _draw_segments_mm(segs, viewport_colors.SELECTION_STRONG, width_mm=1.20)
                    continue
            # edge_styles を index 辞書化
            override_map = {int(s.edge_index): s for s in entry.edge_styles}
            n = len(poly)
            for i in range(n):
                seg = (poly[i], poly[(i + 1) % n])
                style = override_map.get(i)
                if style is not None:
                    color = (
                        float(style.color[0]), float(style.color[1]),
                        float(style.color[2]), float(style.color[3]),
                    )
                    w = max(0.1, float(style.width_mm))
                else:
                    color = base_color
                    w = base_width
                _draw_segments_mm([seg], color, width_mm=w)
        if is_active_panel:
            segs = [
                (poly[i], poly[(i + 1) % len(poly)])
                for i in range(len(poly))
            ]
            _draw_segments_mm(segs, viewport_colors.SELECTION_STRONG, width_mm=1.20)


def _translate_rect(r: Rect, ox_mm: float, oy_mm: float) -> Rect:
    """Rect を (ox_mm, oy_mm) だけ平行移動."""
    if ox_mm == 0.0 and oy_mm == 0.0:
        return r
    return Rect(r.x + ox_mm, r.y + oy_mm, r.width, r.height)


def _draw_canvas_fill_only(paper, rects, ox_mm: float, oy_mm: float) -> None:
    """キャンバス塗りのみを表示用 overlay として描画する.

    `paper_color` は Blender COLOR プロパティなので scene-linear 値。
    GPU overlay では UI 表示相当の sRGB に戻し、不透明 (alpha=1.0) で
    描く。深度テストを有効にして、GP ストロークやレイヤー表示の背後に
    入るようにする。
    """
    canvas_r = _translate_rect(rects.canvas, ox_mm, oy_mm)
    r, g, b = color_space.linear_to_srgb_rgb(paper.paper_color[:3])
    canvas_color = (
        r,
        g,
        b,
        1.0,
    )
    try:
        gpu.state.depth_test_set("LESS_EQUAL")
        _draw_rect_fill(canvas_r, canvas_color)
    finally:
        try:
            gpu.state.depth_test_set("NONE")
        except Exception:  # noqa: BLE001
            pass


def _draw_page_overlay(
    context,
    work,
    paper,
    rects,
    page,
    mode: str,
    ox_mm: float = 0.0,
    oy_mm: float = 0.0,
    draw_image_layers: bool = True,
    is_left_half: bool = False,
) -> None:
    """1 ページ分のガイド/コマ枠を (ox_mm, oy_mm) オフセットで描画.

    ``is_left_half=True`` (見開きの左半分のページ) の場合、ノド/小口/
    inner_frame 横オフセットを左右反転して再計算する。
    """
    if not overlay_visibility.page_visible(page):
        return
    # is_left_half が True の場合は per-page で rects を再計算 (左右反転対応)
    if is_left_half:
        rects = overlay_shared.compute_paper_rects(paper, is_left_half=True)
    canvas_r = _translate_rect(rects.canvas, ox_mm, oy_mm)
    finish_r = _translate_rect(rects.finish, ox_mm, oy_mm)
    inner_r = _translate_rect(rects.inner_frame, ox_mm, oy_mm)
    safe_r = _translate_rect(rects.safe, ox_mm, oy_mm)
    bleed_r = _translate_rect(rects.bleed, ox_mm, oy_mm)

    _draw_canvas_fill_only(paper, rects, ox_mm, oy_mm)

    # セーフライン外オーバーレイ (全ページに表示)
    # 仕様: 常に乗算合成相当 + alpha 100%。
    # Blender 5.x の gpu.state.blend_set("MULTIPLY") は受理されるが期待通り
    # 動かないため、ALPHA で「色付き半透明」描画して乗算同等の見た目を出す。
    # sa.color は Blender の COLOR プロパティなので scene-linear 値。
    # UI表示相当の sRGB に戻してから alpha を色の暗さ (1 - brightness)
    # に連動させる。これにより:
    #   - (0.7, 0.7, 0.7) グレー: alpha=0.3 → 紙の白に薄灰 = 30% 暗いグレー
    #   - (1, 0, 0) 赤:           alpha=0.67 → 赤フィルタ
    #   - (0, 0, 1) 青:           alpha=0.67 → 青フィルタ
    #   - (0, 0, 0) 黒:           alpha=1.0 → 完全に黒
    # 暗い色ほどフィルタが強く、明るい色ほど薄く出る (乗算らしい挙動)。
    sa = work.safe_area_overlay
    if sa.enabled:
        gpu.state.blend_set("ALPHA")
        r, g, b = color_space.linear_to_srgb_rgb(sa.color[:3])
        brightness = (r + g + b) / 3.0
        alpha = max(0.0, min(1.0, 1.0 - brightness))
        color = (r, g, b, alpha)
        _draw_frame_with_hole(canvas_r, safe_r, color)

    # 枠線群はビューポート上で常に 1px 表示にする。
    if getattr(paper, "show_canvas_frame", True):
        _draw_rect_outline(canvas_r, viewport_colors.PAPER_GUIDE_DIM, line_width=1.0)
    # 裁ち落とし枠 (= 仕上がり枠 + 裁ち落とし幅)
    if paper.bleed_mm > 0.0 and getattr(paper, "show_bleed_frame", True):
        _draw_rect_outline(bleed_r, viewport_colors.PAPER_GUIDE_DIM, line_width=1.0)
    if getattr(paper, "show_finish_frame", True):
        _draw_rect_outline(finish_r, viewport_colors.PAPER_GUIDE_LIGHT, line_width=1.0)
    if getattr(paper, "show_inner_frame", True):
        _draw_rect_outline(inner_r, viewport_colors.PAPER_GUIDE, line_width=1.0)
    if getattr(paper, "show_safe_line", True):
        _draw_rect_outline(safe_r, viewport_colors.SAFE_LINE, line_width=1.0)
    # トンボ (四隅 + 各辺中央センタートンボ) を仕上がり枠 / 裁ち落とし枠基準で描画
    if paper.bleed_mm > 0.0 and getattr(paper, "show_trim_marks", True):
        _draw_trim_marks(finish_r, bleed_r)

    # 画像レイヤー (アクティブページのみ — 全ページ一覧時は負荷とレイヤーの per-scene 制約で省略)
    if mode == MODE_PAGE and draw_image_layers:
        _draw_image_layers(context.scene)

    # コマ枠 / フキダシ / テキスト。panel モードでは参照表示として描く。
    if mode in (MODE_PAGE, MODE_PANEL) and page is not None:
        skip_stem = ""
        if mode == MODE_PANEL:
            skip_stem = getattr(context.scene, "bname_current_panel_stem", "")
        _draw_panels(work, page, ox_mm=ox_mm, oy_mm=oy_mm, skip_preview_stem=skip_stem)
        _draw_balloons(page, ox_mm=ox_mm, oy_mm=oy_mm)
        active_text_guides = False
        if getattr(context.scene, "bname_active_layer_kind", "") == "text":
            active_idx = int(getattr(work, "active_page_index", -1))
            if 0 <= active_idx < len(work.pages):
                active_page = work.pages[active_idx]
                active_text_guides = (
                    active_page == page
                    or str(getattr(active_page, "id", "") or "")
                    == str(getattr(page, "id", "") or "")
                )
        overlay_text.draw_text_guides(
            page,
            context=context,
            ox_mm=ox_mm,
            oy_mm=oy_mm,
            active=active_text_guides,
            entry_visible=lambda entry: overlay_visibility.entry_in_visible_panel(page, entry),
            draw_rect_fill=_draw_rect_fill,
            draw_rect_outline=_draw_rect_outline,
        )

    # NOTE: 作品情報の blf 描画は POST_VIEW では効かないため _draw_callback_pixel
    # (POST_PIXEL handler) で別途実行する。ここでは呼ばない。


def _resolve_page_index(work, ox_mm: float, oy_mm: float) -> int:
    """ox/oy オフセットからページ index を逆引き (overview 描画時の各ページ向け).

    ox=oy=0 ならアクティブページ index、それ以外なら overview の grid から逆引き。
    対応するページが見つからなければ -1。
    """
    if ox_mm == 0.0 and oy_mm == 0.0:
        return work.active_page_index
    paper = work.paper
    cw = paper.canvas_width_mm
    ch = paper.canvas_height_mm
    if cw <= 0 or ch <= 0:
        return work.active_page_index
    from ..utils.page_grid import page_grid_offset_mm as _pg_offset
    cols = max(1, int(getattr(bpy.context.scene, "bname_overview_cols", 4)))
    gap = float(getattr(bpy.context.scene, "bname_overview_gap_mm", 30.0))
    start_side = getattr(paper, "start_side", "right")
    read_direction = getattr(paper, "read_direction", "left")
    eps = 0.5  # mm 単位の許容誤差
    for i in range(len(work.pages)):
        ox_i, oy_i = _pg_offset(
            i, cols, gap, cw, ch, start_side, read_direction
        )
        ox_i, oy_i = _with_page_manual_offset(work, i, ox_i, oy_i)
        if abs(ox_i - ox_mm) < eps and abs(oy_i - oy_mm) < eps:
            return i
    return -1


def _with_page_manual_offset(work, page_index: int, ox_mm: float, oy_mm: float):
    try:
        from ..utils import page_grid

        page = work.pages[page_index] if 0 <= page_index < len(work.pages) else None
        add_x, add_y = page_grid.page_manual_offset_mm(page)
        return ox_mm + add_x, oy_mm + add_y
    except Exception:  # noqa: BLE001
        return ox_mm, oy_mm


def _page_overview_offset(
    context,
    work,
    page_index: int,
    cols: int,
    gap: float,
    cw: float,
    ch: float,
    start_side: str,
    read_direction: str,
    *,
    is_page_browser: bool,
) -> tuple[float, float]:
    if is_page_browser and page_browser.fit_enabled(context.scene):
        return page_browser.page_offset_mm(
            work,
            context.scene,
            getattr(context, "area", None),
            page_index,
        )
    from ..utils.page_grid import page_grid_offset_mm as _pg_offset

    ox, oy = _pg_offset(page_index, cols, gap, cw, ch, start_side, read_direction)
    return _with_page_manual_offset(work, page_index, ox, oy)


def _draw_work_info_texts(
    context, work, rects, page_index: int, ox_mm: float, oy_mm: float,
) -> None:
    """作品情報・ページ番号を blf で原稿上 (基本枠基準) に描画.

    描画項目: 作品名 / 話数 / サブタイトル / 作者名 / ページ番号。
    各項目は ``BNameDisplayItem.position`` の 9 通り (top-left 等) に配置し、
    キャンバス枠ではなく **基本枠 (inner_frame)** を基準に内側 2mm の余白で
    アンカーする。これによりセーフライン内に収まりやすくなる。
    """
    info = getattr(work, "work_info", None)
    if info is None:
        return
    inner = _translate_rect(rects.inner_frame, ox_mm, oy_mm)

    # ページ番号文字列の組み立て (開始番号 + page_index、4 桁ゼロ埋め "ページNNNN")
    page_text = ""
    if 0 <= page_index < len(work.pages):
        try:
            start = int(info.page_number_start)
        except Exception:  # noqa: BLE001
            start = 1
        page_text = f"ページ{start + page_index:04d}"

    items = [
        (info.display_work_name, info.work_name),
        (info.display_episode,
         f"第{info.episode_number}話" if info.episode_number else ""),
        (info.display_subtitle, info.subtitle),
        (info.display_author, info.author),
        (info.display_page_number, page_text),
    ]
    for item, text in items:
        if item is None or not item.enabled or not text:
            continue
        _draw_text_at_position(context, inner, item, text)


def _draw_text_at_position(context, anchor_rect, item, text: str) -> None:
    """``anchor_rect`` (mm, 裁ち落とし枠) の position に ``text`` を **枠外** に blf 描画.

    6 通りの position に対し、文字を裁ち落とし枠の外側に押し出す:
      - top-*    : 裁ち落とし枠の上、内側に文字下端が貼り付く
      - bottom-* : 裁ち落とし枠の下、内側に文字上端が貼り付く

    Blender の blf は screen pixel 座標を要求するため、まず world 座標
    (Blender unit, mm の 0.001 倍) に換算し、``location_3d_to_region_2d`` で
    ピクセル座標化する。region/rv3d が取れない場合は黙って no-op。
    """
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector

    region, rv3d = _resolve_active_region(context)
    if region is None or rv3d is None:
        return

    pad = 2.0  # 仕上がり枠と文字の隙間 (mm)
    pos = item.position

    # X 方向のアンカー (mm)
    if pos == "middle-left":
        x_mm = anchor_rect.x - pad
    elif pos == "middle-right":
        x_mm = anchor_rect.x2 + pad
    elif pos.endswith("left"):
        x_mm = anchor_rect.x
    elif pos.endswith("right"):
        x_mm = anchor_rect.x2
    else:
        x_mm = (anchor_rect.x + anchor_rect.x2) * 0.5

    # Y 方向のアンカー (mm)
    if pos.startswith("top"):
        y_mm = anchor_rect.y2 + pad
    elif pos.startswith("bottom"):
        y_mm = anchor_rect.y - pad
    else:
        y_mm = (anchor_rect.y + anchor_rect.y2) * 0.5

    world = Vector((mm_to_m(x_mm), mm_to_m(y_mm), 0.0))
    coord = location_3d_to_region_2d(region, rv3d, world)
    if coord is None:
        return
    # 画面外なら描画スキップ (blf を呼んでも見えないだけだが、無駄なので)
    if not (-200 < coord.x < region.width + 200
            and -200 < coord.y < region.height + 200):
        return

    # フォントサイズ (Q 数 = 0.25mm 単位) → 画面 px。ズームに連動するよう
    # 現在のビューポートで「1 mm が画面で何 px か」を実測してかける。
    from ..utils.geom import q_to_mm
    o0 = location_3d_to_region_2d(region, rv3d, Vector((0.0, 0.0, 0.0)))
    o1 = location_3d_to_region_2d(region, rv3d, Vector((mm_to_m(1.0), 0.0, 0.0)))
    if o0 is not None and o1 is not None:
        px_per_mm = abs(float(o1.x) - float(o0.x))
    else:
        px_per_mm = 3.78  # 96dpi 相当の概算 fallback
    size_mm = q_to_mm(float(item.font_size_q))
    size_px = max(6, int(size_mm * max(px_per_mm, 0.1)))
    font_id = _get_jp_font_id()
    try:
        blf.size(font_id, size_px)
    except Exception:  # noqa: BLE001
        pass
    color = (
        float(item.color[0]),
        float(item.color[1]),
        float(item.color[2]),
        float(item.color[3]),
    )
    try:
        blf.color(font_id, *color)
    except Exception:  # noqa: BLE001
        pass

    try:
        tw, th = blf.dimensions(font_id, text)
    except Exception:  # noqa: BLE001
        tw, th = 0.0, 0.0
    sx, sy = float(coord.x), float(coord.y)

    # 水平アライメント (text のどの位置を anchor x に合わせるか)
    if pos == "middle-left":
        # text 右端を anchor x に揃える (枠の左に張り出す)
        sx -= tw
    elif pos == "middle-right":
        # text 左端を anchor x に揃える (枠の右に張り出す) → sx そのまま
        pass
    elif pos.endswith("right"):
        sx -= tw
    elif pos.endswith("center"):
        sx -= tw * 0.5
    # top-left / bottom-left は sx そのまま (text 左端 = anchor x)

    # 垂直アライメント (text のどの位置を anchor y に合わせるか)
    if pos.startswith("bottom"):
        # 仕上がり枠の下に置くため、text 上端を anchor y に揃える
        sy -= th
    elif pos.startswith("middle"):
        sy -= th * 0.5
    # top-* は baseline = anchor y (text は上方向に伸びる)

    blf.position(font_id, sx, sy, 0.0)
    blf.draw(font_id, text)


def _format_page_header_number(page_index: int, work=None) -> str:
    """作品の開始番号に従って、ページ番号を 001 形式にする。"""
    try:
        start = int(getattr(getattr(work, "work_info", None), "page_number_start", 1))
    except Exception:  # noqa: BLE001
        start = 1
    return f"{max(0, start + int(page_index)):03d}"


def _draw_bold_pixel_text(
    font_id: int,
    text: str,
    x_px: float,
    y_px: float,
    *,
    color: tuple[float, float, float, float],
    outline_color: tuple[float, float, float, float],
) -> None:
    """blf に太字指定が無い環境でも、重ね描きで太字風に表示する。"""
    outline_offsets = (
        (-2.0, -2.0), (-2.0, 0.0), (-2.0, 2.0),
        (0.0, -2.0), (0.0, 2.0),
        (2.0, -2.0), (2.0, 0.0), (2.0, 2.0),
    )
    try:
        blf.color(font_id, *outline_color)
    except Exception:  # noqa: BLE001
        pass
    for dx, dy in outline_offsets:
        blf.position(font_id, x_px + dx, y_px + dy, 0.0)
        blf.draw(font_id, text)

    bold_offsets = ((0.0, 0.0), (0.9, 0.0), (0.0, 0.9), (0.9, 0.9))
    try:
        blf.color(font_id, *color)
    except Exception:  # noqa: BLE001
        pass
    for dx, dy in bold_offsets:
        blf.position(font_id, x_px + dx, y_px + dy, 0.0)
        blf.draw(font_id, text)


def _draw_page_header_number_pixel(
    context,
    paper,
    page_index: int,
    ox_mm: float,
    oy_mm: float,
) -> None:
    """ページキャンバス上端の外側に 001 形式の大きな番号を描画する。"""
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector

    region, rv3d = _resolve_active_region(context)
    if region is None or rv3d is None:
        return
    rects = overlay_shared.compute_paper_rects(paper)
    x_mm = rects.canvas.x + rects.canvas.width * 0.5 + ox_mm
    y_mm = rects.canvas.y2 + _PAGE_HEADER_GAP_MM + oy_mm
    coord = location_3d_to_region_2d(
        region,
        rv3d,
        Vector((mm_to_m(x_mm), mm_to_m(y_mm), 0.0)),
    )
    if coord is None:
        return
    if not (-300 < coord.x < region.width + 300 and -300 < coord.y < region.height + 300):
        return

    text = _format_page_header_number(page_index, get_work(context))
    font_id = _get_jp_font_id()
    try:
        blf.size(font_id, _PAGE_HEADER_FONT_SIZE_PX)
    except Exception:  # noqa: BLE001
        pass
    try:
        tw, th = blf.dimensions(font_id, text)
    except Exception:  # noqa: BLE001
        tw, th = 0.0, float(_PAGE_HEADER_FONT_SIZE_PX)
    sx = float(coord.x) - tw * 0.5
    sy = float(coord.y) - th * 0.5
    _draw_bold_pixel_text(
        font_id,
        text,
        sx,
        sy,
        color=_PAGE_HEADER_COLOR,
        outline_color=_PAGE_HEADER_OUTLINE_COLOR,
    )


def _should_highlight_active_page(context) -> bool:
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "bname_active_layer_kind"):
        return True
    return getattr(scene, "bname_active_layer_kind", "") == "page"


def _draw_callback() -> None:
    context = bpy.context
    work = get_work(context)
    if work is None or not work.loaded:
        return
    mode = get_mode(context)
    is_page_browser = page_browser.is_page_browser_area(context)
    if mode == MODE_PANEL and not is_page_browser:
        return
    paper = work.paper
    rects = overlay_shared.compute_paper_rects(paper)
    scene = context.scene

    gpu.state.blend_set("ALPHA")
    try:
        if (
            (
                mode == MODE_PAGE
                and getattr(scene, "bname_overview_mode", False)
            )
            or is_page_browser
        ) and len(work.pages) > 0:
            # 全ページ一覧モード.
            # 日本の漫画は右→左に読むため、ページ 0001 を右端に置き、追加した
            # ページ (0002, 0003...) を左方向に展開する。オフセットは負の X。
            from ..utils.page_grid import (
                is_left_half_page as _is_left_half,
            )

            cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
            gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
            cw = paper.canvas_width_mm
            ch = paper.canvas_height_mm
            start_side = getattr(paper, "start_side", "right")
            read_direction = getattr(paper, "read_direction", "left")
            active_idx = work.active_page_index
            highlight_active_page = _should_highlight_active_page(context)
            for i, page in enumerate(work.pages):
                if not overlay_visibility.page_visible(page):
                    continue
                # 見開き判定込みの式は page_grid 側に集約
                ox, oy = _page_overview_offset(
                    context, work, i, cols, gap, cw, ch,
                    start_side, read_direction, is_page_browser=is_page_browser,
                )
                left_half = _is_left_half(i, start_side, read_direction)
                _draw_page_overlay(
                    context, work, paper, rects, page, mode,
                    ox_mm=ox, oy_mm=oy, draw_image_layers=False,
                    is_left_half=left_half,
                )
                # アクティブページにハイライト枠 (ズーム連動)
                if highlight_active_page and i == active_idx:
                    canvas_r = _translate_rect(rects.canvas, ox, oy)
                    highlight = canvas_r.inset(-5.0)
                    _draw_rect_outline(highlight, viewport_colors.SELECTION, width_mm=1.00)
        elif mode == MODE_PANEL and len(work.pages) > 0:
            from ..utils.page_grid import (
                is_left_half_page as _is_left_half,
            )

            cols = max(2, int(getattr(scene, "bname_overview_cols", 4)))
            gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
            cw = paper.canvas_width_mm
            ch = paper.canvas_height_mm
            start_side = getattr(paper, "start_side", "left")
            read_direction = getattr(paper, "read_direction", "left")
            active_idx = work.active_page_index
            highlight_active_page = _should_highlight_active_page(context)
            for i, page in enumerate(work.pages):
                if not overlay_visibility.page_visible(page):
                    continue
                ox, oy = _page_overview_offset(
                    context, work, i, cols, gap, cw, ch,
                    start_side, read_direction, is_page_browser=is_page_browser,
                )
                left_half = _is_left_half(i, start_side, read_direction)
                _draw_page_overlay(
                    context, work, paper, rects, page, mode,
                    ox_mm=ox, oy_mm=oy, draw_image_layers=False,
                    is_left_half=left_half,
                )
                if highlight_active_page and i == active_idx:
                    canvas_r = _translate_rect(rects.canvas, ox, oy)
                    highlight = canvas_r.inset(-5.0)
                    _draw_rect_outline(highlight, viewport_colors.SELECTION, width_mm=1.00)
        else:
            from ..utils.page_grid import (
                is_left_half_page as _is_left_half,
                page_grid_offset_mm as _pg_offset,
            )
            page = get_active_page(context)
            if page is not None and not overlay_visibility.page_visible(page):
                return
            cols = max(2, int(getattr(scene, "bname_overview_cols", 4)))
            gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
            cw = paper.canvas_width_mm
            ch = paper.canvas_height_mm
            start_side = getattr(paper, "start_side", "right")
            read_direction = getattr(paper, "read_direction", "left")
            idx = work.active_page_index
            # 単ページモードでも active page の内容は grid 位置にあるため、
            # overlay も同じ (ox, oy) で描画して内容と一致させる。
            ox, oy = _pg_offset(
                max(0, idx), cols, gap, cw, ch, start_side, read_direction
            )
            ox, oy = _with_page_manual_offset(work, max(0, idx), ox, oy)
            left_half = _is_left_half(max(0, idx), start_side, read_direction)
            _draw_page_overlay(
                context, work, paper, rects, page, mode,
                ox_mm=ox, oy_mm=oy, draw_image_layers=True,
                is_left_half=left_half,
            )
        overlay_effect_line.draw_active_effect_line_bounds(
            context,
            draw_rect_fill=_draw_rect_fill,
            draw_rect_outline=_draw_rect_outline,
            logger=_logger,
        )
    finally:
        gpu.state.blend_set("NONE")


def apply_bname_shading_mode(context=None) -> int:
    """全ウィンドウの全 VIEW_3D を B-Name のモード別シェーディングに切替.

    B-Name 作品 UI の見え方を統一する目的:
    - 紙の白マテリアルが MatCap や Studio 光源で立体的に陰になるのを防ぎ、
      フラットな印刷物のように描画する
    - 紙面編集: shading.type = "SOLID", shading.light = "FLAT"
    - コマ編集: shading.type = "SOLID", shading.light = "STUDIO"
    - shading.color_type は変更しない (ユーザー設定維持)
    work_new / work_open / load_post から呼ぶ。戻り値は変更したエリア数。
    """
    ctx = context or bpy.context
    wm = ctx.window_manager
    if wm is None:
        return 0
    target_light = "STUDIO" if get_mode(ctx) == MODE_PANEL else "FLAT"
    count = 0
    for window in wm.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            if space is None:
                continue
            shading = getattr(space, "shading", None)
            if shading is None:
                continue
            try:
                if getattr(shading, "type", None) != "SOLID":
                    shading.type = "SOLID"
                    count += 1
                if getattr(shading, "light", None) != target_light:
                    shading.light = target_light
                    count += 1
            except Exception:  # noqa: BLE001
                _logger.exception("apply_bname_shading_mode: set failed")
    return count


def set_viewport_overlays_enabled(context=None, *, enabled: bool) -> int:
    """全ウィンドウの全 VIEW_3D で Blender 標準オーバーレイ表示を切り替える."""
    ctx = context or bpy.context
    wm = getattr(ctx, "window_manager", None)
    if wm is None:
        return 0
    count = 0
    for window in wm.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in getattr(area, "spaces", []):
                if space.type != "VIEW_3D":
                    continue
                overlay = getattr(space, "overlay", None)
                if overlay is None:
                    continue
                try:
                    if bool(getattr(overlay, "show_overlays", True)) != bool(enabled):
                        overlay.show_overlays = bool(enabled)
                        count += 1
                except Exception:  # noqa: BLE001
                    _logger.exception("set_viewport_overlays_enabled: set failed")
            try:
                area.tag_redraw()
            except Exception:  # noqa: BLE001
                pass
    return count


def schedule_viewport_overlays_enabled(*, enabled: bool, retries: int = 6, interval: float = 0.1) -> None:
    """load_post 直後の UI 再構築をまたいでオーバーレイ表示を再適用する."""
    state = {"left": max(1, int(retries))}

    def _tick():
        try:
            set_viewport_overlays_enabled(bpy.context, enabled=enabled)
        except Exception:  # noqa: BLE001
            pass
        state["left"] -= 1
        return interval if state["left"] > 0 else None

    try:
        bpy.app.timers.register(_tick, first_interval=interval)
    except Exception:  # noqa: BLE001
        pass


def reset_viewport_background_to_theme(context=None) -> int:
    """全ウィンドウの全 VIEW_3D の solid shading 背景をテーマ色 (Blender 既定) に戻す.

    旧実装 (apply_paper_background_color) は Blender 自身の solid 背景色を
    paper_color (白) に書き換えていたため、用紙の外側まで真っ白になり
    「ビューポート全体が白」状態を招いていた。現行では用紙領域だけを
    POST_VIEW の最初に不透明塗りし (``_draw_canvas_fill_only``)、
    ビューポート背景はテーマ既定の灰色に保つ。

    過去に白く書き換えられて .blend に保存されているファイルも、ロード時に
    この関数を呼べば自動で灰色 (テーマ既定) に戻る。
    """
    ctx = context or bpy.context
    wm = ctx.window_manager
    if wm is None:
        return 0
    count = 0
    for window in wm.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            if space is None:
                continue
            shading = getattr(space, "shading", None)
            if shading is None:
                continue
            try:
                if getattr(shading, "background_type", None) != "THEME":
                    shading.background_type = "THEME"
                    count += 1
            except Exception:  # noqa: BLE001
                _logger.exception("reset_viewport_background_to_theme: set failed")
    return count


# ---------- register / unregister ----------


def _draw_callback_pixel() -> None:
    """POST_PIXEL: blf テキスト描画 (作品情報・ページ番号・テキスト本文).

    blf は POST_VIEW では view/projection matrix の影響で screen 座標が
    world 座標扱いになり画面外に飛ぶ。POST_PIXEL では Blender が pixel
    空間に matrix を切り替えて呼び出すので blf.draw が期待通り動く。
    """
    context = bpy.context
    work = get_work(context)
    if work is None or not work.loaded:
        return
    paper = work.paper
    rects = overlay_shared.compute_paper_rects(paper)
    mode = get_mode(context)
    scene = context.scene
    is_page_browser = page_browser.is_page_browser_area(context)

    if mode != MODE_PAGE and not is_page_browser:
        return

    if (
        (
            getattr(scene, "bname_overview_mode", False)
            and mode == MODE_PAGE
        )
        or is_page_browser
    ) and len(work.pages) > 0:
        from ..utils.page_grid import (
            is_left_half_page as _is_left_half,
        )
        cols = max(2, int(getattr(scene, "bname_overview_cols", 4)))
        gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
        cw = paper.canvas_width_mm
        ch = paper.canvas_height_mm
        start_side = getattr(paper, "start_side", "right")
        read_direction = getattr(paper, "read_direction", "left")
        for i, page in enumerate(work.pages):
            if not overlay_visibility.page_visible(page):
                continue
            ox, oy = _page_overview_offset(
                context, work, i, cols, gap, cw, ch,
                start_side, read_direction, is_page_browser=is_page_browser,
            )
            left_half = _is_left_half(i, start_side, read_direction)
            inner = bleed_rect(paper)
            _draw_page_header_number_pixel(context, paper, i, ox, oy)
            _draw_work_info_texts_pixel(context, work, inner, page_index=i,
                                         ox_mm=ox, oy_mm=oy)
            page = work.pages[i] if 0 <= i < len(work.pages) else None
            if page is not None:
                overlay_text.draw_text_pixels(
                    context,
                    page,
                    ox_mm=ox,
                    oy_mm=oy,
                    entry_visible=lambda entry: overlay_visibility.entry_in_visible_panel(page, entry),
                    draw_text_in_rect=_draw_text_in_rect,
                )
    else:
        from ..utils.page_grid import (
            is_left_half_page as _is_left_half,
            page_grid_offset_mm as _pg_offset,
        )
        cols = max(2, int(getattr(scene, "bname_overview_cols", 4)))
        gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
        cw = paper.canvas_width_mm
        ch = paper.canvas_height_mm
        start_side = getattr(paper, "start_side", "right")
        read_direction = getattr(paper, "read_direction", "left")
        idx = max(0, work.active_page_index) if len(work.pages) > 0 else 0
        ox, oy = _pg_offset(idx, cols, gap, cw, ch, start_side, read_direction)
        ox, oy = _with_page_manual_offset(work, idx, ox, oy)
        left_half = _is_left_half(idx, start_side, read_direction)
        inner = bleed_rect(paper)
        page = get_active_page(context)
        if page is not None and overlay_visibility.page_visible(page):
            _draw_page_header_number_pixel(context, paper, idx, ox, oy)
            _draw_work_info_texts_pixel(context, work, inner, page_index=idx,
                                         ox_mm=ox, oy_mm=oy)
            overlay_text.draw_text_pixels(
                context,
                page,
                ox_mm=ox,
                oy_mm=oy,
                entry_visible=lambda entry: overlay_visibility.entry_in_visible_panel(page, entry),
                draw_text_in_rect=_draw_text_in_rect,
            )
    region, rv3d = _resolve_active_region(context)
    overlay_panel_selection.draw(context, work, region, rv3d)


def _draw_work_info_texts_pixel(context, work, inner_rect, page_index: int,
                                 ox_mm: float, oy_mm: float) -> None:
    """POST_PIXEL 版の作品情報描画 (blf のみ)."""
    info = getattr(work, "work_info", None)
    if info is None:
        return
    inner = _translate_rect(inner_rect, ox_mm, oy_mm)

    page_text = ""
    if 0 <= page_index < len(work.pages):
        try:
            start = int(info.page_number_start)
        except Exception:  # noqa: BLE001
            start = 1
        page_text = f"ページ{start + page_index:04d}"

    items = [
        (info.display_work_name, info.work_name),
        (info.display_episode,
         f"第{info.episode_number}話" if info.episode_number else ""),
        (info.display_subtitle, info.subtitle),
        (info.display_author, info.author),
        (info.display_page_number, page_text),
    ]
    for item, text in items:
        if item is None or not item.enabled or not text:
            continue
        _draw_text_at_position(context, inner, item, text)


def register() -> None:
    global _handle, _handle_pixel
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (), "WINDOW", "POST_VIEW"
        )
    if _handle_pixel is None:
        _handle_pixel = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback_pixel, (), "WINDOW", "POST_PIXEL"
        )
    _logger.debug("overlay draw_handlers registered (POST_VIEW + POST_PIXEL)")


def unregister() -> None:
    global _handle, _handle_pixel
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        except (ValueError, RuntimeError):
            pass
        _handle = None
    if _handle_pixel is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle_pixel, "WINDOW")
        except (ValueError, RuntimeError):
            pass
        _handle_pixel = None
    _logger.debug("overlay draw_handlers removed")
