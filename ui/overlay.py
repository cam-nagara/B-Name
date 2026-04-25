"""ビューポート上の原稿オーバーレイ描画 (draw_handler_add + gpu).

計画書 3.4.3a に従い、以下を gpu + blf でオーバーレイ描画する:
- キャンバス (用紙) 枠
- 仕上がり枠 / 基本枠 / セーフライン枠
- セーフライン外側オーバーレイ (乗算)
- ノンブル / 作品情報 (blf)

書き出し結果には焼き込まれない (書き出し時は export_renderer が同じ
overlay_shared ロジックを Pillow で再実装する、Phase 6 で実装)。

座標系:
- 原稿座標は mm 基準 (キャンバス左下が原点)
- Blender ビューポートへの描画は 3D ワールド空間の XY 平面上 (z=0)
- 1 mm = 0.001 Blender unit で配置。カメラリグは Phase 2 で実装予定のため、
  現段階ではワールド XY 平面への配置のみで、カメラがこの平面を写す想定。
"""

from __future__ import annotations

import math
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

from ..core.mode import MODE_PAGE, get_mode
from ..core.work import get_active_page, get_work
from ..utils import log
from ..utils.geom import Rect, bleed_rect, mm_to_m
from . import overlay_shared

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
) -> None:
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
    color: tuple[float, float, float, float] = (0.05, 0.05, 0.05, 0.95),
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


def _panel_rect(entry) -> Rect | None:
    """PanelEntry から描画用の Rect を得る (rect 形状のみ)."""
    if entry.shape_type != "rect":
        # 多角形/曲線は Phase 2.5 以降で実装。現段階は bbox で近似表示する案もあるが、
        # Phase 2 段階ではスキップ。
        return None
    return Rect(
        entry.rect_x_mm,
        entry.rect_y_mm,
        entry.rect_width_mm,
        entry.rect_height_mm,
    )


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
    """ページ内のフキダシをオーバーレイ描画 (形状別 + しっぽ).

    対応形状:
      - rect:           矩形 (角丸オプション込み)
      - ellipse:        楕円 (32 セグメント近似)
      - cloud:          雲 (外周に正弦波)
      - spike_curve:    トゲ (曲線、ノコギリ波 + 滑らか化)
      - spike_straight: トゲ (直線、ノコギリ波)
      - custom / none:  本体描画なし (rect 簡易プレビュー / 無視)

    しっぽ (BNameBalloonTail) は本体に重ねて triangle / curve / sticky で描画。
    """
    balloons = getattr(page, "balloons", None)
    if balloons is None:
        return
    active_idx = getattr(page, "active_balloon_index", -1)
    for i, entry in enumerate(balloons):
        if entry.shape == "none":
            continue
        rect = Rect(
            entry.x_mm + ox_mm,
            entry.y_mm + oy_mm,
            entry.width_mm,
            entry.height_mm,
        )
        # 不透明度 (Meldex opacity 相当). 1.0 が完全不透明、0.0 が透明。
        op = float(getattr(entry, "opacity", 1.0))
        if op <= 0.0:
            continue
        fill = (
            float(entry.fill_color[0]),
            float(entry.fill_color[1]),
            float(entry.fill_color[2]),
            float(entry.fill_color[3]) * op,
        )
        line = (
            float(entry.line_color[0]),
            float(entry.line_color[1]),
            float(entry.line_color[2]),
            float(entry.line_color[3]) * op,
        )
        line_width = max(1.0, float(entry.line_width_mm) * 2.0)

        # 本体形状の輪郭ポリゴンを生成 (mm 座標)
        try:
            outline = _balloon_outline_mm(entry, rect)
        except Exception:  # noqa: BLE001
            outline = _outline_rect(rect)
        # flip / rotate を transforms で適用
        outline = _apply_balloon_transforms(
            outline, rect,
            bool(getattr(entry, "flip_h", False)),
            bool(getattr(entry, "flip_v", False)),
            float(getattr(entry, "rotation_deg", 0.0)),
        )

        # 塗り → 輪郭 (fan で塗る)
        _draw_polygon_fill(outline, fill)
        _draw_polyline_loop(outline, line, line_width=line_width)

        # しっぽ (transforms は本体形状にのみ適用、しっぽは元 rect 基準)
        for tail in getattr(entry, "tails", []):
            _draw_balloon_tail(rect, tail, fill, line, line_width)

        # アクティブハイライト
        if i == active_idx:
            highlight = rect.inset(-1.0)
            _draw_rect_outline(highlight, (1.0, 0.6, 0.0, 0.9), line_width=2.0)


# ---------- フキダシ本体 / しっぽの幾何 ----------


def _outline_rect(rect: Rect) -> list[tuple[float, float]]:
    return [(rect.x, rect.y), (rect.x2, rect.y), (rect.x2, rect.y2), (rect.x, rect.y2)]


def _outline_rounded_rect(rect: Rect, radius_mm: float, segments: int = 8
                          ) -> list[tuple[float, float]]:
    r = max(0.0, min(float(radius_mm), rect.width / 2.0, rect.height / 2.0))
    if r <= 0.0:
        return _outline_rect(rect)
    pts: list[tuple[float, float]] = []
    # 4 隅: 角度 (start, center)
    corners = (
        (rect.x2 - r, rect.y2 - r, 0.0),       # right-top
        (rect.x + r, rect.y2 - r, math.pi * 0.5),
        (rect.x + r, rect.y + r, math.pi),
        (rect.x2 - r, rect.y + r, math.pi * 1.5),
    )
    for cx, cy, a0 in corners:
        for s in range(segments + 1):
            t = a0 + (math.pi * 0.5) * (s / segments)
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return pts


def _outline_ellipse(rect: Rect, segments: int = 64) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = rect.width * 0.5
    ry = rect.height * 0.5
    return [
        (cx + rx * math.cos(2 * math.pi * i / segments),
         cy + ry * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ]


def _outline_cloud(rect: Rect, wave_count: int, amplitude_mm: float,
                   segments_per_wave: int = 6) -> list[tuple[float, float]]:
    """楕円外周に正弦波で凹凸を付けた雲形."""
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = max(1.0, rect.width * 0.5 - amplitude_mm)
    ry = max(1.0, rect.height * 0.5 - amplitude_mm)
    n = max(8, int(wave_count) * max(1, int(segments_per_wave)))
    pts: list[tuple[float, float]] = []
    for i in range(n):
        t = 2 * math.pi * i / n
        bump = amplitude_mm * (0.5 + 0.5 * math.cos(wave_count * t))
        r_mod = 1.0 + bump / max(1.0, min(rx, ry))
        pts.append((cx + rx * math.cos(t) * r_mod, cy + ry * math.sin(t) * r_mod))
    return pts


def _outline_spike(rect: Rect, spike_count: int, depth_mm: float,
                   smooth: bool = False) -> list[tuple[float, float]]:
    """楕円外周にノコギリ波 (spike) を付けたトゲ形.

    smooth=True で頂点を平滑化 (= 曲線トゲ)、False で直線トゲ。
    """
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = max(1.0, rect.width * 0.5)
    ry = max(1.0, rect.height * 0.5)
    n = max(6, int(spike_count) * 2)
    pts: list[tuple[float, float]] = []
    for i in range(n):
        t = 2 * math.pi * i / n
        is_tip = (i % 2) == 0
        r_factor = 1.0 if is_tip else max(0.05, 1.0 - depth_mm / max(rx, ry))
        pts.append((cx + rx * math.cos(t) * r_factor, cy + ry * math.sin(t) * r_factor))
    if smooth and len(pts) >= 3:
        # 隣接点の重み付き平均で 1 段平滑化 (rough but works)
        sm: list[tuple[float, float]] = []
        for i in range(len(pts)):
            p = pts[i]
            pp = pts[(i - 1) % len(pts)]
            pn = pts[(i + 1) % len(pts)]
            sm.append(((pp[0] + 2 * p[0] + pn[0]) * 0.25,
                       (pp[1] + 2 * p[1] + pn[1]) * 0.25))
        pts = sm
    return pts


def _outline_polygon_pct(rect: Rect, pct_pts: list[tuple[float, float]]
                         ) -> list[tuple[float, float]]:
    """clip-path の polygon(% %, …) と同じ書式で mm 座標を生成 (上下反転).

    Meldex は CSS clip-path を使うため Y 軸が下向き。Blender 側は上向き
    なので Y 座標を反転 (1.0 - py) する。
    """
    return [
        (rect.x + (px / 100.0) * rect.width,
         rect.y + ((100.0 - py) / 100.0) * rect.height)
        for px, py in pct_pts
    ]


def _outline_pill(rect: Rect, segments: int = 16) -> list[tuple[float, float]]:
    """ピル (両端半円の長方形). 短辺の半分が半径."""
    r = min(rect.width, rect.height) * 0.5
    if r <= 0:
        return _outline_rect(rect)
    cy = (rect.y + rect.y2) * 0.5
    cx_left = rect.x + r
    cx_right = rect.x2 - r
    pts: list[tuple[float, float]] = []
    # 右半円 (下→上、-π/2 → +π/2)
    for s in range(segments + 1):
        t = -math.pi * 0.5 + math.pi * (s / segments)
        pts.append((cx_right + r * math.cos(t), cy + r * math.sin(t)))
    # 左半円 (上→下、+π/2 → +3π/2)
    for s in range(segments + 1):
        t = math.pi * 0.5 + math.pi * (s / segments)
        pts.append((cx_left + r * math.cos(t), cy + r * math.sin(t)))
    return pts


def _outline_diamond(rect: Rect) -> list[tuple[float, float]]:
    """ダイヤ (4 頂点)."""
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    return [(cx, rect.y2), (rect.x2, cy), (cx, rect.y), (rect.x, cy)]


def _outline_hexagon(rect: Rect) -> list[tuple[float, float]]:
    """六角形 — Meldex clip-path: 25/0,75/0,100/50,75/100,25/100,0/50."""
    return _outline_polygon_pct(rect, [
        (25, 0), (75, 0), (100, 50), (75, 100), (25, 100), (0, 50),
    ])


def _outline_octagon(rect: Rect) -> list[tuple[float, float]]:
    """八角形 — Meldex clip-path: 12/0,88/0,100/12,100/88,88/100,12/100,0/88,0/12."""
    return _outline_polygon_pct(rect, [
        (12, 0), (88, 0), (100, 12), (100, 88),
        (88, 100), (12, 100), (0, 88), (0, 12),
    ])


def _outline_star(rect: Rect) -> list[tuple[float, float]]:
    """5 角星 — Meldex clip-path: 50/0,61/35,98/35,68/57,79/91,50/70,21/91,32/57,2/35,39/35."""
    return _outline_polygon_pct(rect, [
        (50, 0), (61, 35), (98, 35), (68, 57), (79, 91),
        (50, 70), (21, 91), (32, 57), (2, 35), (39, 35),
    ])


def _outline_fluffy(rect: Rect) -> list[tuple[float, float]]:
    """もやもや — Meldex clip-path 16 点の楕円波形."""
    return _outline_polygon_pct(rect, [
        (50, 3), (70, 8), (88, 16), (96, 30), (92, 50), (96, 70),
        (88, 84), (70, 92), (50, 97), (30, 92), (12, 84), (4, 70),
        (8, 50), (4, 30), (12, 16), (30, 8),
    ])


def _balloon_outline_mm(entry, rect: Rect) -> list[tuple[float, float]]:
    """フキダシ entry の本体輪郭ポリゴンを mm 座標で返す.

    Meldex のカード形状 11 種に対応 (rect/ellipse/pill/hexagon/octagon/
    diamond/star/cloud/fluffy/thorn=spike_straight/thorn-curve=spike_curve)。
    """
    sp = entry.shape_params
    s = entry.shape
    if s == "rect":
        if entry.rounded_corner_enabled and entry.rounded_corner_radius_mm > 0.0:
            return _outline_rounded_rect(rect, entry.rounded_corner_radius_mm)
        return _outline_rect(rect)
    if s == "ellipse":
        return _outline_ellipse(rect)
    if s == "pill":
        return _outline_pill(rect)
    if s == "diamond":
        return _outline_diamond(rect)
    if s == "hexagon":
        return _outline_hexagon(rect)
    if s == "octagon":
        return _outline_octagon(rect)
    if s == "star":
        return _outline_star(rect)
    if s == "fluffy":
        return _outline_fluffy(rect)
    if s == "cloud":
        return _outline_cloud(rect, sp.cloud_wave_count, sp.cloud_wave_amplitude_mm)
    if s == "spike_straight":
        return _outline_spike(rect, sp.spike_count, sp.spike_depth_mm, smooth=False)
    if s == "spike_curve":
        return _outline_spike(rect, sp.spike_count, sp.spike_depth_mm, smooth=True)
    return _outline_rect(rect)


def _apply_balloon_transforms(pts: list[tuple[float, float]], rect: Rect,
                              flip_h: bool, flip_v: bool, rotation_deg: float
                              ) -> list[tuple[float, float]]:
    """flip_h/flip_v/rotation_deg を rect 中心基準でポリゴンに適用."""
    if not (flip_h or flip_v or abs(rotation_deg) > 1e-6):
        return pts
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    sx = -1.0 if flip_h else 1.0
    sy = -1.0 if flip_v else 1.0
    cos_r = math.cos(math.radians(rotation_deg))
    sin_r = math.sin(math.radians(rotation_deg))
    out: list[tuple[float, float]] = []
    for x, y in pts:
        # 中心基準
        dx, dy = (x - cx) * sx, (y - cy) * sy
        # 回転
        rx = dx * cos_r - dy * sin_r
        ry = dx * sin_r + dy * cos_r
        out.append((cx + rx, cy + ry))
    return out


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


def _draw_balloon_tail(rect: Rect, tail, fill_color, line_color, line_width: float) -> None:
    """しっぽを本体矩形に重ねて描画.

    - 起点: rect の外周上で direction_deg の方向にある点
    - 終点: 起点から length_mm 進んだ点
    - 形状: type=straight → 三角形、curve → 曲げた三角形、sticky → 矩形タブ
    """
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = rect.width * 0.5
    ry = rect.height * 0.5
    angle = math.radians(float(tail.direction_deg))
    dx, dy = math.cos(angle), math.sin(angle)
    # 楕円外周上の起点
    denom = math.hypot(dx / max(rx, 0.01), dy / max(ry, 0.01))
    base_x = cx + (dx / denom) if denom > 0 else cx
    base_y = cy + (dy / denom) if denom > 0 else cy
    # 終点 (先端)
    tip_x = base_x + dx * tail.length_mm
    tip_y = base_y + dy * tail.length_mm
    # 法線 (左右)
    nx, ny = -dy, dx
    rw = float(tail.root_width_mm) * 0.5
    tw = float(tail.tip_width_mm) * 0.5

    if tail.type == "sticky":
        # 矩形タブ (付箋)
        pts = [
            (base_x + nx * rw, base_y + ny * rw),
            (tip_x + nx * tw if tw > 0 else tip_x, tip_y + ny * tw if tw > 0 else tip_y),
            (tip_x - nx * tw if tw > 0 else tip_x, tip_y - ny * tw if tw > 0 else tip_y),
            (base_x - nx * rw, base_y - ny * rw),
        ]
    elif tail.type == "curve":
        # 曲げた三角形 (中央点を法線方向にずらす)
        bend = float(tail.curve_bend) * tail.length_mm * 0.4
        mid_x = (base_x + tip_x) * 0.5 + nx * bend
        mid_y = (base_y + tip_y) * 0.5 + ny * bend
        pts = [
            (base_x + nx * rw, base_y + ny * rw),
            (mid_x, mid_y),
            (tip_x, tip_y),
            (mid_x, mid_y),
            (base_x - nx * rw, base_y - ny * rw),
        ]
    else:
        # 直線三角形
        pts = [
            (base_x + nx * rw, base_y + ny * rw),
            (tip_x, tip_y),
            (base_x - nx * rw, base_y - ny * rw),
        ]
    _draw_polygon_fill(pts, fill_color)
    _draw_polyline_loop(pts, line_color, line_width=line_width)


def _draw_texts(page, ox_mm: float = 0.0, oy_mm: float = 0.0, context=None) -> None:
    """ページ内のテキストエントリを描画 (本文 blf + 外接ガイド枠).

    実際の組版は将来の typography レンダラに委ねるが、ビューポート上で
    「テキストがどこにあるか・何が書かれているか」が見えないと「追加した
    のに見当たらない」という不具合に見えるため、この関数で:
    - 半透明の白い下敷き (alpha=0.55 で目立つように)
    - ガイド枠線 (親子連動: シアン / 独立: 黄)
    - 本文 (空なら "(空のテキスト)" のプレースホルダ) を blf で描画
    する。
    """
    texts = getattr(page, "texts", None)
    if texts is None:
        return
    active_idx = getattr(page, "active_text_index", -1)
    for i, entry in enumerate(texts):
        rect = Rect(
            entry.x_mm + ox_mm,
            entry.y_mm + oy_mm,
            entry.width_mm,
            entry.height_mm,
        )
        # 白い下敷き (本文を読みやすくするため半透明白)
        _draw_rect_fill(rect, (1.0, 1.0, 1.0, 0.55))
        # ガイド枠線
        color = (0.2, 0.7, 1.0, 1.0) if entry.parent_balloon_id else (0.95, 0.85, 0.1, 1.0)
        _draw_rect_outline(rect, color, line_width=1.5)
        if i == active_idx:
            highlight = rect.inset(-1.0)
            _draw_rect_outline(highlight, (1.0, 0.6, 0.0, 1.0), line_width=2.0)
        # 本文 blf 描画は POST_PIXEL handler (_draw_texts_pixel) で実行


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


def _draw_text_in_rect(context, rect, text: str, color=(0, 0, 0, 1)) -> None:
    """``rect`` (mm) の中に ``text`` を blf で描画 (左上アンカー、改行未対応)."""
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector

    region, rv3d = _resolve_active_region(context)
    if region is None or rv3d is None:
        return
    # 左上 (mm) をスクリーン座標に
    world = Vector((mm_to_m(rect.x + 1.0), mm_to_m(rect.y2 - 1.0), 0.0))
    coord = location_3d_to_region_2d(region, rv3d, world)
    if coord is None:
        return
    font_id = _get_jp_font_id()
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


def _draw_panels(page, ox_mm: float = 0.0, oy_mm: float = 0.0) -> None:
    """ページ内のコマ枠・白フチを Z 順に従って描画.

    Z順序昇順 (背面→手前) で描画することで重なり時も正しく表示される。
    自動くり抜きは Phase 2 段階では未実装 (塗りつぶし描画ではなく枠線の
    みのため Z 重なりでも視覚的には問題なし)。本格的なクリッピングは
    Phase 2.5 で実装。
    """
    sorted_panels = sorted(page.panels, key=lambda p: p.z_order)
    for entry in sorted_panels:
        rect = _panel_rect(entry)
        if rect is None:
            continue
        if ox_mm != 0.0 or oy_mm != 0.0:
            rect = Rect(rect.x + ox_mm, rect.y + oy_mm, rect.width, rect.height)
        # 白フチ (枠線の外側)
        wm = entry.white_margin
        if wm.enabled and wm.width_mm > 0.0:
            outer = rect.inset(-wm.width_mm)
            color = (
                float(wm.color[0]),
                float(wm.color[1]),
                float(wm.color[2]),
                float(wm.color[3]),
            )
            _draw_rect_fill(outer, color)
        # 枠線
        b = entry.border
        if b.visible:
            color = (float(b.color[0]), float(b.color[1]), float(b.color[2]), float(b.color[3]))
            line_width = max(1.0, b.width_mm * 2.0)  # mm→画面線幅の暫定換算
            _draw_rect_outline(rect, color, line_width=line_width)


def _translate_rect(r: Rect, ox_mm: float, oy_mm: float) -> Rect:
    """Rect を (ox_mm, oy_mm) だけ平行移動."""
    if ox_mm == 0.0 and oy_mm == 0.0:
        return r
    return Rect(r.x + ox_mm, r.y + oy_mm, r.width, r.height)


def _draw_canvas_fill_only(paper, rects, ox_mm: float, oy_mm: float) -> None:
    """キャンバス塗りのみを描画 (PRE_VIEW から呼び出し、GP ストロークの下に敷く).

    PRE_VIEW 時代 (Phase 1-2) は POST_VIEW でオブジェクトを透かすため半透明
    (display_alpha=0.85) だったが、Phase 3 以降は PRE_VIEW で紙の下敷きとして
    描くため **不透明** (alpha=1.0) で固定。Blender 既定の暗い背景が用紙を
    通して透けるのを防ぐ。
    """
    canvas_r = _translate_rect(rects.canvas, ox_mm, oy_mm)
    canvas_color = (
        float(paper.paper_color[0]),
        float(paper.paper_color[1]),
        float(paper.paper_color[2]),
        1.0,
    )
    _draw_rect_fill(canvas_r, canvas_color)


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
    # is_left_half が True の場合は per-page で rects を再計算 (左右反転対応)
    if is_left_half:
        rects = overlay_shared.compute_paper_rects(paper, is_left_half=True)
    canvas_r = _translate_rect(rects.canvas, ox_mm, oy_mm)
    finish_r = _translate_rect(rects.finish, ox_mm, oy_mm)
    inner_r = _translate_rect(rects.inner_frame, ox_mm, oy_mm)
    safe_r = _translate_rect(rects.safe, ox_mm, oy_mm)
    bleed_r = _translate_rect(rects.bleed, ox_mm, oy_mm)

    # セーフライン外オーバーレイ (全ページに表示)
    # 仕様: 常に乗算合成相当 + alpha 100%。
    # Blender 5.x の gpu.state.blend_set("MULTIPLY") は受理されるが期待通り
    # 動かないため、ALPHA で「色付き半透明」描画して乗算同等の見た目を出す。
    # color は sa.color の RGB をそのまま使い、alpha は色の暗さ (1 - brightness)
    # に連動させる。これにより:
    #   - (0.7, 0.7, 0.7) グレー: alpha=0.3 → 紙の白に薄灰 = 30% 暗いグレー
    #   - (1, 0, 0) 赤:           alpha=0.67 → 赤フィルタ
    #   - (0, 0, 1) 青:           alpha=0.67 → 青フィルタ
    #   - (0, 0, 0) 黒:           alpha=1.0 → 完全に黒
    # 暗い色ほどフィルタが強く、明るい色ほど薄く出る (乗算らしい挙動)。
    sa = work.safe_area_overlay
    if sa.enabled:
        gpu.state.blend_set("ALPHA")
        r = float(sa.color[0])
        g = float(sa.color[1])
        b = float(sa.color[2])
        brightness = (r + g + b) / 3.0
        alpha = max(0.0, min(1.0, 1.0 - brightness))
        color = (r, g, b, alpha)
        _draw_frame_with_hole(canvas_r, safe_r, color)

    # 枠線群
    _draw_rect_outline(canvas_r, (0.4, 0.4, 0.4, 0.8), line_width=1.0)
    # 裁ち落とし枠 (= 仕上がり枠 + 裁ち落とし幅) を破線風の細線で描画
    if paper.bleed_mm > 0.0:
        _draw_rect_outline(bleed_r, (0.6, 0.4, 0.4, 0.7), line_width=1.0)
    _draw_rect_outline(finish_r, (0.8, 0.2, 0.2, 0.9), line_width=1.5)
    _draw_rect_outline(inner_r, (0.2, 0.6, 0.9, 0.9), line_width=1.0)
    _draw_rect_outline(safe_r, (0.2, 0.8, 0.4, 0.6), line_width=1.0)
    # トンボ (四隅 + 各辺中央センタートンボ) を仕上がり枠 / 裁ち落とし枠基準で描画
    if paper.bleed_mm > 0.0:
        _draw_trim_marks(finish_r, bleed_r)

    # 画像レイヤー (アクティブページのみ — 全ページ一覧時は負荷とレイヤーの per-scene 制約で省略)
    if mode == MODE_PAGE and draw_image_layers:
        _draw_image_layers(context.scene)

    # コマ枠 / フキダシ / テキスト (紙面編集モード時のみ)
    if mode == MODE_PAGE and page is not None:
        _draw_panels(page, ox_mm=ox_mm, oy_mm=oy_mm)
        _draw_balloons(page, ox_mm=ox_mm, oy_mm=oy_mm)
        _draw_texts(page, ox_mm=ox_mm, oy_mm=oy_mm, context=context)

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
        if abs(ox_i - ox_mm) < eps and abs(oy_i - oy_mm) < eps:
            return i
    return -1


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

    # フォントサイズ (pt) → px の概算 (ビューポート視認性優先で約 2.5x)
    font_id = _get_jp_font_id()
    size_px = max(8, int(float(item.font_size_pt) * 2.5))
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


def _draw_callback() -> None:
    context = bpy.context
    work = get_work(context)
    if work is None or not work.loaded:
        return
    paper = work.paper
    rects = overlay_shared.compute_paper_rects(paper)
    mode = get_mode(context)
    scene = context.scene

    gpu.state.blend_set("ALPHA")
    try:
        # panel モード中は overview を描かず、単ページの 2D 表示のみ
        # (計画書 3. Phase 1 — overlay は panel 編集モード時は従来動作)
        if (
            mode == MODE_PAGE
            and getattr(scene, "bname_overview_mode", False)
            and len(work.pages) > 0
        ):
            # 全ページ一覧モード.
            # 日本の漫画は右→左に読むため、ページ 0001 を右端に置き、追加した
            # ページ (0002, 0003...) を左方向に展開する。オフセットは負の X。
            from ..utils.page_grid import (
                is_left_half_page as _is_left_half,
                page_grid_offset_mm as _pg_offset,
            )

            cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
            gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
            cw = paper.canvas_width_mm
            ch = paper.canvas_height_mm
            start_side = getattr(paper, "start_side", "right")
            read_direction = getattr(paper, "read_direction", "left")
            active_idx = work.active_page_index
            for i, page in enumerate(work.pages):
                # 見開き判定込みの式は page_grid 側に集約
                ox, oy = _pg_offset(
                    i, cols, gap, cw, ch, start_side, read_direction
                )
                left_half = _is_left_half(i, start_side, read_direction)
                _draw_page_overlay(
                    context, work, paper, rects, page, mode,
                    ox_mm=ox, oy_mm=oy, draw_image_layers=False,
                    is_left_half=left_half,
                )
                # アクティブページにハイライト枠
                if i == active_idx:
                    canvas_r = _translate_rect(rects.canvas, ox, oy)
                    highlight = canvas_r.inset(-5.0)
                    _draw_rect_outline(highlight, (1.0, 0.85, 0.0, 0.9), line_width=3.0)
        else:
            from ..utils.page_grid import (
                is_left_half_page as _is_left_half,
                page_grid_offset_mm as _pg_offset,
            )
            page = get_active_page(context)
            cols = max(2, int(getattr(scene, "bname_overview_cols", 4)))
            gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
            cw = paper.canvas_width_mm
            ch = paper.canvas_height_mm
            start_side = getattr(paper, "start_side", "right")
            read_direction = getattr(paper, "read_direction", "left")
            idx = work.active_page_index
            # 単ページモードでも active page は grid 位置に紙メッシュがあるため、
            # overlay も同じ (ox, oy) で描画してオーバーレイと紙メッシュを一致させる。
            ox, oy = _pg_offset(
                max(0, idx), cols, gap, cw, ch, start_side, read_direction
            )
            left_half = _is_left_half(max(0, idx), start_side, read_direction)
            _draw_page_overlay(
                context, work, paper, rects, page, mode,
                ox_mm=ox, oy_mm=oy, draw_image_layers=True,
                is_left_half=left_half,
            )
    finally:
        gpu.state.blend_set("NONE")


def apply_bname_shading_mode(context=None) -> int:
    """全ウィンドウの全 VIEW_3D を「Solid + Flat 照明」に切替.

    B-Name 作品 UI の見え方を統一する目的:
    - 紙の白マテリアルが MatCap や Studio 光源で立体的に陰になるのを防ぎ、
      フラットな印刷物のように描画する
    - shading.type = "SOLID"
    - shading.light = "FLAT"
    - shading.color_type は変更しない (ユーザー設定維持)
    work_new / work_open / load_post から呼ぶ。戻り値は変更したエリア数。
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
                if getattr(shading, "type", None) != "SOLID":
                    shading.type = "SOLID"
                    count += 1
                if getattr(shading, "light", None) != "FLAT":
                    shading.light = "FLAT"
                    count += 1
            except Exception:  # noqa: BLE001
                _logger.exception("apply_bname_shading_mode: set failed")
    return count


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

    if mode != MODE_PAGE:
        return

    if (
        getattr(scene, "bname_overview_mode", False)
        and len(work.pages) > 0
    ):
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
        for i, page in enumerate(work.pages):
            ox, oy = _pg_offset(i, cols, gap, cw, ch, start_side, read_direction)
            left_half = _is_left_half(i, start_side, read_direction)
            inner = bleed_rect(paper)
            _draw_work_info_texts_pixel(context, work, inner, page_index=i,
                                         ox_mm=ox, oy_mm=oy)
            page = work.pages[i] if 0 <= i < len(work.pages) else None
            if page is not None:
                _draw_texts_pixel(context, page, ox_mm=ox, oy_mm=oy)
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
        left_half = _is_left_half(idx, start_side, read_direction)
        inner = bleed_rect(paper)
        _draw_work_info_texts_pixel(context, work, inner, page_index=idx,
                                     ox_mm=ox, oy_mm=oy)
        page = get_active_page(context)
        if page is not None:
            _draw_texts_pixel(context, page, ox_mm=ox, oy_mm=oy)


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


def _draw_texts_pixel(context, page, ox_mm: float, oy_mm: float) -> None:
    """POST_PIXEL 版のテキストエントリ本文描画 (blf のみ)."""
    texts = getattr(page, "texts", None)
    if texts is None:
        return
    for entry in texts:
        rect = Rect(
            entry.x_mm + ox_mm,
            entry.y_mm + oy_mm,
            entry.width_mm,
            entry.height_mm,
        )
        body = (getattr(entry, "body", "") or "").strip() or "(空のテキスト)"
        _draw_text_in_rect(context, rect, body, color=(0.0, 0.0, 0.0, 1.0))


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
