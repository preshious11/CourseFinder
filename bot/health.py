"""Optional health-check HTTP endpoint.

When WEBHOOK_URL is set (or HEALTH_PORT is explicitly configured), a tiny
HTTP server exposes ``/healthz`` so orchestrators can probe liveness.
Disabled by default — set ``HEALTH_PORT`` to enable.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

log = logging.getLogger(__name__)

#: Module-level hook the handler reads; set by HealthServer before start.
def _stats_fn() -> dict[str, Any]:
    return {}


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal /healthz responder."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in ("", "/healthz", "/health"):
            self.send_error(404)
            return
        payload = {"status": "ok", **_stats_fn()}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        log.debug("health: " + format, *args)


class HealthServer:
    """Background thread running a tiny HTTP server for health checks."""

    def __init__(self, port: int, stats_fn: Callable[[], dict[str, Any]] | None = None) -> None:
        global _stats_fn
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        if stats_fn:
            _stats_fn = stats_fn

    def start(self) -> None:
        try:
            self._server = ThreadingHTTPServer(("0.0.0.0", self.port), _HealthHandler)
        except OSError as exc:
            log.warning("Health server could not bind port %d: %s", self.port, exc)
            return
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        log.info("Health server listening on :%d/healthz", self.port)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
