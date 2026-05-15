from __future__ import annotations
import json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from sx1302_meshcore_kiss.config import AppConfig, redact_config
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer

class DashboardServer:
    def __init__(self, *, config: AppConfig, counters: Counters, ring: PacketRingBuffer, status_provider: Callable[[], dict[str, Any]] | None = None) -> None:
        self.config=config; self.counters=counters; self.ring=ring; self.status_provider=status_provider or (lambda: {}); self.bind_host=config.dashboard.bind_host; self.port=int(config.dashboard.port); self._server=None; self._thread=None
    def start(self) -> None:
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args): return
            def _json(self, payload, code=200):
                body=json.dumps(payload, sort_keys=True).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                if self.path == '/':
                    body=b"<html><body><h1>SX1302 MeshCore KISS</h1><p>Use /api/status /api/counters /api/packets /api/config</p></body></html>"; self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
                if self.path == '/api/status': self._json({'node_id': outer.config.node_id, **outer.status_provider()}); return
                if self.path == '/api/counters': self._json(outer.counters.snapshot(node_id=outer.config.node_id, uptime_seconds=0)); return
                if self.path == '/api/packets': self._json(outer.ring.snapshot()); return
                if self.path == '/api/config': self._json(redact_config(outer.config)); return
                self._json({'error':'not_found'}, 404)
        self._server=ThreadingHTTPServer((self.bind_host, 0 if self.port == 0 else self.port), Handler)
        self.port=self._server.server_address[1]
        self._thread=threading.Thread(target=self._server.serve_forever, daemon=True); self._thread.start()
    def stop(self) -> None:
        if self._server is not None: self._server.shutdown(); self._server.server_close(); self._server=None
        if self._thread is not None: self._thread.join(timeout=2); self._thread=None
