"""ビューポート操作モーダルオペレータ (Phase 0 ではプレースホルダ).

Phase 1 以降で以下を実装:
- Space + ドラッグ       → パン
- Shift + Space + ドラッグ → 回転 (カメラリグのロール)
- Ctrl + Space + クリック  → ズームイン
- Alt + Space + クリック   → ズームアウト
- Ctrl + Space + ドラッグ  → ズーム (連続)
- Ctrl + Shift + クリック/ドラッグ → レイヤー選択
- 右クリック               → スポイト (Preferences で ON のとき)

本フェーズでは Operator クラスを登録せず、モジュールの register/unregister
だけ存在させておく。
"""

from __future__ import annotations

from ..utils import log

_logger = log.get_logger(__name__)


def register() -> None:
    _logger.debug("viewport_ops: stub register (Phase 0)")


def unregister() -> None:
    _logger.debug("viewport_ops: stub unregister (Phase 0)")
