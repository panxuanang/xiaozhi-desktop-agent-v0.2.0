from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .paths import QR_FILE, UI_DIR
from .runtime import XiaoZhiRuntime

log = logging.getLogger(__name__)


class WebUIServer:
    def __init__(self, runtime: XiaoZhiRuntime, host: str = "127.0.0.1", port: int = 8765):
        self.runtime = runtime
        self.host = host
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None

    def start(self) -> None:
        runtime = self.runtime

        class Handler(BaseHTTPRequestHandler):
            server_version = "XiaoZhiLocalUI/0.2"

            def log_message(self, fmt: str, *args):
                log.info("ui %s", fmt % args)

            def _json(self, data, status=200):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 40 * 1024 * 1024:
                    raise RuntimeError("请求体过大")
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                    return data if isinstance(data, dict) else {}
                except Exception:
                    return {}

            def do_GET(self):  # noqa: N802
                path = urlparse(self.path).path
                if path == "/api/status":
                    self._json(runtime.status())
                    return
                if path == "/api/tasks":
                    tasks = runtime.center.list_tasks(200)
                    for t in tasks:
                        t.pop("context_token", None)
                        t.pop("decision", None)
                    self._json({"tasks": tasks})
                    return
                m = re.fullmatch(r"/api/tasks/(TASK[-\w]+)", path, re.I)
                if m:
                    try:
                        self._json({"ok": True, "task": runtime.get_task(m.group(1).upper())})
                    except KeyError:
                        self._json({"ok": False, "error": "task not found"}, status=404)
                    return
                if path == "/api/weixin/qr":
                    if not QR_FILE.exists():
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    data = QR_FILE.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path == "/" or path == "/index.html":
                    data = (UI_DIR / "index.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                static = UI_DIR / path.lstrip("/")
                if static.exists() and static.is_file():
                    data = static.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(static.name)[0] or "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self):  # noqa: N802
                path = urlparse(self.path).path
                try:
                    data = self._body()
                    if path == "/api/config":
                        cfg = runtime.save_config(data)
                        self._json({
                            "ok": True,
                            "workspace": cfg.workspace,
                            "planner_provider": cfg.planner_provider,
                            "planner_model": cfg.planner_model,
                            "direct_provider": cfg.direct_provider,
                            "direct_model": cfg.direct_model,
                            "harness_model": cfg.model,
                        })
                        return
                    if path == "/api/test-api":
                        provider = str(data.get("provider") or "deepseek")
                        self._json({"ok": True, "response": runtime.test_api(provider), "provider": provider})
                        return
                    if path == "/api/chat/send":
                        task = runtime.desktop_chat(str(data.get("text") or ""), data.get("attachments") if isinstance(data.get("attachments"), list) else [])
                        self._json({"ok": True, "task": task})
                        return
                    m = re.fullmatch(r"/api/tasks/(TASK[-\w]+)/(approve|cancel|replan|approve-output)", path, re.I)
                    if m:
                        task_id = m.group(1).upper()
                        action = m.group(2).lower()
                        if action == "approve":
                            task = runtime.approve_plan(task_id)
                        elif action == "cancel":
                            task = runtime.cancel_plan(task_id)
                        elif action == "replan":
                            task = runtime.replan(task_id, str(data.get("instruction") or ""))
                        else:
                            contact = str(data.get("contact") or "").strip() or None
                            task = runtime.approve_output(task_id, contact)
                        self._json({"ok": True, "task": task})
                        return
                    if path == "/api/open-path":
                        runtime.open_path(str(data.get("path") or ""))
                        self._json({"ok": True})
                        return
                    if path == "/api/browse-workspace":
                        self._json({"ok": True, "path": runtime.browse_workspace()})
                        return
                    if path == "/api/weixin/login":
                        runtime.start_weixin_login(force=bool(data.get("force", False)))
                        self._json({"ok": True})
                        return
                    if path == "/api/weixin/verify":
                        runtime.submit_weixin_verify(str(data.get("code") or ""))
                        self._json({"ok": True})
                        return
                    if path == "/api/weixin/start":
                        runtime.channel.start_polling()
                        self._json({"ok": True})
                        return
                    if path == "/api/weixin/stop":
                        runtime.channel.stop()
                        self._json({"ok": True})
                        return
                    if path == "/api/open-workspace":
                        runtime.open_workspace()
                        self._json({"ok": True})
                        return
                    if path == "/api/open-logs":
                        runtime.open_logs()
                        self._json({"ok": True})
                        return
                    if path == "/api/shutdown":
                        self._json({"ok": True})
                        threading.Thread(target=runtime.shutdown, daemon=True).start()
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                        return
                    self.send_error(HTTPStatus.NOT_FOUND)
                except Exception as exc:
                    log.exception("UI API failed: %s", path)
                    self._json({"ok": False, "error": str(exc)}, status=500)

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.httpd.serve_forever(poll_interval=0.5)
