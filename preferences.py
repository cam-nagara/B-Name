"""B-Name AddonPreferences.

Phase 0 時点では以下を提供:
- ログレベル
- Meldex 受信サーバーのポート（Phase 5 で利用、UI は先に用意）
- B-Name 専用キーマップのトグル
- 右クリック=スポイト モードのスイッチ
- Spaceバー既定挙動の退避情報（デバッグ表示）
- アセットライブラリ登録ガイド

``__package__`` が ``b_name`` のようなアドオン ID 名を指す前提。
Blender 4.3+ / 5.x の Extensions Platform 配下では ``bl_idname`` に
``__package__`` を使う。
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from .utils import log

ADDON_ID = __package__ or "b_name"

_LOG_LEVEL_ITEMS = (
    ("DEBUG", "Debug", "詳細ログ"),
    ("INFO", "Info", "標準ログ (既定)"),
    ("WARNING", "Warning", "警告以上のみ"),
    ("ERROR", "Error", "エラーのみ"),
)

_SPACEBAR_PRESET_ITEMS = (
    ("AUTO", "Auto", "現在の Blender 設定を検出して自動選択"),
    ("TOOL", "Tool", "Space = ツール切替 (Blender 既定)"),
    ("SEARCH", "Search", "Space = 検索メニュー"),
    ("PLAY", "Playback", "Space = 再生"),
)


def _on_log_level_changed(self, _context) -> None:  # noqa: ANN001 - Blender callback
    log.set_level(self.log_level)


def _on_gpencil_follow_changed(prefs) -> None:
    """preferences.gpencil_follow_cursor 変更で watcher を即時起動/停止.

    アドオン register/unregister の過渡状態 (operators モジュールがまだ
    完全に初期化されていない / 既に unregister 済) でも安全に no-op
    できるよう、全例外を握り潰す。
    """
    try:
        from .operators import gpencil_op

        if bool(prefs.gpencil_follow_cursor):
            gpencil_op._follow_start()
        else:
            gpencil_op._follow_stop()
    except Exception:  # noqa: BLE001
        pass


def _on_keymap_settings_changed(self, _context) -> None:
    """preferences のキーマップ設定が変わったら addon kc を再構築する.

    register/unregister 中に呼ばれた場合は keymap モジュールが未初期化の
    可能性があるので例外を握り潰す。
    """
    try:
        from .keymap import keymap as _kmap

        _kmap.rebuild_keymap_from_prefs()
    except Exception:  # noqa: BLE001
        pass


class BNamePreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    log_level: EnumProperty(  # type: ignore[valid-type]
        name="ログレベル",
        description="B-Name アドオンのログレベル",
        items=_LOG_LEVEL_ITEMS,
        default="INFO",
        update=_on_log_level_changed,
    )

    meldex_port: IntProperty(  # type: ignore[valid-type]
        name="Meldex 受信ポート",
        description="Meldex からのシナリオ受信に使う localhost ポート (Phase 5)",
        default=47817,
        min=1024,
        max=65535,
    )

    keymap_enabled: BoolProperty(  # type: ignore[valid-type]
        name="B-Name 専用キーマップを有効化",
        description="CLIP STUDIO PAINT 準拠のビューポート操作ショートカットを有効にする",
        default=True,
    )

    right_click_eyedropper: BoolProperty(  # type: ignore[valid-type]
        name="右クリックをスポイトに割り当てる",
        description="B-Name モード中のみ、右クリックの既定動作をスポイトに切り替える",
        default=False,
    )

    spacebar_preset: EnumProperty(  # type: ignore[valid-type]
        name="Spaceバー挙動 (検出用)",
        description="既定キーマップの Space キー挙動。AUTO 以外は退避処理のベースとして使用",
        items=_SPACEBAR_PRESET_ITEMS,
        default="AUTO",
    )

    global_asset_library: StringProperty(  # type: ignore[valid-type]
        name="グローバルアセットライブラリ パス",
        description="全作品共通で参照するアセットの格納先 (Blender 設定でアセットライブラリとして登録)",
        default=r"D:\Develop\Blender\B-Name-Assets",
        subtype="DIR_PATH",
    )

    gpencil_follow_cursor: BoolProperty(  # type: ignore[valid-type]
        name="カーソル追従で active GP 切替",
        description=(
            "overview モード中、マウス位置のページ GP を自動で active に切替える "
            "(Blender 標準のドロー/消しゴムを overview 全ページに適用するため)"
        ),
        default=True,
        update=lambda self, _ctx: _on_gpencil_follow_changed(self),
    )

    # ---------- ショートカットキーのカスタマイズ ----------
    # 各機能ごとに「キー文字列 + Shift/Ctrl/Alt 修飾」を保持する。
    # キー文字列は Blender の Event.type 名 (例: "SPACE", "O", "P",
    # "COMMA", "PERIOD", "LEFTMOUSE", "WHEELUPMOUSE")。
    # 値が変わると _on_keymap_settings_changed が addon kc を作り直す。

    key_navigate: StringProperty(  # type: ignore[valid-type]
        name="ナビゲート (パン/回転/ズーム統合)",
        description="このキー押下中の LMB ドラッグでパン/回転/ズーム",
        default="SPACE",
        update=_on_keymap_settings_changed,
    )

    key_set_mode_object: StringProperty(  # type: ignore[valid-type]
        name="オブジェクトモード切替",
        default="O",
        update=_on_keymap_settings_changed,
    )
    mod_set_mode_object_shift: BoolProperty(  # type: ignore[valid-type]
        name="Shift", default=False, update=_on_keymap_settings_changed
    )
    mod_set_mode_object_ctrl: BoolProperty(  # type: ignore[valid-type]
        name="Ctrl", default=False, update=_on_keymap_settings_changed
    )
    mod_set_mode_object_alt: BoolProperty(  # type: ignore[valid-type]
        name="Alt", default=False, update=_on_keymap_settings_changed
    )

    key_set_mode_draw: StringProperty(  # type: ignore[valid-type]
        name="描画モード切替",
        default="P",
        update=_on_keymap_settings_changed,
    )
    mod_set_mode_draw_shift: BoolProperty(  # type: ignore[valid-type]
        name="Shift", default=False, update=_on_keymap_settings_changed
    )
    mod_set_mode_draw_ctrl: BoolProperty(  # type: ignore[valid-type]
        name="Ctrl", default=False, update=_on_keymap_settings_changed
    )
    mod_set_mode_draw_alt: BoolProperty(  # type: ignore[valid-type]
        name="Alt", default=False, update=_on_keymap_settings_changed
    )

    key_page_next: StringProperty(  # type: ignore[valid-type]
        name="次のページ",
        default="COMMA",
        update=_on_keymap_settings_changed,
    )
    mod_page_next_shift: BoolProperty(  # type: ignore[valid-type]
        name="Shift", default=False, update=_on_keymap_settings_changed
    )
    mod_page_next_ctrl: BoolProperty(  # type: ignore[valid-type]
        name="Ctrl", default=False, update=_on_keymap_settings_changed
    )
    mod_page_next_alt: BoolProperty(  # type: ignore[valid-type]
        name="Alt", default=False, update=_on_keymap_settings_changed
    )

    key_page_prev: StringProperty(  # type: ignore[valid-type]
        name="前のページ",
        default="PERIOD",
        update=_on_keymap_settings_changed,
    )
    mod_page_prev_shift: BoolProperty(  # type: ignore[valid-type]
        name="Shift", default=False, update=_on_keymap_settings_changed
    )
    mod_page_prev_ctrl: BoolProperty(  # type: ignore[valid-type]
        name="Ctrl", default=False, update=_on_keymap_settings_changed
    )
    mod_page_prev_alt: BoolProperty(  # type: ignore[valid-type]
        name="Alt", default=False, update=_on_keymap_settings_changed
    )

    def draw(self, context) -> None:  # noqa: D401, ANN001
        layout = self.layout

        box = layout.box()
        box.label(text="ログ / デバッグ")
        box.prop(self, "log_level")

        box = layout.box()
        box.label(text="Meldex 連携 (Phase 5)")
        box.prop(self, "meldex_port")

        box = layout.box()
        box.label(text="キーマップ")
        box.prop(self, "keymap_enabled")
        sub = box.column()
        sub.enabled = self.keymap_enabled
        sub.prop(self, "right_click_eyedropper")
        sub.prop(self, "spacebar_preset")

        # ショートカットキー カスタマイズ
        kbox = layout.box()
        kbox.label(text="ショートカットキー (変更後は自動反映)")
        kbox.enabled = self.keymap_enabled

        row = kbox.row(align=True)
        row.label(text="ナビゲート (パン/回転/ズーム)", icon="ORIENTATION_VIEW")
        row.prop(self, "key_navigate", text="")

        for label, key_attr, mod_prefix, icon in (
            ("オブジェクトモード", "key_set_mode_object", "mod_set_mode_object", "OBJECT_DATAMODE"),
            ("描画モード", "key_set_mode_draw", "mod_set_mode_draw", "GREASEPENCIL"),
            ("次のページ", "key_page_next", "mod_page_next", "TRIA_RIGHT"),
            ("前のページ", "key_page_prev", "mod_page_prev", "TRIA_LEFT"),
        ):
            row = kbox.row(align=True)
            row.label(text=label, icon=icon)
            row.prop(self, f"{mod_prefix}_shift", toggle=True)
            row.prop(self, f"{mod_prefix}_ctrl", toggle=True)
            row.prop(self, f"{mod_prefix}_alt", toggle=True)
            row.prop(self, key_attr, text="")

        kbox.separator()
        info = kbox.column(align=True)
        info.scale_y = 0.85
        info.label(text="キー名は Blender のイベント名 (例: SPACE, O, P, COMMA, PERIOD, A〜Z, F1〜F12)", icon="INFO")
        info.label(text="ナビゲートのモード切替はキー押下中の Shift=回転 / Ctrl=ズーム (固定)")
        info.label(text="ズーム中の LMB クリック=25%イン / Alt+LMB クリック=25%アウト (固定)")
        info.label(text="描画モード中: Space=ナビゲート / C=ブラシシェルフ表示切替 (Blender既定の入れ替え)")

        box = layout.box()
        box.label(text="アセットライブラリ登録ガイド")
        box.prop(self, "global_asset_library")
        col = box.column(align=True)
        col.label(text="1. 上のパスを Blender 本体の Preferences > File Paths > Asset Libraries に追加")
        col.label(text="2. 作品固有アセットは MyWork.bname/assets/ 配下 (B-Name が自動管理)")
        col.label(text="3. コマ編集モード中にアセットブラウザからドラッグ&ドロップでリンク参照")

        box = layout.box()
        box.label(text="Grease Pencil (overview)")
        box.prop(self, "gpencil_follow_cursor")


def get_preferences(context=None) -> "BNamePreferences | None":
    ctx = context or bpy.context
    prefs = ctx.preferences.addons.get(ADDON_ID)
    return prefs.preferences if prefs else None


_CLASSES = (BNamePreferences,)


def register() -> None:
    logger = log.get_logger(__name__)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    prefs = get_preferences()
    if prefs is not None:
        log.set_level(prefs.log_level)
    logger.debug("preferences registered")


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            # 既に解除されている場合は黙殺
            pass
