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

from typing import Optional

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
from ..utils.geom import Rect, mm_to_m
from . import overlay_shared

_logger = log.get_logger(__name__)

# draw_handler_add の戻り値 (ハンドラ識別子)
_handle: Optional[object] = None


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
    """ページ内のフキダシをオーバーレイ描画 (Phase 3 骨格: 矩形プレビュー).

    形状別の厳密な描画 (雲/トゲ/曲線尻尾) は Phase 6 の書き出しパイプラインで
    Pillow 実装側で本番化する。overview での視覚的配置確認用の描画として、
    ここでは矩形 + 塗り色 + 線色で概形を描く。
    """
    balloons = getattr(page, "balloons", None)
    if balloons is None:
        return
    active_idx = getattr(page, "active_balloon_index", -1)
    for i, entry in enumerate(balloons):
        if entry.shape == "none":
            continue  # テキスト単体
        rect = Rect(
            entry.x_mm + ox_mm,
            entry.y_mm + oy_mm,
            entry.width_mm,
            entry.height_mm,
        )
        fill = (
            float(entry.fill_color[0]),
            float(entry.fill_color[1]),
            float(entry.fill_color[2]),
            float(entry.fill_color[3]),
        )
        _draw_rect_fill(rect, fill)
        line = (
            float(entry.line_color[0]),
            float(entry.line_color[1]),
            float(entry.line_color[2]),
            float(entry.line_color[3]),
        )
        line_width = max(1.0, float(entry.line_width_mm) * 2.0)
        _draw_rect_outline(rect, line, line_width=line_width)
        if i == active_idx:
            highlight = rect.inset(-1.0)
            _draw_rect_outline(highlight, (1.0, 0.6, 0.0, 0.9), line_width=2.0)


def _draw_texts(page, ox_mm: float = 0.0, oy_mm: float = 0.0) -> None:
    """ページ内のテキストエントリの外接矩形を淡く描画 (Phase 3 骨格).

    実際のテキスト組版描画は ``typography/viewport_renderer.py`` が担当する
    想定。ここでは「どこにテキストがあるか」の配置ガイドだけ。
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
        # 白フチ背景 (うっすら) → ガイド枠
        _draw_rect_fill(rect, (1.0, 1.0, 1.0, 0.25))
        # 親子連動中なら親フキダシと同色系のガイド (シアン)、独立なら黄
        color = (0.2, 0.7, 1.0, 0.9) if entry.parent_balloon_id else (0.9, 0.9, 0.1, 0.9)
        _draw_rect_outline(rect, color, line_width=1.0)
        if i == active_idx:
            highlight = rect.inset(-1.0)
            _draw_rect_outline(highlight, (1.0, 0.6, 0.0, 0.9), line_width=2.0)


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
) -> None:
    """1 ページ分のガイド/コマ枠を (ox_mm, oy_mm) オフセットで描画.

    **キャンバス塗りは描画しない** (PRE_VIEW の ``_draw_callback_pre`` で
    GP ストロークの下に敷くため)。この関数は POST_VIEW 用で、ストロークの
    上に乗るべき要素 (枠線・コマ・フキダシ・テキスト・画像レイヤー) のみ
    描画する。
    """
    canvas_r = _translate_rect(rects.canvas, ox_mm, oy_mm)
    finish_r = _translate_rect(rects.finish, ox_mm, oy_mm)
    inner_r = _translate_rect(rects.inner_frame, ox_mm, oy_mm)
    safe_r = _translate_rect(rects.safe, ox_mm, oy_mm)

    # セーフライン外オーバーレイ (アクティブページのみ。overview 中は重いので省略)
    sa = work.safe_area_overlay
    if sa.enabled and ox_mm == 0.0 and oy_mm == 0.0:
        _apply_blend_mode(sa.blend_mode)
        color = (float(sa.color[0]), float(sa.color[1]), float(sa.color[2]), float(sa.opacity))
        _draw_frame_with_hole(canvas_r, safe_r, color)
        gpu.state.blend_set("ALPHA")

    # 枠線群
    _draw_rect_outline(canvas_r, (0.4, 0.4, 0.4, 0.8), line_width=1.0)
    _draw_rect_outline(finish_r, (0.8, 0.2, 0.2, 0.9), line_width=1.5)
    _draw_rect_outline(inner_r, (0.2, 0.6, 0.9, 0.9), line_width=1.0)
    _draw_rect_outline(safe_r, (0.2, 0.8, 0.4, 0.6), line_width=1.0)

    # 画像レイヤー (アクティブページのみ — 全ページ一覧時は負荷とレイヤーの per-scene 制約で省略)
    if mode == MODE_PAGE and draw_image_layers:
        _draw_image_layers(context.scene)

    # コマ枠 / フキダシ / テキスト (紙面編集モード時のみ)
    if mode == MODE_PAGE and page is not None:
        _draw_panels(page, ox_mm=ox_mm, oy_mm=oy_mm)
        _draw_balloons(page, ox_mm=ox_mm, oy_mm=oy_mm)
        _draw_texts(page, ox_mm=ox_mm, oy_mm=oy_mm)


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
            cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
            gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
            cw = paper.canvas_width_mm
            ch = paper.canvas_height_mm
            active_idx = work.active_page_index
            for i, page in enumerate(work.pages):
                col = i % cols
                row = i // cols
                ox = -col * (cw + gap)  # 左方向 (負の X) に展開
                oy = -row * (ch + gap)
                _draw_page_overlay(
                    context, work, paper, rects, page, mode,
                    ox_mm=ox, oy_mm=oy, draw_image_layers=False,
                )
                # アクティブページにハイライト枠
                if i == active_idx:
                    canvas_r = _translate_rect(rects.canvas, ox, oy)
                    highlight = canvas_r.inset(-5.0)
                    _draw_rect_outline(highlight, (1.0, 0.85, 0.0, 0.9), line_width=3.0)
        else:
            page = get_active_page(context)
            _draw_page_overlay(
                context, work, paper, rects, page, mode,
                ox_mm=0.0, oy_mm=0.0, draw_image_layers=True,
            )
    finally:
        gpu.state.blend_set("NONE")


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


def register() -> None:
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (), "WINDOW", "POST_VIEW"
        )
    _logger.debug("overlay draw_handler registered (POST_VIEW)")


def unregister() -> None:
    global _handle
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        except (ValueError, RuntimeError):
            pass
        _handle = None
    _logger.debug("overlay draw_handler removed")
