"""ページの見開き変更・解除 Operator (計画書 3.3.4 / Phase 3 データ保持版).

**データ保持**:
- 見開き統合時: 左右両ページの panels / balloons / texts / GP ストローク / cNN.blend を
  すべて見開きページに引き継ぐ
- 見開き解除時: 見開きページの panels / balloons / texts / GP を中心 x で左右
  ページに振り分けて保持

**座標規約**:
- 見開きキャンバスは幅 2 * ``canvas_width_mm`` の横長矩形として扱う
- 原点 (0, 0) はキャンバスの左下 = 左ページの原点
- 左ページ (b = 0002) の内容は x ∈ [0, W] に配置
- 右ページ (a = 0001) の内容は x ∈ [W, 2W] に配置
  - メタデータ (panels / balloons / texts) は PropertyGroup 上の x に ``+W`` を加算
  - GP オブジェクトは subpage offset custom property で ``+W`` の位置ずらしを表現
    (strokes 自体は触らず、obj.location のみで移動 → stroke データを破壊しない)

**見開き解除**:
- 各エンティティの中心 x を見て W 未満なら左ページ (0002)、W 以上なら右ページ (0001) に振り分け
- 右ページに振り分けた panels / balloons / texts は x を ``-W`` 戻す
- GP は subpage-offset 付きの右サブ GP (``*_R``) を右ページ用として切り出し、
  主 GP を左ページ用として切り出す
"""

from __future__ import annotations

import shutil
from pathlib import Path

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty
from bpy.types import Operator

from ..core.work import get_work
from ..io import page_io, coma_io, schema
from ..utils import gpencil as gp_utils
from ..utils import log, page_grid, paths

_logger = log.get_logger(__name__)


# ---------- 共通ヘルパ ----------


def _shift_coma_entry_x(entry, dx_mm: float) -> None:
    """coma_entry の rect / 多角形頂点 x を dx_mm ずらす."""
    entry.rect_x_mm = entry.rect_x_mm + dx_mm
    for v in entry.vertices:
        v.x_mm = v.x_mm + dx_mm


def _shift_balloon_entry_x(entry, dx_mm: float) -> None:
    entry.x_mm = entry.x_mm + dx_mm


def _shift_text_entry_x(entry, dx_mm: float) -> None:
    entry.x_mm = entry.x_mm + dx_mm


def _copy_coma_entry(src, dst) -> None:
    """ComaEntry の内容を schema 経由で複製. coma_id は呼出側で上書き."""
    data = schema.coma_entry_to_dict(src)
    schema.coma_entry_from_dict(dst, data)


def _copy_balloon_entry(src, dst) -> None:
    data = schema.balloon_entry_to_dict(src)
    schema.balloon_entry_from_dict(dst, data)


def _copy_text_entry(src, dst) -> None:
    data = schema.text_entry_to_dict(src)
    schema.text_entry_from_dict(dst, data)


def _reallocate_balloon_id(used_ids: set[str]) -> str:
    """新しい balloon id を採番。``used_ids`` と衝突しないように."""
    i = 1
    while True:
        candidate = f"balloon_{i:04d}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate
        i += 1


def _reallocate_text_id(used_ids: set[str]) -> str:
    i = 1
    while True:
        candidate = f"text_{i:04d}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate
        i += 1


def _subpage_gp_name(page_id: str, suffix: str = "") -> str:
    """見開きの右サブ GP 用の Object 名を返す.

    primary (左ページ用) は ``page_{page_id}_sketch``。
    suffix="_R" で ``page_{page_id}_sketch_R`` を返す。
    """
    return f"{gp_utils.page_gp_object_name(page_id)}{suffix}"


def _subpage_gp_data_name(page_id: str, suffix: str = "") -> str:
    return f"{gp_utils.page_gp_data_name(page_id)}{suffix}"


# ---------- 見開き統合 ----------


def _merge_pages_pp_groups(
    merged_entry, b_entry, canvas_width_mm: float
) -> None:
    """merged_entry (元 a) の panels/balloons/texts を +W シフト、b の内容を追加.

    - merged.comas: 既存 a の panels を x += W。続いて b の panels を x + 0 で append
      (coma_id 衝突は後段でリネーム処理)
    - balloons / texts も同様。b の balloon id / text id は merged 内で衝突する可能性が
      あるため採番し直し。text の parent_balloon_id は新 id に追随させる。
    """
    # a の既存 panels/balloons/texts を +W シフト (右半分へ)
    for panel in merged_entry.comas:
        _shift_coma_entry_x(panel, canvas_width_mm)
    for balloon in merged_entry.balloons:
        _shift_balloon_entry_x(balloon, canvas_width_mm)
    for text in merged_entry.texts:
        _shift_text_entry_x(text, canvas_width_mm)

    # b の balloon id → merged 内でユニーク化するためのマップ
    merged_balloon_ids = {b.id for b in merged_entry.balloons}
    balloon_id_map: dict[str, str] = {}
    for b_balloon in b_entry.balloons:
        if b_balloon.id in merged_balloon_ids or not b_balloon.id:
            new_id = _reallocate_balloon_id(merged_balloon_ids)
        else:
            new_id = b_balloon.id
            merged_balloon_ids.add(new_id)
        balloon_id_map[b_balloon.id] = new_id

    # b の balloons を merged に追加 (シフトなし: 左半分)
    for b_balloon in b_entry.balloons:
        new_entry = merged_entry.balloons.add()
        _copy_balloon_entry(b_balloon, new_entry)
        new_entry.id = balloon_id_map[b_balloon.id]

    # b の texts を merged に追加. parent_balloon_id を新 id にマップ
    merged_text_ids = {t.id for t in merged_entry.texts}
    for b_text in b_entry.texts:
        new_entry = merged_entry.texts.add()
        _copy_text_entry(b_text, new_entry)
        if new_entry.id in merged_text_ids or not new_entry.id:
            new_entry.id = _reallocate_text_id(merged_text_ids)
        else:
            merged_text_ids.add(new_entry.id)
        if b_text.parent_balloon_id in balloon_id_map:
            new_entry.parent_balloon_id = balloon_id_map[b_text.parent_balloon_id]


def _merge_coma_files(
    work_dir: Path,
    merged_entry,
    b_entry,
    a_old_id: str,
    b_old_id: str,
    spread_id: str,
) -> None:
    """a のディレクトリを spread に rename、b の panel_* を spread へコピー/rename.

    処理順:
    1. ``pages/{a_old_id}/`` を ``pages/{spread_id}/`` へ rename (a の panel_* もそのまま)
    2. b の各 panel について空き stem を採番し、``pages/{b_old_id}/panels/`` から
       ``pages/{spread_id}/panels/`` に move
    3. b の panel PropertyGroup を merged.comas に copy し、coma_id を新 stem に差替
    4. ``pages/{b_old_id}/`` ディレクトリ (panels 空のはず) を remove

    merged_entry の panels は既に +W シフト済 (呼出側で実施)。
    """
    work_dir = Path(work_dir)
    a_dir = paths.page_dir(work_dir, a_old_id)
    spread_dir = paths.page_dir(work_dir, spread_id)

    # 1) a のディレクトリを spread_id へ rename. (a == spread_id の場合は不要)
    if spread_dir.exists():
        raise FileExistsError(f"spread destination already exists: {spread_dir}")
    if a_dir.exists():
        a_dir.rename(spread_dir)
    else:
        # 念のため空の骨格を用意
        page_io.ensure_page_dir(work_dir, spread_id)

    # 2) b の panel ファイルを spread にコピー (stem 衝突回避で新採番)
    stem_remap: dict[str, str] = {}
    for b_panel in b_entry.comas:
        old_stem = b_panel.coma_id
        if not old_stem or not paths.is_valid_coma_id(old_stem):
            continue
        new_stem = coma_io.allocate_new_coma_id(work_dir, spread_id)
        try:
            coma_io.move_coma_files(work_dir, b_old_id, spread_id, old_stem, new_stem)
        except FileNotFoundError:
            # panel ファイルが存在しない PropertyGroup だけのケース (新規追加直後など)
            pass
        except Exception:  # noqa: BLE001
            _logger.exception(
                "merge: panel files move failed %s/%s -> %s/%s",
                b_old_id, old_stem, spread_id, new_stem,
            )
            continue
        stem_remap[old_stem] = new_stem

    # 3) b の panels を merged.comas に append. coma_id / id を新 stem に差替
    for b_panel in b_entry.comas:
        new_entry = merged_entry.comas.add()
        _copy_coma_entry(b_panel, new_entry)
        old_stem = b_panel.coma_id
        new_stem = stem_remap.get(old_stem, old_stem)
        # 衝突チェック: merged 内で同名 coma_id が既にあるなら更に新採番
        existing = {p.coma_id for p in merged_entry.comas if p is not new_entry}
        if new_stem in existing:
            new_stem = coma_io.allocate_new_coma_id(work_dir, spread_id)
        new_entry.coma_id = new_stem
        new_entry.id = new_stem
        # coma_id を書き換えた場合、.json メタも上書き再保存
        try:
            coma_io.save_coma_meta(work_dir, spread_id, new_entry)
        except Exception:  # noqa: BLE001
            _logger.exception("merge: save_coma_meta failed for %s", new_entry.coma_id)

    # 4) 空になった b ディレクトリを削除
    b_dir = paths.page_dir(work_dir, b_old_id)
    if b_dir.exists():
        try:
            shutil.rmtree(b_dir)
        except Exception:  # noqa: BLE001
            _logger.exception("merge: remove %s failed", b_dir)

    # 5) a の panel_*.json も +W シフト後の座標で上書き保存
    #    (page.json が load 時のソース・オブ・トゥルースだが、panel_*.json
    #    も揃えておく方がデータ不整合を起こしにくい)
    for panel in merged_entry.comas:
        if not panel.coma_id or not paths.is_valid_coma_id(panel.coma_id):
            continue
        # b 由来の panels は step 3 で既に save 済なので、a 由来のみ書き直す
        # 判定: stem_remap の値は b 由来のみ → a 由来を見分けるため
        #       stem_remap.values() に含まれない coma_id を対象とする
        if panel.coma_id in stem_remap.values():
            continue
        try:
            coma_io.save_coma_meta(work_dir, spread_id, panel)
        except Exception:  # noqa: BLE001
            _logger.exception("merge: resave panel meta failed for %s", panel.coma_id)


def _merge_page_gpencil(
    scene,
    a_old_id: str,
    b_old_id: str,
    spread_id: str,
    canvas_width_mm: float,
) -> None:
    """a / b の GP オブジェクトを見開きページ Collection に再配置.

    - b (左) の GP → 主 GP として ``page_{spread_id}_sketch`` にリネーム。
      obj.location は grid offset のみ (subpage_offset = 0)。
    - a (右) の GP → 副 GP として ``page_{spread_id}_sketch_R`` にリネーム。
      subpage_offset_x_mm = canvas_width_mm を custom property にセット。
    - 元 Collection ``page_{a_old_id}`` / ``page_{b_old_id}`` は削除。
    - Collection ``page_{spread_id}`` を新設して両 GP を収容。
    """
    # 取得
    a_obj = gp_utils.get_page_gpencil(a_old_id)
    b_obj = gp_utils.get_page_gpencil(b_old_id)
    a_coll = gp_utils.get_page_collection(a_old_id)
    b_coll = gp_utils.get_page_collection(b_old_id)

    # 新 Collection 作成 (他に衝突していれば既存を再利用)
    spread_coll = gp_utils.ensure_page_collection(scene, spread_id)

    # b → 主 GP
    if b_obj is not None:
        # name の衝突を避けるため、先に a を一時名に退避
        if a_obj is not None:
            tmp_obj_name = f"__bname_tmp_{a_old_id}_R_obj"
            tmp_data_name = f"__bname_tmp_{a_old_id}_R_data"
            gp_utils.rename_gp_object_and_data(a_obj, tmp_obj_name, tmp_data_name)
        # b を spread の主 GP 名にリネーム
        gp_utils.rename_gp_object_and_data(
            b_obj,
            gp_utils.page_gp_object_name(spread_id),
            gp_utils.page_gp_data_name(spread_id),
        )
        # spread Collection のみにリンク
        gp_utils.relink_object_to_page(scene, b_obj, spread_id)
        # 主 GP は subpage offset = 0
        b_obj[page_grid.SUBPAGE_OFFSET_X_PROP] = 0.0
        b_obj[page_grid.SUBPAGE_OFFSET_Y_PROP] = 0.0

    # a → 副 GP (右半分)
    if a_obj is not None:
        gp_utils.rename_gp_object_and_data(
            a_obj,
            _subpage_gp_name(spread_id, "_R"),
            _subpage_gp_data_name(spread_id, "_R"),
        )
        gp_utils.relink_object_to_page(scene, a_obj, spread_id)
        a_obj[page_grid.SUBPAGE_OFFSET_X_PROP] = float(canvas_width_mm)
        a_obj[page_grid.SUBPAGE_OFFSET_Y_PROP] = 0.0

    # 旧 Collection (a / b) を削除
    for coll in (a_coll, b_coll):
        if coll is None or coll == spread_coll:
            continue
        try:
            bpy.data.collections.remove(coll)
        except Exception:  # noqa: BLE001
            _logger.exception("merge: remove collection %s failed", coll.name)


# ---------- 見開き解除 ----------


def _split_page_assign_entries(
    spread_entry,
    left_entry,
    right_entry,
    canvas_width_mm: float,
) -> dict:
    """spread の panels/balloons/texts を中心 x で左右ページに振り分け.

    戻り値: ``{"right_coma_ids": [...], "balloon_id_map_right": {...}}``
    右ページ用のコマ stem リスト (ファイル操作の入力に使う) 等。
    """
    W = float(canvas_width_mm)

    # 振り分け: panel
    left_comas: list[dict] = []
    right_comas: list[dict] = []
    right_coma_ids: list[str] = []
    for p in spread_entry.comas:
        if p.shape_type == "rect":
            center_x = p.rect_x_mm + p.rect_width_mm / 2.0
        elif p.shape_type == "polygon" and len(p.vertices) > 0:
            xs = [v.x_mm for v in p.vertices]
            center_x = (min(xs) + max(xs)) / 2.0
        else:
            center_x = p.rect_x_mm
        data = schema.coma_entry_to_dict(p)
        if center_x < W:
            left_comas.append(data)
        else:
            right_comas.append(data)
            right_coma_ids.append(p.coma_id)

    # 左右ページの panels に再構築
    left_entry.comas.clear()
    right_entry.comas.clear()
    for d in left_comas:
        e = left_entry.comas.add()
        schema.coma_entry_from_dict(e, d)
        # 左ページはそのまま
    for d in right_comas:
        e = right_entry.comas.add()
        schema.coma_entry_from_dict(e, d)
        # 右ページは x を -W シフト
        _shift_coma_entry_x(e, -W)
    left_entry.active_coma_index = 0 if len(left_entry.comas) > 0 else -1
    right_entry.active_coma_index = 0 if len(right_entry.comas) > 0 else -1
    left_entry.coma_count = len(left_entry.comas)
    right_entry.coma_count = len(right_entry.comas)

    # balloon 振り分け
    left_balloon_ids: set[str] = set()
    right_balloon_ids: set[str] = set()
    balloon_to_page: dict[str, str] = {}  # balloon_id -> "L" or "R"
    left_balloons: list[dict] = []
    right_balloons: list[dict] = []
    for b in spread_entry.balloons:
        center_x = b.x_mm + b.width_mm / 2.0
        data = schema.balloon_entry_to_dict(b)
        if center_x < W:
            left_balloons.append(data)
            balloon_to_page[b.id] = "L"
            left_balloon_ids.add(b.id)
        else:
            right_balloons.append(data)
            balloon_to_page[b.id] = "R"
            right_balloon_ids.add(b.id)

    left_entry.balloons.clear()
    right_entry.balloons.clear()
    for d in left_balloons:
        e = left_entry.balloons.add()
        schema.balloon_entry_from_dict(e, d)
    for d in right_balloons:
        e = right_entry.balloons.add()
        schema.balloon_entry_from_dict(e, d)
        _shift_balloon_entry_x(e, -W)
    left_entry.active_balloon_index = 0 if len(left_entry.balloons) > 0 else -1
    right_entry.active_balloon_index = 0 if len(right_entry.balloons) > 0 else -1

    # text 振り分け. parent_balloon_id がついていれば親の所属ページに従う
    left_texts: list[dict] = []
    right_texts: list[dict] = []
    for t in spread_entry.texts:
        if t.parent_balloon_id and t.parent_balloon_id in balloon_to_page:
            page_side = balloon_to_page[t.parent_balloon_id]
        else:
            center_x = t.x_mm + t.width_mm / 2.0
            page_side = "L" if center_x < W else "R"
        data = schema.text_entry_to_dict(t)
        if page_side == "L":
            left_texts.append(data)
        else:
            right_texts.append(data)

    left_entry.texts.clear()
    right_entry.texts.clear()
    for d in left_texts:
        e = left_entry.texts.add()
        schema.text_entry_from_dict(e, d)
        # parent_balloon_id は左ページに残存する balloon だけ有効
        if e.parent_balloon_id and e.parent_balloon_id not in left_balloon_ids:
            e.parent_balloon_id = ""
    for d in right_texts:
        e = right_entry.texts.add()
        schema.text_entry_from_dict(e, d)
        _shift_text_entry_x(e, -W)
        if e.parent_balloon_id and e.parent_balloon_id not in right_balloon_ids:
            e.parent_balloon_id = ""
    left_entry.active_text_index = 0 if len(left_entry.texts) > 0 else -1
    right_entry.active_text_index = 0 if len(right_entry.texts) > 0 else -1

    return {
        "right_coma_ids": right_coma_ids,
    }


def _split_coma_files(
    work_dir: Path,
    spread_id: str,
    left_id: str,
    right_id: str,
    right_coma_ids: list[str],
) -> None:
    """spread/ ディレクトリを 2 ページに分割してファイルを配分.

    実装:
    1. ``pages/{spread_id}/`` ディレクトリを ``pages/{left_id}/`` に rename
      (左ページの panel ファイルはそのまま残る)
    2. 右ページ用に ``pages/{right_id}/`` を新設し、右ページに属する
      panel stem の一式を left_id から right_id へ move
    """
    work_dir = Path(work_dir)
    spread_dir = paths.page_dir(work_dir, spread_id)
    left_dir = paths.page_dir(work_dir, left_id)
    right_dir = paths.page_dir(work_dir, right_id)

    if left_dir.exists() and left_dir != spread_dir:
        raise FileExistsError(f"left destination already exists: {left_dir}")
    if right_dir.exists():
        raise FileExistsError(f"right destination already exists: {right_dir}")

    if spread_dir.exists() and spread_dir != left_dir:
        spread_dir.rename(left_dir)
    else:
        page_io.ensure_page_dir(work_dir, left_id)

    page_io.ensure_page_dir(work_dir, right_id)

    for stem in right_coma_ids:
        if not stem or not paths.is_valid_coma_id(stem):
            continue
        try:
            coma_io.move_coma_files(work_dir, left_id, right_id, stem, stem)
        except FileNotFoundError:
            pass  # panel ファイル未作成のエントリ
        except FileExistsError:
            # 右ディレクトリ側で衝突したら採番し直し
            new_stem = coma_io.allocate_new_coma_id(work_dir, right_id)
            coma_io.move_coma_files(work_dir, left_id, right_id, stem, new_stem)
            # PropertyGroup 側の coma_id は呼出側で再計算するのが本来だが、
            # ここで検出した場合は警告のみ (頻度は低いケース)
            _logger.warning(
                "split: panel stem collision %s -> renamed to %s (PropertyGroup unchanged)",
                stem, new_stem,
            )


def _split_page_gpencil(
    scene,
    spread_id: str,
    left_id: str,
    right_id: str,
) -> None:
    """見開きページの主 GP / _R サブ GP を左右ページ単独の GP に戻す.

    - 主 GP (``page_{spread_id}_sketch``) → 左ページ用にリネーム, subpage offset クリア
    - 副 GP (``page_{spread_id}_sketch_R``) → 右ページ用にリネーム, subpage offset クリア
    - 見開き Collection を削除、左/右 Collection を新設して各 GP を再リンク
    """
    primary_name = gp_utils.page_gp_object_name(spread_id)
    sub_name = _subpage_gp_name(spread_id, "_R")
    primary = bpy.data.objects.get(primary_name)
    sub = bpy.data.objects.get(sub_name)
    spread_coll = gp_utils.get_page_collection(spread_id)

    # primary → left_id 用 GP
    if primary is not None:
        gp_utils.rename_gp_object_and_data(
            primary,
            gp_utils.page_gp_object_name(left_id),
            gp_utils.page_gp_data_name(left_id),
        )
        gp_utils.relink_object_to_page(scene, primary, left_id)
        for key in (page_grid.SUBPAGE_OFFSET_X_PROP, page_grid.SUBPAGE_OFFSET_Y_PROP):
            try:
                if key in primary:
                    del primary[key]
            except Exception:  # noqa: BLE001
                pass
    else:
        # 主 GP が無ければ左ページに空 GP を新規生成
        gp_utils.ensure_page_gpencil(scene, left_id)

    # sub → right_id 用 GP
    if sub is not None:
        gp_utils.rename_gp_object_and_data(
            sub,
            gp_utils.page_gp_object_name(right_id),
            gp_utils.page_gp_data_name(right_id),
        )
        gp_utils.relink_object_to_page(scene, sub, right_id)
        for key in (page_grid.SUBPAGE_OFFSET_X_PROP, page_grid.SUBPAGE_OFFSET_Y_PROP):
            try:
                if key in sub:
                    del sub[key]
            except Exception:  # noqa: BLE001
                pass
    else:
        gp_utils.ensure_page_gpencil(scene, right_id)

    # 見開き Collection を削除 (空のはず)
    if spread_coll is not None:
        # relink で spread_coll から抜けているはず。念のため中身を確認。
        try:
            bpy.data.collections.remove(spread_coll)
        except Exception:  # noqa: BLE001
            _logger.exception("split: remove spread collection failed")


# ---------- Operator ----------


class BNAME_OT_pages_merge_spread(Operator):
    """連続 2 ページを見開きに統合 (データ保持つき)."""

    bl_idname = "bname.pages_merge_spread"
    bl_label = "見開きに変更"
    bl_options = {"REGISTER", "UNDO"}

    left_index: IntProperty(  # type: ignore[valid-type]
        name="左ページ index",
        default=-1,
        min=-1,
    )
    tombo_aligned: BoolProperty(  # type: ignore[valid-type]
        name="トンボを合わせる",
        default=True,
    )
    tombo_gap_mm: FloatProperty(  # type: ignore[valid-type]
        name="間隔 (mm)",
        description="負値はページを重ねる方向",
        default=-9.60,
    )

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        return bool(w and w.loaded and len(w.pages) >= 2)

    def invoke(self, context, event):
        work = get_work(context)
        if self.left_index < 0:
            self.left_index = work.active_page_index
        return context.window_manager.invoke_props_dialog(self, width=450)

    def draw(self, context):
        layout = self.layout
        work = get_work(context)
        left = self.left_index
        if not (0 <= left < len(work.pages) - 1):
            layout.label(text="左ページの選択が不正です", icon="ERROR")
            return
        a = work.pages[left]
        b = work.pages[left + 1]
        col = layout.column()
        col.label(text=f"{a.title} と {b.title} を見開きに統合します")
        summary = (
            f"コマ: {len(a.comas) + len(b.comas)} / "
            f"フキダシ: {len(a.balloons) + len(b.balloons)} / "
            f"テキスト: {len(a.texts) + len(b.texts)} を保持"
        )
        col.label(text=summary, icon="INFO")
        col.separator()
        col.label(
            text="右ページの内容は X 座標が +W シフトされ見開き右半分に配置されます",
            icon="ARROW_LEFTRIGHT",
        )
        col.separator()
        col.prop(self, "tombo_aligned")
        sub = col.column()
        sub.enabled = self.tombo_aligned
        sub.prop(self, "tombo_gap_mm")

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        left = self.left_index
        if not (0 <= left < len(work.pages) - 1):
            self.report({"ERROR"}, "左ページの選択が不正です")
            return {"CANCELLED"}
        a = work.pages[left]
        b = work.pages[left + 1]
        if a.spread or b.spread:
            self.report({"ERROR"}, "既に見開きのページは結合できません")
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)

        # 結合 ID (左=a, 右=b を連結した文字列; 読み順準拠)
        try:
            head_a = int(a.id.split("-", 1)[0].lstrip("p"))
            head_b = int(b.id.split("-", 1)[0].lstrip("p"))
        except ValueError:
            self.report({"ERROR"}, "ページ ID が不正です")
            return {"CANCELLED"}
        spread_id = paths.format_spread_id(head_a, head_b)

        a_old_id = a.id
        b_old_id = b.id
        W = float(work.paper.canvas_width_mm)

        try:
            # 1) メタデータ統合: a の panels/balloons/texts を +W、b を +0 で追加
            _merge_pages_pp_groups(a, b, W)

            # 2) ファイル操作: a dir を spread_id にリネームし、b の panels をコピー統合
            _merge_coma_files(work_dir, a, b, a_old_id, b_old_id, spread_id)

            # 3) GP: 左/右 GP を spread Collection に再配置、subpage_offset を設定
            _merge_page_gpencil(context.scene, a_old_id, b_old_id, spread_id, W)

            # 4) pages コレクション: b を削除し、a を spread_id にリブランド
            work.pages.remove(left + 1)
            merged = work.pages[left]
            merged.id = spread_id
            merged.title = f"{head_a}-{head_b}"
            merged.dir_rel = f"{spread_id}/"
            merged.spread = True
            merged.tombo_aligned = self.tombo_aligned
            merged.tombo_gap_mm = self.tombo_gap_mm
            merged.original_pages.clear()
            r1 = merged.original_pages.add()
            r1.page_id = paths.format_page_id(head_a)
            r2 = merged.original_pages.add()
            r2.page_id = paths.format_page_id(head_b)
            merged.coma_count = len(merged.comas)
            work.active_page_index = left

            # 5) grid transform を再配置
            page_grid.apply_page_collection_transforms(context, work)

            # 6) JSON 保存
            page_io.save_page_json(work_dir, merged)
            page_io.save_pages_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("pages_merge_spread failed")
            self.report({"ERROR"}, f"見開き統合失敗: {exc}")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"見開き統合: {spread_id} "
            f"(panels {len(merged.comas)} / balloons {len(merged.balloons)} / texts {len(merged.texts)})",
        )
        return {"FINISHED"}


class BNAME_OT_pages_split_spread(Operator):
    """見開きを 2 ページに解除 (データ保持つき)."""

    bl_idname = "bname.pages_split_spread"
    bl_label = "見開きを解除"
    bl_options = {"REGISTER", "UNDO"}

    spread_index: IntProperty(default=-1, min=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        w = get_work(context)
        if not (w and w.loaded):
            return False
        idx = w.active_page_index
        return 0 <= idx < len(w.pages) and w.pages[idx].spread

    def invoke(self, context, event):
        work = get_work(context)
        if self.spread_index < 0:
            self.spread_index = work.active_page_index
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded:
            return {"CANCELLED"}
        idx = self.spread_index
        if not (0 <= idx < len(work.pages)):
            return {"CANCELLED"}
        entry = work.pages[idx]
        if not entry.spread:
            self.report({"ERROR"}, "見開きページではありません")
            return {"CANCELLED"}
        if len(entry.original_pages) < 2:
            self.report({"ERROR"}, "結合元ページ情報が失われているため解除できません")
            return {"CANCELLED"}
        work_dir = Path(work.work_dir)
        spread_id_prev = entry.id
        # original_pages[0] は merge 時に head_a (読み順で先) を保存している。
        #   読み順 先 (earlier) = head_a = 見開き内の「右半分」  → reading_first / physical_right
        #   読み順 後 (later)   = head_b = 見開き内の「左半分」  → reading_second / physical_left
        reading_first_id = entry.original_pages[0].page_id   # = "0001" 側 = 物理右半分
        reading_second_id = entry.original_pages[1].page_id  # = "0002" 側 = 物理左半分

        W = float(work.paper.canvas_width_mm)

        try:
            # 1) spread entry の内容を dict で snapshot (後で振り分けに使う)
            spread_data = schema.page_to_dict(entry)

            # 2) spread entry を削除し、読み順で 2 ページを追加
            #    読み順 先 = 物理右半分 を idx (前方) に、
            #    読み順 後 = 物理左半分 を idx+1 (後方) に。
            work.pages.remove(idx)

            right_half = work.pages.add()
            right_half.id = reading_first_id
            right_half.title = reading_first_id
            right_half.dir_rel = f"{reading_first_id}/"
            right_half.spread = False
            work.pages.move(len(work.pages) - 1, idx)

            left_half = work.pages.add()
            left_half.id = reading_second_id
            left_half.title = reading_second_id
            left_half.dir_rel = f"{reading_second_id}/"
            left_half.spread = False
            work.pages.move(len(work.pages) - 1, idx + 1)

            # 一時 spread entry を再構築して振り分け元にする
            tmp_spread = work.pages.add()
            schema.page_from_dict(tmp_spread, spread_data)

            # 振り分け: 中心 x < W → 物理左半分 = left_half, それ以上 → 物理右半分 = right_half
            assignment = _split_page_assign_entries(tmp_spread, left_half, right_half, W)
            right_coma_ids = assignment["right_coma_ids"]

            # 一時 spread entry を削除
            work.pages.remove(len(work.pages) - 1)

            # 3) ファイル操作: spread/ → 物理左ページ (reading_second_id) dir に rename、
            #    物理右ページ (reading_first_id) 用に panel files を move
            _split_coma_files(
                work_dir, spread_id_prev, reading_second_id, reading_first_id, right_coma_ids
            )

            # 4) GP 分割 (primary → 左半分 = reading_second_id, sub_R → 右半分 = reading_first_id)
            _split_page_gpencil(
                context.scene, spread_id_prev, reading_second_id, reading_first_id
            )

            # 5) pages コレクションの active を読み順 先 (物理右) へ
            work.active_page_index = idx

            # 6) coma_count 再計算
            left_half.coma_count = len(left_half.comas)
            right_half.coma_count = len(right_half.comas)

            # 7) grid transform を再配置
            page_grid.apply_page_collection_transforms(context, work)

            # 8) JSON 保存
            #    page.json がロード時のソース・オブ・トゥルース。panel_*.json
            #    も座標不整合を避けるため左右ページ分を個別に書き直す。
            for e in left_half.comas:
                if e.coma_id and paths.is_valid_coma_id(e.coma_id):
                    try:
                        coma_io.save_coma_meta(work_dir, left_half.id, e)
                    except Exception:  # noqa: BLE001
                        _logger.exception("split: resave panel %s/%s failed", left_half.id, e.coma_id)
            for e in right_half.comas:
                if e.coma_id and paths.is_valid_coma_id(e.coma_id):
                    try:
                        coma_io.save_coma_meta(work_dir, right_half.id, e)
                    except Exception:  # noqa: BLE001
                        _logger.exception("split: resave panel %s/%s failed", right_half.id, e.coma_id)
            page_io.save_page_json(work_dir, left_half)
            page_io.save_page_json(work_dir, right_half)
            page_io.save_pages_json(work_dir, work)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("pages_split_spread failed")
            self.report({"ERROR"}, f"見開き解除失敗: {exc}")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"見開き解除: {reading_first_id} / {reading_second_id} "
            f"(右: panels {len(right_half.comas)} / 左: panels {len(left_half.comas)})",
        )
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_pages_merge_spread,
    BNAME_OT_pages_split_spread,
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
