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

# B-Name 専用キーは Blender 標準の "3D View" キーマップ (addon 層) に登録する.
# 独自名 ("B-Name Viewport" 等) で keymaps.new すると addon kc には残るが、
# Blender の active keyconfig マージ評価に乗らずキーが一切発火しない (確認済).
# unregister では kc.keymaps.remove(km) は呼ばず、keymap_items の削除のみ行う
# (標準の "3D View" キーマップを丸ごと消すと Blender 既定操作が壊れる)。
BNAME_KEYMAP_NAME = "3D View"
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
            print("[B-Name][KEYMAP] create_bname_keymap: window_manager is None")
            return None
        kc = wm.keyconfigs.addon
        if kc is None:
            print("[B-Name][KEYMAP] create_bname_keymap: keyconfigs.addon is None")
            _logger.warning("addon keyconfig unavailable; skip bname keymap")
            return None
        # 旧バージョンが残した独自名キーマップ ("B-Name Viewport") を addon kc
        # から掃除する。残ったまま新しい "3D View" 経由で kmi を増やすと、
        # 無効化時に二重 unregister で C レベルクラッシュする可能性がある。
        for legacy_name in ("B-Name Viewport",):
            legacy = kc.keymaps.get(legacy_name)
            if legacy is not None:
                try:
                    kc.keymaps.remove(legacy)
                    print(f"[B-Name][KEYMAP] removed legacy keymap: {legacy_name!r}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[B-Name][KEYMAP] legacy keymap removal failed: {exc!r}")
        try:
            km = kc.keymaps.new(
                name=BNAME_KEYMAP_NAME,
                space_type=BNAME_SPACE_TYPE,
                region_type=BNAME_REGION_TYPE,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[B-Name][KEYMAP] keymaps.new failed: {exc!r}")
            _logger.exception("keymaps.new failed")
            return None
        self.bname_keymaps.append(km)
        try:
            self._populate_keymap_items(km)
        except Exception as exc:  # noqa: BLE001
            print(f"[B-Name][KEYMAP] _populate_keymap_items failed: {exc!r}")
            _logger.exception("_populate_keymap_items failed")
            return None
        # Window キーマップにも Shift+Space / Ctrl+Space を登録して
        # screen.screen_full_area (Shift+Space) 等の標準ショートカットを
        # 先取りする。3D View 外で押された場合は invoke が PASS_THROUGH を
        # 返すので、Outliner 等での標準動作には影響しない。
        try:
            km_window = kc.keymaps.new(
                name="Window", space_type="EMPTY", region_type="WINDOW"
            )
            self.bname_keymaps.append(km_window)
            self._populate_window_overrides(km_window)
        except Exception as exc:  # noqa: BLE001
            print(f"[B-Name][KEYMAP] Window keymap setup failed: {exc!r}")
            _logger.exception("Window keymap setup failed")

        # Grease Pencil Paint / Draw モードキーマップに Space と C を登録。
        # キーマップ名は Blender バージョン (GP legacy / GP v3) や Locale で
        # 揺れるため、default kc を走査して名前+SPACE/C 既定割当を全部 dump し、
        # "rease" を含む keymap には漏らさず先取り登録する。
        gp_keymap_targets: list[tuple[str, str, str]] = []
        try:
            default_kc = wm.keyconfigs.default
            if default_kc is not None:
                # 全キーマップ名 dump (デバッグ用、"rease"/"Paint"/"Draw"/"Asset"/"Brush" を含むもの)
                print("[B-Name][KEYMAP] -- default kc keymap survey --")
                for km in default_kc.keymaps:
                    nm = km.name
                    if any(s in nm for s in ("rease", "Paint", "Draw", "Asset", "Brush")):
                        # SPACE / C 既定割当を確認
                        space_kmis = []
                        c_kmis = []
                        try:
                            for kmi in km.keymap_items:
                                if kmi.type == "SPACE" and not (
                                    kmi.shift or kmi.ctrl or kmi.alt
                                ):
                                    space_kmis.append(kmi.idname)
                                if kmi.type == "C" and not (
                                    kmi.shift or kmi.ctrl or kmi.alt
                                ):
                                    c_kmis.append(kmi.idname)
                        except Exception:  # noqa: BLE001
                            pass
                        print(
                            f"  km={nm!r} space_type={km.space_type}"
                            f" region_type={km.region_type}"
                            f" SPACE={space_kmis} C={c_kmis}"
                        )
                # GP Paint/Draw/Edit モード系を全部ターゲットに含める
                # (L=投げ縄 / Ctrl+X / Ctrl+V を Edit モードでも先取り)
                for km in default_kc.keymaps:
                    if "rease Pencil" in km.name and (
                        "Paint" in km.name
                        or "Draw" in km.name
                        or "Edit" in km.name
                    ):
                        gp_keymap_targets.append(
                            (km.name, km.space_type, km.region_type)
                        )
            print(f"[B-Name][KEYMAP] GP Paint/Draw/Edit targets: {gp_keymap_targets}")
            for name, st, rt in gp_keymap_targets:
                try:
                    km_gp = kc.keymaps.new(name=name, space_type=st, region_type=rt)
                    self.bname_keymaps.append(km_gp)
                    self._populate_gp_paint_overrides(km_gp, name)
                except Exception as exc:  # noqa: BLE001
                    print(f"[B-Name][KEYMAP] GP keymap setup failed ({name}): {exc!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"[B-Name][KEYMAP] GP keymap discovery failed: {exc!r}")
        print(
            f"[B-Name][KEYMAP] bname keymap created: name={BNAME_KEYMAP_NAME}"
            f" items={len(self.bname_items)} kc_name={kc.name!r}"
        )
        _logger.info("bname keymap created (items=%d)", len(self.bname_items))
        return km

    def _populate_gp_paint_overrides(self, km, km_name: str) -> None:
        """Grease Pencil Paint / Edit モードキーマップに先取り登録.

        - Space → bname.view_navigate (ブラシ Asset Shelf の先取り)
        - C     → bname.toggle_asset_shelf (元の機能を C 側に移設)
        - L     → bname.toggle_lasso_tool (投げ縄 ⇔ Box トグル)
        - Ctrl+X → bname.gp_cut_to_new_layer (Paste で新レイヤー化フラグを立てる)
        - Ctrl+V → bname.gp_paste_to_new_layer (フラグありなら新レイヤーへ paste)
        """
        try:
            from ..preferences import get_preferences
            prefs = get_preferences()
            nav_key = getattr(prefs, "key_navigate", "SPACE") if prefs else "SPACE"
            if not nav_key:
                nav_key = "SPACE"
        except Exception:  # noqa: BLE001
            nav_key = "SPACE"

        def _add(idname, key, **mods):
            try:
                kmi = km.keymap_items.new(idname, key, "PRESS", **mods)
                self.bname_items.append(kmi)
                print(
                    f"[B-Name][KEYMAP] + {idname} ({km_name}) {key}"
                    f" shift={kmi.shift} ctrl={kmi.ctrl} alt={kmi.alt}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] {km_name} {key} override failed: {exc!r}")

        _add("bname.view_navigate", nav_key)
        _add("bname.toggle_asset_shelf", "C")
        _add("bname.toggle_lasso_tool", "L")
        _add("bname.gp_cut_to_new_layer", "X", ctrl=True)
        _add("bname.gp_paste_to_new_layer", "V", ctrl=True)

    def _populate_window_overrides(self, km) -> None:
        """Window キーマップに 修飾+ナビゲートキー を先取り登録.

        Blender 標準では Shift+Space / Ctrl+Space が Window キーマップ層で
        screen.screen_full_area などに割当てられており、addon kc の
        "3D View" よりも評価が早い。Window 層に同じキー組み合わせで
        bname.view_navigate を登録することで先取りする。
        """
        # preferences 取得 (失敗時は SPACE 既定)
        try:
            from ..preferences import get_preferences
            prefs = get_preferences()
            nav_key = getattr(prefs, "key_navigate", "SPACE") if prefs else "SPACE"
            if not nav_key:
                nav_key = "SPACE"
        except Exception:  # noqa: BLE001
            nav_key = "SPACE"

        for shift, ctrl, label in ((True, False, "shift"), (False, True, "ctrl")):
            try:
                kmi = km.keymap_items.new(
                    "bname.view_navigate", nav_key, "PRESS",
                    shift=shift, ctrl=ctrl,
                )
                self.bname_items.append(kmi)
                print(
                    f"[B-Name][KEYMAP] + bname.view_navigate (Window) {label}+{nav_key}"
                    f" active={kmi.active}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] window override {label}+{nav_key} failed: {exc!r}")

    def _populate_keymap_items(self, km) -> None:
        """B-Name 専用のキーマップエントリを追加.

        preferences 値 (key_navigate / key_set_mode_object / key_set_mode_draw
        / key_page_next / key_page_prev とそれぞれの mod_*) を読み込んで
        keymap items を構築する。preferences 取得失敗時は既定値を用いる。

        ナビゲート (パン/回転/ズーム) は Space 1キーに統合し、modal 内で
        Shift/Ctrl 状態を見て動的切替する。Shift+Space を addon kc に直接
        登録すると Blender 標準 (screen.screen_full_area) と衝突するため、
        修飾組み合わせは Window キーマップ側で別途先取りする。
        """
        def _add(idname, key, value="PRESS", **mods):
            try:
                kmi = km.keymap_items.new(idname, key, value, **mods)
                self.bname_items.append(kmi)
                print(
                    f"[B-Name][KEYMAP] + {idname} key={key} value={value}"
                    f" shift={kmi.shift} ctrl={kmi.ctrl} alt={kmi.alt}"
                    f" active={kmi.active}"
                )
                return kmi
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] FAILED to add {idname} {key} {mods}: {exc!r}")
                return None

        # preferences を取得 (失敗時は既定値で動く)
        try:
            from ..preferences import get_preferences
            prefs = get_preferences()
        except Exception:  # noqa: BLE001
            prefs = None

        def _key(attr, default):
            if prefs is None:
                return default
            v = getattr(prefs, attr, default)
            return v if v else default

        def _mods(prefix):
            if prefs is None:
                return False, False, False
            return (
                bool(getattr(prefs, f"{prefix}_shift", False)),
                bool(getattr(prefs, f"{prefix}_ctrl", False)),
                bool(getattr(prefs, f"{prefix}_alt", False)),
            )

        # 統合ナビゲートモーダル (キー単独、修飾は modal 内で動的判定)
        _add("bname.view_navigate", _key("key_navigate", "SPACE"))

        # Ctrl + ホイール → 1 ステップズーム (固定)
        kmi = _add("bname.view_zoom_step", "WHEELUPMOUSE", ctrl=True)
        if kmi is not None:
            try:
                kmi.properties.direction = "IN"
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] set direction IN failed: {exc!r}")
        kmi = _add("bname.view_zoom_step", "WHEELDOWNMOUSE", ctrl=True)
        if kmi is not None:
            try:
                kmi.properties.direction = "OUT"
            except Exception as exc:  # noqa: BLE001
                print(f"[B-Name][KEYMAP] set direction OUT failed: {exc!r}")

        # Ctrl+Shift+クリック → レイヤー選択 (固定)
        _add("bname.view_layer_pick", "LEFTMOUSE", ctrl=True, shift=True)
        # ダブルクリック → コマ編集モードへ (固定)
        _add("bname.enter_panel_mode", "LEFTMOUSE", value="DOUBLE_CLICK")

        # preferences 設定可能なショートカット
        s, c, a = _mods("mod_set_mode_object")
        _add("bname.set_mode_object", _key("key_set_mode_object", "O"),
             shift=s, ctrl=c, alt=a)
        s, c, a = _mods("mod_set_mode_draw")
        _add("bname.set_mode_draw", _key("key_set_mode_draw", "P"),
             shift=s, ctrl=c, alt=a)
        s, c, a = _mods("mod_page_next")
        _add("bname.page_next", _key("key_page_next", "COMMA"),
             shift=s, ctrl=c, alt=a)
        s, c, a = _mods("mod_page_prev")
        _add("bname.page_prev", _key("key_page_prev", "PERIOD"),
             shift=s, ctrl=c, alt=a)

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
        """B-Name が追加した keymap_items だけを削除し、標準キーマップは残す.

        ``BNAME_KEYMAP_NAME = "3D View"`` (Blender 標準キーマップ名) に
        相乗りしているため、kc.keymaps.remove(km) を呼ぶと標準操作が
        全部消える。ここでは bname_items のみ remove する。
        """
        wm = bpy.context.window_manager
        if wm is None:
            self.bname_keymaps.clear()
            self.bname_items.clear()
            return
        # km / kmi の C 参照が既に無効化されている可能性があるため、
        # 個別 try で防御し、最後にリストを必ずクリアする。
        for km in self.bname_keymaps:
            for kmi in list(self.bname_items):
                try:
                    km.keymap_items.remove(kmi)
                except Exception:  # noqa: BLE001
                    pass
        self.bname_keymaps.clear()
        self.bname_items.clear()
        _logger.debug("bname keymap items removed (standard '3D View' keymap kept)")

    # ---------- 既定キーマップ退避/復元 ----------
    # NOTE (deprecated): override_defaults / restore_defaults は廃止された。
    # B-Name は addon kc の "3D View" キーマップに kmi を追加するだけで、
    # Blender のキーマップ評価優先順 (addon > user > default) によって
    # 自動的に既定操作より優先される。default kc の active プロパティを
    # 書き換える方式は、アドオン無効化中に Blender 内部のキーマップ
    # 再構築とレースして C レベル segfault を起こすため除去した。
    # これらのメソッドは互換維持のため残してあるが no-op 化している。

    def override_defaults(
        self, combos: Iterable = _BNAME_EXCLUSIVE_COMBOS
    ) -> int:
        """[NO-OP] 既定キーマップ退避は廃止された.

        addon kc に "3D View" 同名キーマップで kmi を追加すれば
        Blender のキーマップ評価が自動的に addon kc を優先するため、
        default kc 側を書き換える必要がない。書き換える方式はアドオン
        無効化中に Blender 内部のキーマップ再構築とレースして
        EXCEPTION_ACCESS_VIOLATION を起こすため除去した。
        """
        # enabled フラグだけ立てておく (watcher の再呼び出しを抑制)
        self.enabled = True
        return 0

    def restore_defaults(self) -> None:
        """[NO-OP] 既定キーマップ復元は廃止された (override_defaults 参照)."""
        self.saved.clear()
        self.enabled = False

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

    register 時に ``window_manager`` / ``keyconfigs.addon`` がまだ整っておら
    ず ``create_bname_keymap`` が失敗した場合に備え、毎 tick ``bname_items``
    が空なら作成をリトライする。これがないと「ショートカットが一つも効か
    ない」状態が永久に続く。
    """
    state = _state
    if state is None:
        return None  # タイマー停止
    try:
        from ..preferences import get_preferences

        prefs = get_preferences()
        enabled = True if prefs is None else bool(prefs.keymap_enabled)

        # キーマップ未作成 (register 時に wm/addon keyconfig が None だった等) なら再試行
        if not state.bname_items:
            km = state.create_bname_keymap()
            if km is not None:
                _logger.info(
                    "bname keymap recreated by watcher (items=%d)",
                    len(state.bname_items),
                )

        if enabled:
            # set_bname_items_active は内部で変化判定付き = 冪等なので毎 tick 呼んで OK
            state.set_bname_items_active(True)
            if not state.enabled and state.bname_items:
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
    print("[B-Name][KEYMAP] register() called")
    _state = KeymapState()
    preset = KeymapState.detect_preset_name()
    print(f"[B-Name][KEYMAP] detected preset: {preset or '(unknown)'}")
    _logger.info("detected keymap preset: %s", preset or "(unknown)")

    # Preferences に従い、B-Name キーマップを有効化するかを決める
    from ..preferences import get_preferences

    prefs = get_preferences()
    keymap_enabled = True if prefs is None else bool(prefs.keymap_enabled)

    km = _state.create_bname_keymap()
    if km is None or not _state.bname_items:
        # wm / keyconfigs.addon がまだ整っていない (Blender 起動直後のアドオン
        # 自動有効化等)。watcher が後で再試行するので fatal ではない。
        print(
            "[B-Name][KEYMAP] register: keymap NOT created at register-time;"
            f" watcher will retry every {_WATCH_INTERVAL:.1f}s"
        )
        _logger.warning(
            "bname keymap not created at register-time (wm/addon keyconfig unavailable);"
            " watcher will retry every %.1fs",
            _WATCH_INTERVAL,
        )
    # B-Name キーマップを **register 時点で即時 active 化**.
    # 旧実装は watcher 起動を待っていたが、Blender 5.x で watcher が
    # 効くまでに UI 操作が走るとショートカットが反応しないため、最初から
    # 有効化しておく。Disable は preferences.keymap_enabled を OFF に切替
    # した時のみ watcher が反映する。
    if keymap_enabled:
        _state.set_bname_items_active(True)
        if _state.bname_items:
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


def rebuild_keymap_from_prefs() -> None:
    """preferences のショートカット設定が変わった時に呼ぶ.

    既存の bname_items を全て remove → preferences を再読込で keymap を作り直す。
    アドオン無効化中などで _state が None なら何もしない。
    """
    state = _state
    if state is None:
        return
    print("[B-Name][KEYMAP] rebuild_keymap_from_prefs() triggered")
    try:
        # 既存のアイテムを掃除 (既存 keymap オブジェクト自体は標準 "3D View" /
        # "Window" を参照しているため remove しない)
        state.remove_bname_keymaps()
    except Exception:  # noqa: BLE001
        _logger.exception("rebuild: remove_bname_keymaps failed")
    try:
        state.create_bname_keymap()
        from ..preferences import get_preferences
        prefs = get_preferences()
        enabled = True if prefs is None else bool(prefs.keymap_enabled)
        if enabled:
            state.set_bname_items_active(True)
        else:
            state.set_bname_items_active(False)
    except Exception:  # noqa: BLE001
        _logger.exception("rebuild: create_bname_keymap failed")
