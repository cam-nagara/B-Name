"""Meldex からのシナリオ受信 (ローカル HTTP サーバー).

計画書 3.5 / 8.1 参照。別スレッドで ThreadingHTTPServer を起動し、
受信データは Queue に積む。bpy.app.timers でメインスレッドから poll して
bpy.data を操作する。

スレッドセーフティ:
- 受信ハンドラ内では bpy.* を一切呼ばない
- unregister 時に shutdown → join → timer 解除 を確実に実行
- localhost 以外からの接続は拒否
"""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

import bpy

from ..core.work import get_active_page, get_work
from ..utils import json_io, log, paths

_logger = log.get_logger(__name__)

# --- グローバル状態 ---
_server: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_in_queue: "queue.Queue[dict]" = queue.Queue()
_timer_registered: bool = False

_DEFAULT_PORT = 47817
_MAX_PORT_TRIES = 10


# ---------- HTTP ハンドラ ----------


class _MeldexHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A003 - BaseHTTPRequestHandler 互換
        _logger.debug("meldex http: " + format, *args)

    def _check_localhost(self) -> bool:
        client_host = self.client_address[0]
        return client_host in ("127.0.0.1", "::1", "localhost")

    def do_POST(self) -> None:  # noqa: N802 - HTTP 規約
        if not self._check_localhost():
            self.send_error(403, "forbidden (non-localhost)")
            return
        if self.path not in ("/scenario", "/api/scenario"):
            self.send_error(404, "unknown endpoint")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            self.send_error(400, "empty body")
            return
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.send_error(400, f"invalid json: {exc}")
            return
        _in_queue.put({"type": "scenario", "payload": data})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"accepted"}')

    def do_GET(self) -> None:  # noqa: N802 - 監視用
        if not self._check_localhost():
            self.send_error(403, "forbidden")
            return
        if self.path != "/health":
            self.send_error(404, "unknown endpoint")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok","app":"B-Name"}')


# ---------- サーバー起動・停止 ----------


def _start_server(port: int) -> Optional[ThreadingHTTPServer]:
    for offset in range(_MAX_PORT_TRIES):
        try_port = port + offset
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", try_port), _MeldexHandler)
            _logger.info("meldex receiver listening on http://127.0.0.1:%d", try_port)
            return srv
        except OSError as exc:
            _logger.warning("port %d unavailable: %s", try_port, exc)
    return None


def start(port: int = _DEFAULT_PORT) -> bool:
    global _server, _server_thread, _timer_registered
    if _server is not None:
        return True  # already running
    _server = _start_server(port)
    if _server is None:
        _logger.error("no available port in range %d..%d", port, port + _MAX_PORT_TRIES - 1)
        return False
    _server_thread = threading.Thread(
        target=_server.serve_forever, name="BName-MeldexReceiver", daemon=True
    )
    _server_thread.start()
    # メインスレッド poll を bpy.app.timers で実行
    if not _timer_registered:
        bpy.app.timers.register(_poll_queue, first_interval=0.1, persistent=True)
        _timer_registered = True
    return True


def stop() -> None:
    global _server, _server_thread, _timer_registered
    if _server is None:
        return
    try:
        _server.shutdown()
        _server.server_close()
    except OSError:
        pass
    if _server_thread is not None:
        _server_thread.join(timeout=3.0)
    _server = None
    _server_thread = None
    if _timer_registered:
        try:
            bpy.app.timers.unregister(_poll_queue)
        except (ValueError, KeyError):
            pass
        _timer_registered = False
    _logger.info("meldex receiver stopped")


# ---------- Queue poll (メインスレッド) ----------


def _poll_queue() -> float:
    """Blender メインスレッドから定期呼出. bpy.data 操作はここで行う."""
    try:
        while True:
            try:
                message = _in_queue.get_nowait()
            except queue.Empty:
                break
            _handle_message(message)
    except Exception:  # noqa: BLE001
        _logger.exception("_poll_queue failed")
    return 0.5  # 次回 0.5 秒後


def _handle_message(message: dict) -> None:
    mtype = message.get("type")
    payload = message.get("payload", {})
    if mtype == "scenario":
        _ingest_scenario(payload)
    else:
        _logger.warning("unknown message type: %s", mtype)


def _ingest_scenario(payload: dict) -> None:
    """Meldex から受信したシナリオを作品に取り込む.

    payload の形式 (暫定):
      {"workName": "...", "episode": 1, "pages": [
         {"comas": [{"text": "...", "speakerType": "normal", "rubies": [...]}]},
         ...]}
    """
    work = get_work()
    if work is None or not work.loaded or not work.work_dir:
        _logger.warning("scenario received but no work loaded")
        return
    work_dir = Path(work.work_dir)

    # シナリオ原本を scenario/imported.json に保存
    try:
        out = paths.scenario_file(work_dir)
        json_io.write_json(out, payload)
        _logger.info("scenario saved: %s", out)
    except OSError as exc:
        _logger.error("failed to save scenario: %s", exc)

    # 作品情報の更新 (上書き)
    if "workName" in payload:
        work.work_info.work_name = str(payload["workName"])
    if "episode" in payload:
        try:
            work.work_info.episode_number = int(payload["episode"])
        except (TypeError, ValueError):
            pass

    # Phase 5 骨格: ページ/コマ雛形生成は TODO
    # (ページ自動生成は pages_json への追加と ensure_page_dir の組合せで実装、
    #  Phase 5 後半で詳細ロジックを入れる)
    _logger.info(
        "scenario ingested: %d pages (skeleton only)",
        len(payload.get("pages", [])),
    )


# ---------- register / unregister ----------


def register() -> None:
    # Preferences から port を取得して自動起動する。失敗しても register 自体は成功扱い。
    from ..preferences import get_preferences

    prefs = get_preferences()
    port = _DEFAULT_PORT if prefs is None else int(prefs.meldex_port)
    start(port)


def unregister() -> None:
    stop()
