from __future__ import annotations

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from sx1302_meshcore_kiss.config import AppConfig, redact_config
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer


def _dashboard_html(node_id: str) -> bytes:
    safe_node_id = html.escape(node_id)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SX1302 MeshCore KISS - {safe_node_id}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #071019;
      --panel: #101b2a;
      --panel-2: #0d1623;
      --border: #26384f;
      --text: #e6edf6;
      --muted: #91a4bc;
      --good: #4ade80;
      --warn: #facc15;
      --bad: #fb7185;
      --accent: #38bdf8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top left, #12304a 0, var(--bg) 38rem);
      color: var(--text);
    }}
    header {{ padding: 1.5rem; border-bottom: 1px solid var(--border); background: rgba(7, 16, 25, 0.86); position: sticky; top: 0; backdrop-filter: blur(10px); z-index: 1; }}
    h1 {{ margin: 0; font-size: clamp(1.4rem, 3vw, 2.2rem); }}
    .subtitle {{ color: var(--muted); margin-top: .35rem; }}
    main {{ padding: 1rem; max-width: 1400px; margin: 0 auto; }}
    .grid {{ display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }}
    .card {{ background: linear-gradient(180deg, rgba(16, 27, 42, .96), rgba(13, 22, 35, .96)); border: 1px solid var(--border); border-radius: 16px; padding: 1rem; box-shadow: 0 12px 40px rgba(0,0,0,.24); }}
    .card h2 {{ margin: 0 0 .75rem; font-size: 1rem; color: #dbeafe; }}
    .metric {{ font-size: 1.9rem; font-weight: 800; letter-spacing: -.03em; }}
    .muted {{ color: var(--muted); }}
    .status-dot {{ display: inline-block; width: .7rem; height: .7rem; border-radius: 50%; margin-right: .45rem; background: var(--muted); }}
    .status-dot.good {{ background: var(--good); }}
    .status-dot.warn {{ background: var(--warn); }}
    .status-dot.bad {{ background: var(--bad); }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #06101b; border: 1px solid var(--border); border-radius: 12px; padding: .75rem; color: #bfdbfe; overflow: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .92rem; }}
    th, td {{ text-align: left; padding: .55rem .5rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 700; }}
    code {{ color: #bae6fd; }}
    .wide {{ grid-column: 1 / -1; }}
    .pill {{ display: inline-flex; align-items: center; border: 1px solid var(--border); border-radius: 999px; padding: .15rem .55rem; color: var(--muted); }}
    .good-text {{ color: var(--good); }} .warn-text {{ color: var(--warn); }} .bad-text {{ color: var(--bad); }}
  </style>
</head>
<body>
  <header>
    <h1>SX1302 MeshCore KISS dashboard</h1>
    <div class="subtitle">Node <strong id="node-id">{safe_node_id}</strong> · <span id="updated">loading…</span></div>
  </header>
  <main>
    <section class="grid">
      <article class="card"><h2>Radio</h2><div id="radio-state"><span class="status-dot"></span>Loading</div><pre id="radio-json">{{}}</pre></article>
      <article class="card"><h2>MQTT</h2><div id="mqtt-state"><span class="status-dot"></span>Loading</div><pre id="mqtt-json">{{}}</pre></article>
      <article class="card"><h2>KISS</h2><div id="kiss-state"><span class="status-dot"></span>Loading</div><pre id="kiss-json">{{}}</pre></article>
      <article class="card"><h2>Counters</h2><div class="metric" id="packet-total">0</div><div class="muted">RX/TX/error activity</div><pre id="counters-json">{{"rx_good_count":0,"tx_done_count":0,"payload_hex":"API field"}}</pre></article>
      <article class="card wide"><h2>Active config</h2><pre id="config-json">Loading config…</pre></article>
      <article class="card wide">
        <h2>Latest packet events</h2>
        <div class="muted">Newest daemon ring-buffer events, max 50. KISS Data frames carry raw MeshCore payload bytes only.</div>
        <table aria-label="Latest packet events">
          <thead><tr><th>Time</th><th>Dir</th><th>Status</th><th>Len</th><th>RF</th><th>RSSI/SNR</th><th>payload_hex</th></tr></thead>
          <tbody id="packet-rows"><tr><td colspan="7" class="muted">Loading packet events…</td></tr></tbody>
        </table>
      </article>
    </section>
  </main>
  <script>
    const fmtJson = (v) => JSON.stringify(v ?? {{}}, null, 2);
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    async function getJson(path) {{
      const response = await fetch(path, {{cache: 'no-store'}});
      if (!response.ok) throw new Error(`${{path}} ${{response.status}}`);
      return response.json();
    }}
    function stateClass(value) {{
      if (value === true || value === 'ok' || value === 'running' || value === 'started') return 'good';
      if (value === false || value === 'down' || value === 'error') return 'bad';
      return 'warn';
    }}
    function setPanel(name, value) {{
      const dot = document.querySelector(`#${{name}}-state .status-dot`);
      const text = document.getElementById(`${{name}}-state`);
      const jsonEl = document.getElementById(`${{name}}-json`);
      const primary = value?.connected ?? value?.started ?? value?.mode ?? value?.state ?? 'unknown';
      dot.className = `status-dot ${{stateClass(primary)}}`;
      text.lastChild.textContent = ` ${{primary}}`;
      jsonEl.textContent = fmtJson(value);
    }}
    function renderPackets(packets) {{
      const rows = document.getElementById('packet-rows');
      if (!packets.length) {{ rows.innerHTML = '<tr><td colspan="7" class="muted">No packet events yet</td></tr>'; return; }}
      rows.innerHTML = packets.slice().reverse().map(p => `
        <tr>
          <td>${{esc(p.timestamp || p.time || '')}}</td>
          <td><span class="pill">${{esc(p.direction || '')}}</span></td>
          <td>${{esc(p.status || '')}}</td>
          <td>${{esc(p.payload_len ?? p.length ?? '')}}</td>
          <td>${{esc(p.frequency_hz || '')}} ${{esc(p.spreading_factor ? 'SF'+p.spreading_factor : '')}} ${{esc(p.bandwidth_hz || '')}}</td>
          <td>${{esc(p.rssi_dbm ?? '')}} / ${{esc(p.snr_db ?? '')}}</td>
          <td><code>${{esc(p.payload_hex || '')}}</code></td>
        </tr>`).join('');
    }}
    async function refresh() {{
      try {{
        const [status, counters, packets, config] = await Promise.all([
          fetch('/api/status').then(r => r.json()),
          getJson('/api/counters'), getJson('/api/packets'), getJson('/api/config')
        ]);
        document.getElementById('node-id').textContent = status.node_id || config.node_id || '{safe_node_id}';
        setPanel('radio', status.radio || status.sx1302 || {{state: 'unknown'}});
        setPanel('mqtt', status.mqtt || {{connected: false}});
        setPanel('kiss', status.kiss || {{mode: config.kiss?.mode || 'unknown'}});
        document.getElementById('counters-json').textContent = fmtJson(counters.counters || counters);
        const c = counters.counters || {{}};
        document.getElementById('packet-total').textContent = (c.rx_good_count || 0) + (c.rx_bad_crc_count || 0) + (c.rx_unknown_crc_count || 0) + (c.tx_done_count || 0) + (c.tx_error_count || 0);
        document.getElementById('config-json').textContent = fmtJson(config);
        renderPackets(packets || []);
        document.getElementById('updated').textContent = `updated ${{new Date().toLocaleTimeString()}}`;
      }} catch (err) {{
        document.getElementById('updated').textContent = `dashboard error: ${{err.message}}`;
      }}
    }}
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>""".encode()


class DashboardServer:
    def __init__(self, *, config: AppConfig, counters: Counters, ring: PacketRingBuffer, status_provider: Callable[[], dict[str, Any]] | None = None) -> None:
        self.config = config
        self.counters = counters
        self.ring = ring
        self.status_provider = status_provider or (lambda: {})
        self.bind_host = config.dashboard.bind_host
        self.port = int(config.dashboard.port)
        self._server = None
        self._thread = None

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def _send(self, body: bytes, content_type: str, code: int = 200) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, payload: Any, code: int = 200) -> None:
                self._send(json.dumps(payload, sort_keys=True).encode(), "application/json", code)

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == "/":
                    self._send(_dashboard_html(outer.config.node_id), "text/html; charset=utf-8")
                    return
                if path == "/api/status":
                    self._json({"node_id": outer.config.node_id, **outer.status_provider()})
                    return
                if path == "/api/counters":
                    self._json(outer.counters.snapshot(node_id=outer.config.node_id, uptime_seconds=0))
                    return
                if path == "/api/packets":
                    self._json(outer.ring.snapshot())
                    return
                if path == "/api/config":
                    self._json(redact_config(outer.config))
                    return
                self._json({"error": "not_found"}, 404)

        self._server = ThreadingHTTPServer((self.bind_host, 0 if self.port == 0 else self.port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
