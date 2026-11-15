"""
Tiny HTTP server for the one-time firmware confirmation link.

  GET  /firmware/confirm/<token>   page naming product + version with two buttons (changes nothing)
  POST /firmware/confirm/<token>   action=installed|cancel: confirm or cancel, then start a
                                   navdata run so an inserted card is restored right away

Nothing else is served. Tokens are long, random and single use; anyone holding the link can
confirm, so keep it private. Tokens are never logged.
"""

import html
import logging
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs

from avcardtool.firmware.registry import PRODUCTS
from avcardtool.firmware.state import FirmwareState

logger = logging.getLogger(__name__)

MAX_BODY = 4096
REQUEST_TIMEOUT = 10
_ROUTE = re.compile(r"^/firmware/confirm/([A-Za-z0-9_-]{20,128})$")
_LOCK = threading.Lock()

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>{title}</title><style>body{{font:18px/1.4 system-ui,sans-serif;margin:0 auto;max-width:30em;padding:1em}}
button{{display:block;width:100%;font-size:1em;padding:.8em;margin:.6em 0}}</style></head>
<body><h1>{title}</h1>{body}</body></html>"""


def launch_navdata_run(cfg) -> None:
    """Start 'navdata auto-update' detached, so a card in the reader is restored right away."""
    cmd = [sys.executable, "-m", "avcardtool.cli"]
    if getattr(cfg, "config_path", None):
        cmd += ["--config", str(cfg.config_path)]
    cmd += ["navdata", "auto-update"]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


class ConfirmHandler(BaseHTTPRequestHandler):
    server_version = "avcardtool"
    sys_version = ""
    timeout = REQUEST_TIMEOUT

    def log_message(self, format, *args):  # never log request lines: they contain the token
        pass

    # -- helpers --------------------------------------------------------------
    def _send(self, status: int, title: str, body: str) -> None:
        data = _PAGE.format(title=html.escape(title), body=body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _not_found(self) -> None:
        self._send(404, "Not found", "<p>This link is not valid (it may already have been used).</p>")

    def _token(self) -> Optional[str]:
        m = _ROUTE.match(self.path.split("?", 1)[0])
        return m.group(1) if m else None

    def _lookup(self, state: FirmwareState, token: str):
        found = state.find_by_token(token)
        if not found:
            return None
        serial, key = found
        return serial, key, state.pending(serial, key)

    # -- verbs ----------------------------------------------------------------
    def do_GET(self):
        try:
            token = self._token()
            if not token:
                return self._not_found()
            item = self._lookup(FirmwareState(self.server.data_dir), token)
            if not item:
                return self._not_found()
            _serial, key, pend = item
            name = html.escape(PRODUCTS[key].name)
            version = html.escape(str(pend.get("version")))
            action = f"/firmware/confirm/{html.escape(token)}"
            body = (f"<p>{name} software {version} was written to the database card.</p>"
                    f'<form method="post" action="{action}"><input type="hidden" name="action" value="installed">'
                    "<button>Firmware installed successfully &mdash; restore databases</button></form>"
                    f'<form method="post" action="{action}"><input type="hidden" name="action" value="cancel">'
                    "<button>Not installing &mdash; restore databases</button></form>")
            self._send(200, f"{PRODUCTS[key].name} software {pend.get('version')}", body)
        except Exception:
            self._send(500, "Error", "<p>Something went wrong.</p>")

    def do_POST(self):
        try:
            token = self._token()
            if not token:
                return self._not_found()
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._send(411, "Length required", "<p>Content-Length is required.</p>")
            if length < 0 or length > MAX_BODY:
                self.close_connection = True
                return self._send(413, "Too large", "<p>Request too large.</p>")
            fields = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
            action = (fields.get("action") or [""])[0]
            if action not in ("installed", "cancel"):
                return self._send(400, "Bad request", "<p>Unknown action.</p>")
            with _LOCK:
                state = FirmwareState(self.server.data_dir)
                item = self._lookup(state, token)
                if not item:
                    return self._not_found()
                serial, key, _pend = item
                version = state.confirm(serial, key) if action == "installed" else state.cancel(serial, key)
                state.save()
            logger.info(f"Firmware cycle for {key} {version}: {'confirmed' if action == 'installed' else 'cancelled'}")
            try:
                self.server.launcher()
            except Exception:
                logger.exception("Could not start the navdata run")
            self._send(200, "Thank you", "<p>Recorded. The card's databases will be reinstalled the next "
                       "time the card is in the reader.</p>")
        except Exception:
            self._send(500, "Error", "<p>Something went wrong.</p>")


def make_server(data_dir, host: str, port: int, launcher: Callable[[], None]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), ConfirmHandler)
    server.data_dir = Path(data_dir)
    server.launcher = launcher
    server.daemon_threads = True
    return server


def run_server(cfg) -> None:
    host, _, port = cfg.firmware.confirm_listen.rpartition(":")
    server = make_server(cfg.system.data_dir, host.strip("[]"), int(port), lambda: launch_navdata_run(cfg))
    logger.info(f"Firmware confirmation server listening on {cfg.firmware.confirm_listen}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
