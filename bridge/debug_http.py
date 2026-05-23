"""HTTP debug server.

Exposes the ring buffer at:
  http://<pi-ip>:8080/debug          HTML view (auto-refreshes)
  http://<pi-ip>:8080/debug.json     raw JSON
  http://<pi-ip>:8080/health         {"ok": true}

Bound to 0.0.0.0 by default. Suppresses stdlib's per-request access
log so it doesn't compete with the application log.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from . import logger


_HTML_TEMPLATE = """<!doctype html>
<html><head>
<title>midibridge /debug</title>
<meta http-equiv="refresh" content="2">
<style>
  body {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px;
          background: #111; color: #ddd; margin: 0; padding: 1rem; }}
  h1 {{ font-size: 14px; margin: 0 0 0.5rem 0; color: #8af; }}
  .meta {{ color: #888; margin-bottom: 1rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  td {{ padding: 1px 8px; vertical-align: top; white-space: nowrap;
        border-bottom: 1px solid #222; }}
  td.ts {{ color: #888; }}
  td.lvl {{ color: #8af; width: 70px; }}
  td.lvl.WARNING {{ color: #fc6; }}
  td.lvl.ERROR {{ color: #f66; }}
  td.kind {{ color: #6c6; width: 80px; }}
  td.msg {{ color: #ddd; white-space: pre-wrap; }}
</style>
</head><body>
<h1>midibridge /debug</h1>
<div class="meta">events: {n}, ring size: {ring_size}. auto-refresh 2s. <a href="/debug.json" style="color:#8af">json</a></div>
<table>{rows}</table>
</body></html>"""


def _format_event(ev: dict) -> str:
    import time as _t
    ts = _t.strftime("%H:%M:%S", _t.localtime(ev["ts"]))
    kind = ev["kind"]
    lvl = ev["level"]
    msg = ev["msg"]
    if ev.get("data"):
        kv = " ".join(f"{k}={v}" for k, v in ev["data"].items())
        msg = f"{msg}  {kv}"
    # Basic HTML escape -- enough for our content
    msg = (msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return f'<tr><td class="ts">{ts}</td><td class="lvl {lvl}">{lvl}</td><td class="kind">{kind}</td><td class="msg">{msg}</td></tr>'


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # silence access log
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/debug?") or self.path == "/debug":
            events = logger.get_ring()
            # Newest at the top
            rows = "\n".join(_format_event(e) for e in reversed(events))
            html = _HTML_TEMPLATE.format(
                n=len(events), ring_size=events.__len__(), rows=rows
            )
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/debug.json":
            body = json.dumps(logger.get_ring(), indent=2).encode("utf-8")
            self._send(200, body, "application/json")
        elif self.path == "/health":
            self._send(200, b'{"ok": true}\n', "application/json")
        else:
            self._send(404, b"not found\n", "text/plain")


class DebugServer:
    def __init__(self, port: int = 8080):
        self.port = port
        self._srv: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        # Reuse-address: lets us reclaim the port immediately if another
        # process just released it (kept by the kernel in TIME_WAIT).
        class _ReusableServer(ThreadingHTTPServer):
            allow_reuse_address = True

        try:
            self._srv = _ReusableServer(("0.0.0.0", self.port), _Handler)
        except OSError as e:
            logger.warn("debug-http", "could not bind debug port",
                        port=self.port, err=str(e))
            self._srv = None
            return
        self._thread = threading.Thread(
            target=self._srv.serve_forever, name="debug-http", daemon=True
        )
        self._thread.start()
        logger.info("debug-http", "listening", port=self.port)

    def stop(self) -> None:
        if self._srv is not None:
            try:
                self._srv.shutdown()
                self._srv.server_close()
            except Exception:
                pass
            self._srv = None
