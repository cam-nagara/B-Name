"""B-Name 専用キーマップ.

Phase 0 ではキーマップの **基盤** のみ用意する。実際のオペレータ呼び出し
割り当て（パン/回転/ズーム/スポイト等）は Phase 1 以降の viewport_ops.py
実装と同時に追加する。

ここで提供する機能:
- B-Name 専用 KeyMap の作成 / 破棄
- 既定キーマップ (Blender 標準) のうち B-Name と衝突し得るアイテムを
  ``KeyMapItem.active = False`` に切り替え、退避情報を保持
- unregister 時 (またはキーマップ無効化時) に元の active 状態へ完全に復元
- 現在の Blender キーマップ Preset 名を検出するフォールバック

設計メモ:
- ``bpy.context.window_manager.keyconfigs.addon`` はアドオンごとの KeyMap
  登録先として Blender 公式が用意している層。unregister 時に自作 KeyMap
  を空にすれば残留しない。
- 退避対象の既定キーマップ項目は ``keyconfigs.default`` 配下で検索する。
  Preset によって map の命名は概ね同じだが、キー割当や存在有無は違うので、
  衝突候補は「見つかったものだけ」退避する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional

import bpy

from ..utils import log

_logger = log.get_logger(__name__)

# B-Name 専用 KeyMap の名称。'3D View' 限定で空間を閉じる。
BNAME_KEYMAP_NAME = "B-Name Viewport"
BNAME_SPACE_TYPE = "VIEW_3D"
BNAME_REGION_TYPE = "WINDOW"

# B-Name が独占使用するキー組み合わせ.
# (type, shift, ctrl, alt) のタプル列挙。Blender のプリセット (Blender /
# Industry Compatible / Blender 27x 等) や idname は多岐にわたるため、
# idname ではなく「キー組み合わせ全部」で一括退避する。
_BNAME_EXCLUSIVE_COMBOS: tuple = (
    ("SPACE", False, False, False),  # Space
    ("SPACE", True, False, False),   # Shift + Space
    ("SPACE", False, True, False),   # Ctrl + Space
    ("WHEELUPMOUSE", False, True, False),    # Ctrl + Wheel Up
    ("WHEELDOWNMOUSE", False, True, False),  # Ctrl + Wheel Down
    ("LEFTMOUSE", True, True, False),        # Ctrl + Shift + LMB
)


@dataclass
class _SavedItem:
    keymap_name: str
    idname: str
    key_type: str
    shift: bool
    ctrl: bool
    alt: bool
    oskey: bool
    prev_active: bool
    item_ref: object = field(repr=False)  # bpy_struct (KeyMapItem) 参照


class KeymapState:
    """退避情報と B-Name 専用 KeyMap を保持する状態オブジェクト."""

    def __init__(self) -> None:
        self.saved: List[_SavedItem] = []
        self.bname_keymaps: List[object] = []
        self.bname_items: List[object] = []
        self.enabled: bool = False

    # ---------- B-Name 専用 KeyMap ----------

    def create_bname_keymap(self) -> Optional[object]:
        wm = bpy.context.window_manager
        if wm is None:
            return None
        kc = wm.keyconfigs.addon
        if kc is None:
            _logger.warning("addon keyconfig unavailable; skip bname keymap")
            return None
        km = kc.keymaps.new(
            name=BNAME_KEYMAP_NAME,
            space_type=BNAME_SPACE_TYPE,
            region_type=BNAME_REGION_TYPE,
        )
        self.bname_keymaps.append(km)
        self._populate_keymap_items(km)
        _logger.debug("bname keymap created: %s", BNAME_KEYMAP_NAME)
        return km

    def _populate_keymap_items(self, km) -> None:
        """B-Name 専用のキーマップエントリを追加 (計画書 3.6)."""
        # Space + ドラッグ → パン
        kmi = km.keymap_items.new("bname.view_pan", "SPACE", "PRESS")
        self.bname_items.append(kmi)
        # Shift + Space + ドラッグ → 回転
        kmi = km.keymap_items.new("bname.view_rotate", "SPACE", "PRESS", shift=True)
        self.bname_items.append(kmi)
        # Ctrl + Space + ドラッグ → ズーム (連続)
        kmi = km.keymap_items.new("bname.view_zoom_drag", "SPACE", "PRESS", ctrl=True)
        self.bname_items.append(kmi)
        # Ctrl + マウスホイール → ズーム (1 ステップ)
        kmi = km.keymap_items.new(
            "bname.view_zoom_step", "WHEELUPMOUSE", "PRESS", ctrl=True
        )
        kmi.properties.direction = "IN"
        self.bname_items.append(kmi)
        kmi = km.keymap_items.new(
            "bname.view_zoom_step", "WHEELDOWNMOUSE", "PRESS", ctrl=True
        )
        kmi.properties.direction = "OUT"
        self.bname_items.append(kmi)
        # Ctrl + Shift + クリック → レイヤー選択
        kmi = km.keymap_items.new(
            "bname.view_layer_pick", "LEFTMOUSE", "PRESS", ctrl=True, shift=True
        )
        self.bname_items.append(kmi)
        # ダブルクリック → コマ編集モードへ (ビューポート直下のコマを逆引き)
        kmi = km.keymap_items.new(
            "bname.enter_panel_mode", "LEFTMOUSE", "DOUBLE_CLICK"
        )
        self.bname_items.append(kmi)
        _logger.debug("bname keymap items: %d", len(self.bname_items))

    def set_bname_items_active(self, active: bool) -> int:
        """B-Name 自身のキーマップアイテムを一括で active/inactive に切替.

        addon keyconfig 層のアイテムは default 層より優先されるため、
        タブ非アクティブ時には False にしておかないと Blender 既定ショート
        カットに戻らない。
        """
        changed = 0
        for kmi in self.bname_items:
            try:
                if bool(kmi.active) != bool(active):
                    kmi.active = bool(active)
                    changed += 1
            except (ReferenceError, AttributeError):
                pass
        return changed

    def remove_bname_keymaps(self) -> None:
        wm = bpy.context.window_manager
        if wm is None:
            self.bname_keymaps.clear()
            self.bname_items.clear()
            return
        kc = wm.keyconfigs.addon
        if kc is None:
            self.bname_keymaps.clear()
            self.bname_items.clear()
            return
        for km in self.bname_keymaps:
            for kmi in list(self.bname_items):
                try:
                    km.keymap_items.remove(kmi)
                except Exception:  # noqa: BLE001 - 既に削除済みの場合は黙殺
                    pass
            try:
                kc.keymaps.remove(km)
            except Exception:  # noqa: BLE001
                pass
        self.bname_keymaps.clear()
        self.bname_items.clear()
        _logger.debug("bname keymaps removed")

    # ---------- 既定キーマップ退避/復元 ----------

    def override_defaults(
        self, combos: Iterable = _BNAME_EXCLUSIVE_COMBOS
    ) -> int:
        """B-Name が独占使用するキー組み合わせに当たる全既定キーマップアイテムを退避.

        default / user の両 keyconfigs を走査し、combos にマッチする active
        な KeyMapItem を無効化する (B-Name 自身の bname.* idname はスキップ)。
        これにより Blender のプリセット (Blender / Industry Compatible 等) や
        バージョンに依存せず確実に B-Name の割当てが優先される。

        Returns: 退避した項目数。
        """
        wm = bpy.context.window_manager
        if wm is None:
            return 0
        combos = list(combos)
        keyconfigs = []
        # Blender 5.x では ``wm.keyconfigs.get("default")`` が None を返す場合
        # がある (コレクション名とプロパティアクセス名の非対称)。``default`` /
        # ``user`` は KeyConfigurations のプロパティとして公開されているので
        # 属性アクセスで取得する。addon 層は B-Name 自身の keymap 登録先で
        # 参照する必要が無いので含めない。
        for kc_name, kc in (
            ("default", getattr(wm.keyconfigs, "default", None)),
            ("user", getattr(wm.keyconfigs, "user", None)),
            ("active", getattr(wm.keyconfigs, "active", None)),
        ):
            if kc is not None and (kc_name, kc) not in keyconfigs:
                keyconfigs.append((kc_name, kc))
        # 重複除去 (active が default と同一インスタンスの場合)
        seen = set()
        unique = []
        for name, kc in keyconfigs:
            key = id(kc)
            if key in seen:
                continue
            seen.add(key)
            unique.append((name, kc))
        keyconfigs = unique
        if not keyconfigs:
            _logger.warning("no default/user/active keyconfigs available; skip override")
            return 0

        count = 0
        for kc_name, kc in keyconfigs:
            for km in kc.keymaps:
                for kmi in km.keymap_items:
                    # 自分自身の割当てはスキップ
                    if kmi.idname.startswith("bname."):
                        continue
                    if not kmi.active:
                        continue
                    matched = False
                    for ktype, kshift, kctrl, kalt in combos:
                        if (
                            kmi.type == ktype
                            and bool(kmi.shift) == kshift
                            and bool(kmi.ctrl) == kctrl
                            and bool(kmi.alt) == kalt
                        ):
                            matched = True
                            break
                    if not matched:
                        continue
                    self.saved.append(
                        _SavedItem(
                            keymap_name=f"{kc_name}:{km.name}",
                            idname=kmi.idname,
                            key_type=kmi.type,
                            shift=bool(kmi.shift),
                            ctrl=bool(kmi.ctrl),
                            alt=bool(kmi.alt),
                            oskey=bool(kmi.oskey),
                            prev_active=True,
                            item_ref=kmi,
                        )
                    )
                    kmi.active = False
                    count += 1
        # count が 0 でも「override 状態に入った」とみなす。
        # ウォッチャーが毎回 override_defaults を呼び直すのを防ぐ。
        self.enabled = True
        _logger.info("default keymap overrides applied: %d item(s)", count)
        return count

    def restore_defaults(self) -> None:
        """退避した既定キーマップ項目を元の active 状態へ戻す."""
        restored = 0
        for saved in self.saved:
            try:
                saved.item_ref.active = saved.prev_active
                restored += 1
            except (ReferenceError, AttributeError):
                # KeyMapItem が既に破棄されている場合は諦める
                pass
        self.saved.clear()
        self.enabled = False
        _logger.info("default keymap restored: %d item(s)", restored)

    # ---------- Preset 検出 ----------

    @staticmethod
    def detect_preset_name() -> str:
        """現在の Blender キーマップ Preset 名を検出.

        Blender の Preset は ``WindowManager.keyconfigs.active.name`` に
        入っている (例: "Blender", "Industry Compatible" 等)。取得できない
        場合は空文字を返す。
        """
        wm = bpy.context.window_manager
        if wm is None:
            return ""
        kc = wm.keyconfigs.active
        return kc.name if kc is not None else ""


def _match_filter(kmi: object, filt: dict) -> bool:
    for key, expected in filt.items():
        if getattr(kmi, key, None) != expected:
            return False
    return True


# ---------- モジュール公開 API ----------

_state: Optional[KeymapState] = None

# タイマー監視間隔 (秒)
_WATCH_INTERVAL = 0.5
# B-Name タブの bl_category 名
_BNAME_TAB_CATEGORY = "B-Name"


def get_state() -> Optional[KeymapState]:
    return _state


def _any_bname_tab_active() -> bool:
    """いずれかの VIEW_3D の N パネルで B-Name タブがアクティブか判定.

    Blender 5.x で ``region.active_panel_category`` の値の取り方が変わった
    影響で、安定して「B-Name タブが active か」を判定するのは難しい。
    現行では ``keymap_enabled`` が True のとき常に True を返し、
    B-Name ショートカットを常時有効化する運用に変更している (下記
    ``_watch_bname_tab`` 参照)。この関数は将来のタブ連動復活用に残す。
    """
    wm = bpy.context.window_manager
    if wm is None:
        return False
    for window in wm.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            if space is None or not getattr(space, "show_region_ui", False):
                continue
            for region in area.regions:
                if region.type != "UI":
                    continue
                category = getattr(region, "active_panel_category", None)
                if category == _BNAME_TAB_CATEGORY:
                    return True
    return False


def _watch_bname_tab() -> Optional[float]:
    """タイマー: B-Name キーマップを常時有効化 (Phase 3+ 新挙動).

    旧挙動は「B-Name タブ active 時のみ B-Name ショートカット有効」だったが、
    Blender 5.x でタブ active 判定 API (``region.active_panel_category``) が
    安定せずショートカットが効かなくなる不具合があった。Phase 3+ では
    preferences.keymap_enabled=True なら常に B-Name キーマップ + 既定
    キーマップ退避を有効化し、keymap_enabled=False なら全て復元する。

    これにより:
    - addon 有効かつ keymap_enabled ON → B-Name ショートカットが常時効く
      (Space がツールパイメニューを出さない、等)
    - 無効化したいユーザーは preferences から keymap_enabled を OFF に

    override_defaults / restore_defaults / set_bname_items_active は
    冪等で、毎ティック呼んでも追加コストは微小 (状態の早期 return あり)。
    """
    state = _state
    if state is None:
        return None  # タイマー停止
    try:
        from ..preferences import get_preferences

        prefs = get_preferences()
        enabled = True if prefs is None else bool(prefs.keymap_enabled)

        if enabled:
            # set_bname_items_active は内部で変化判定付き = 冪等なので毎 tick 呼んで OK
            state.set_bname_items_active(True)
            if not state.enabled:
                # override_defaults は状態遷移時のみ (既存 saved リストの重複登録を避ける)
                state.override_defaults()
        elif state.enabled:
            state.restore_defaults()
            state.set_bname_items_active(False)
    except Exception:  # noqa: BLE001
        _logger.exception("watch_bname_tab failed")
    return _WATCH_INTERVAL


def _register_watcher() -> None:
    if bpy.app.timers.is_registered(_watch_bname_tab):
        return
    bpy.app.timers.register(
        _watch_bname_tab,
        first_interval=_WATCH_INTERVAL,
        persistent=True,
    )


def _unregister_watcher() -> None:
    if bpy.app.timers.is_registered(_watch_bname_tab):
        try:
            bpy.app.timers.unregister(_watch_bname_tab)
        except ValueError:
            pass


def register() -> None:
    global _state
    _state = KeymapState()
    preset = KeymapState.detect_preset_name()
    _logger.info("detected keymap preset: %s", preset or "(unknown)")

    # Preferences に従い、B-Name キーマップを有効化するかを決める
    from ..preferences import get_preferences

    prefs = get_preferences()
    keymap_enabled = True if prefs is None else bool(prefs.keymap_enabled)

    _state.create_bname_keymap()
    # B-Name キーマップを **register 時点で即時 active 化**.
    # 旧実装は watcher 起動を待っていたが、Blender 5.x で watcher が
    # 効くまでに UI 操作が走るとショートカットが反応しないため、最初から
    # 有効化しておく。Disable は preferences.keymap_enabled を OFF に切替
    # した時のみ watcher が反映する。
    if keymap_enabled:
        _state.set_bname_items_active(True)
        _state.override_defaults()
    else:
        _state.set_bname_items_active(False)
    # watcher は preferences.keymap_enabled の動的トグルへの追従専用
    _register_watcher()
    _logger.info(
        "keymap registered (enabled=%s, items=%d, overrides=%d, watcher=%s)",
        keymap_enabled,
        len(_state.bname_items),
        len(_state.saved),
        bpy.app.timers.is_registered(_watch_bname_tab),
    )


def unregister() -> None:
    global _state
    if _state is None:
        return
    _unregister_watcher()
    try:
        _state.restore_defaults()
    finally:
        _state.remove_bname_keymaps()
    _state = None
    _logger.debug("keymap unregistered")
