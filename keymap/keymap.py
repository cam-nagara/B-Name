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

# 退避候補となる既定キーマップ項目。
# (keymap_name, idname, {filter_attr: value, ...}) のタプル列挙。
# filter にマッチした KeyMapItem を見つけたら active=False にして退避。
_CANDIDATE_OVERRIDES: tuple = (
    # Space バー: 既定は Preset により Play/Search/Tool に割り振られる
    ("Screen", "screen.animation_play", {"type": "SPACE"}),
    ("Screen", "wm.search_menu", {"type": "SPACE"}),
    ("Screen Editing", "wm.search_menu", {"type": "SPACE"}),
    # ビューポート右クリック (Context Menu) — スポイトに割り当てる際に退避
    ("3D View", "wm.call_panel", {"type": "RIGHTMOUSE"}),
    ("3D View", "wm.call_menu", {"type": "RIGHTMOUSE"}),
    # Shift+ホイール (Blender 既定で水平スクロール等)
    ("3D View", "view3d.view_orbit", {"type": "WHEELUPMOUSE", "shift": True}),
    ("3D View", "view3d.view_orbit", {"type": "WHEELDOWNMOUSE", "shift": True}),
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
        _logger.debug("bname keymap items: %d", len(self.bname_items))

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

    def override_defaults(self, candidates: Iterable = _CANDIDATE_OVERRIDES) -> int:
        """候補に合致する既定キーマップアイテムを退避しつつ無効化.

        Returns: 退避した項目数。
        """
        wm = bpy.context.window_manager
        if wm is None:
            return 0
        kc = wm.keyconfigs.default
        if kc is None:
            _logger.warning("default keyconfig unavailable; skip override")
            return 0
        count = 0
        for km_name, idname, filt in candidates:
            km = kc.keymaps.get(km_name)
            if km is None:
                continue
            for kmi in km.keymap_items:
                if kmi.idname != idname:
                    continue
                if not _match_filter(kmi, filt):
                    continue
                if not kmi.active:
                    continue  # 既に無効ならノータッチ
                self.saved.append(
                    _SavedItem(
                        keymap_name=km_name,
                        idname=idname,
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
        self.enabled = count > 0 or self.enabled
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


def get_state() -> Optional[KeymapState]:
    return _state


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
    if keymap_enabled:
        _state.override_defaults()
    _logger.debug("keymap registered (enabled=%s)", keymap_enabled)


def unregister() -> None:
    global _state
    if _state is None:
        return
    try:
        _state.restore_defaults()
    finally:
        _state.remove_bname_keymaps()
    _state = None
    _logger.debug("keymap unregistered")
