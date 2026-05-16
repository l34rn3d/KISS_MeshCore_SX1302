from __future__ import annotations

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from sx1302_meshcore_kiss.config import AppConfig, redact_config
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer


_DASHBOARD_CSS = r"""
:root {
  color-scheme: dark;
  --bg: #050814;
  --panel: rgba(15, 23, 42, .78);
  --panel-strong: rgba(15, 23, 42, .96);
  --panel-soft: rgba(30, 41, 59, .52);
  --border: rgba(148, 163, 184, .16);
  --text: #e5edf8;
  --muted: #94a3b8;
  --muted-2: #64748b;
  --good: #22c55e;
  --warn: #f59e0b;
  --bad: #fb7185;
  --accent: #38bdf8;
  --accent-2: #818cf8;
  --shadow: 0 24px 80px rgba(0, 0, 0, .42);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  font-family: "Noto Sans", Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: var(--text);
  background:
    radial-gradient(circle at 10% 0%, rgba(56, 189, 248, .22), transparent 28rem),
    radial-gradient(circle at 85% 12%, rgba(129, 140, 248, .18), transparent 32rem),
    linear-gradient(135deg, #020617 0%, var(--bg) 48%, #0f172a 100%);
}
button, input, select { font: inherit; }
.shell { display: grid; grid-template-columns: 18rem minmax(0, 1fr); min-height: 100vh; }
.sidebar {
  position: sticky; top: 0; height: 100vh; overflow: auto;
  padding: 1.25rem; border-right: 1px solid var(--border);
  background: linear-gradient(180deg, rgba(2, 6, 23, .92), rgba(15, 23, 42, .76));
  backdrop-filter: blur(18px);
}
.brand { display: flex; align-items: center; gap: .85rem; margin-bottom: 1.5rem; }
.logo {
  width: 2.8rem; height: 2.8rem; border-radius: 1rem;
  display: grid; place-items: center; font-weight: 900; letter-spacing: -.08em;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  color: #020617; box-shadow: 0 0 42px rgba(56, 189, 248, .35);
}
.brand h1 { margin: 0; font-size: 1rem; line-height: 1.15; }
.brand small { display: block; color: var(--muted); margin-top: .15rem; }
.nav { display: grid; gap: .45rem; margin: 1.25rem 0; }
.nav a {
  color: var(--muted); text-decoration: none; padding: .65rem .75rem; border-radius: .85rem;
  border: 1px solid transparent;
}
.nav a:hover { color: var(--text); background: var(--panel-soft); border-color: var(--border); }
.side-card { margin-top: 1rem; padding: .9rem; border: 1px solid var(--border); border-radius: 1rem; background: var(--panel); }
.side-card .label { color: var(--muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; }
.side-card .value { margin-top: .35rem; font-weight: 800; overflow-wrap: anywhere; }
.main { min-width: 0; padding: 1.1rem clamp(1rem, 3vw, 2rem) 2rem; }
.topbar { display: flex; justify-content: space-between; align-items: center; gap: 1rem; margin-bottom: 1rem; }
.topbar h2 { margin: 0; font-size: clamp(1.35rem, 3vw, 2.25rem); letter-spacing: -.04em; }
.updated { color: var(--muted); text-align: right; }
.hero {
  position: relative; overflow: hidden; margin-bottom: 1rem;
  border: 1px solid var(--border); border-radius: 1.35rem; background: var(--panel-strong);
  box-shadow: var(--shadow);
}
.hero::before {
  content: ""; position: absolute; inset: -35% -10% auto auto; width: 32rem; height: 32rem; border-radius: 50%;
  background: radial-gradient(circle, rgba(56, 189, 248, .28), transparent 60%);
}
.hero-inner { position: relative; padding: clamp(1rem, 2.6vw, 1.6rem); display: grid; grid-template-columns: 1.45fr .9fr; gap: 1rem; align-items: end; }
.hero-title { margin: 0 0 .35rem; font-size: clamp(1.8rem, 4vw, 3.6rem); line-height: .92; letter-spacing: -.07em; }
.hero-copy { color: var(--muted); max-width: 56rem; margin: .65rem 0 0; }
.chip-row { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: 1rem; }
.chip { display: inline-flex; align-items: center; gap: .45rem; padding: .42rem .7rem; border: 1px solid var(--border); border-radius: 999px; background: rgba(15, 23, 42, .78); color: var(--muted); }
.status-dot { width: .62rem; height: .62rem; border-radius: 50%; display: inline-block; background: var(--muted-2); box-shadow: 0 0 0 .18rem rgba(100, 116, 139, .15); }
.status-dot.good { background: var(--good); box-shadow: 0 0 0 .18rem rgba(34, 197, 94, .16); }
.status-dot.warn { background: var(--warn); box-shadow: 0 0 0 .18rem rgba(245, 158, 11, .16); }
.status-dot.bad { background: var(--bad); box-shadow: 0 0 0 .18rem rgba(251, 113, 133, .16); }
.grid { display: grid; gap: 1rem; grid-template-columns: repeat(12, minmax(0, 1fr)); }
.card {
  grid-column: span 4; min-width: 0; padding: 1rem;
  border: 1px solid var(--border); border-radius: 1.2rem; background: var(--panel);
  box-shadow: 0 14px 44px rgba(0, 0, 0, .22); backdrop-filter: blur(14px);
}
.card.wide { grid-column: span 8; }
.card.full { grid-column: 1 / -1; }
.card h3 { margin: 0 0 .75rem; font-size: .95rem; letter-spacing: .01em; display:flex; align-items:center; justify-content:space-between; gap:.75rem; }
.metric { font-size: clamp(2rem, 5vw, 3.6rem); font-weight: 900; line-height: .95; letter-spacing: -.08em; }
.submetric { color: var(--muted); margin-top: .35rem; }
.metrics-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .65rem; margin-top: .75rem; }
.mini { padding: .7rem; border-radius: .9rem; background: rgba(2, 6, 23, .38); border: 1px solid var(--border); }
.mini .k { color: var(--muted); font-size: .78rem; }
.mini .v { font-weight: 800; margin-top: .2rem; overflow-wrap: anywhere; }
pre { margin: 0; white-space: pre-wrap; word-break: break-word; background: rgba(2, 6, 23, .52); border: 1px solid var(--border); border-radius: .95rem; padding: .8rem; color: #bfdbfe; overflow: auto; max-height: 24rem; }
.table-wrap { overflow: auto; border: 1px solid var(--border); border-radius: 1rem; background: rgba(2, 6, 23, .32); }
table { width: 100%; min-width: 820px; border-collapse: collapse; font-size: .9rem; }
th, td { text-align: left; padding: .72rem .75rem; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-size: .76rem; letter-spacing: .08em; text-transform: uppercase; font-weight: 800; }
tr:last-child td { border-bottom: 0; }
code { color: #bae6fd; }
.pill { display: inline-flex; align-items: center; border: 1px solid var(--border); border-radius: 999px; padding: .17rem .5rem; color: var(--muted); background: rgba(15, 23, 42, .74); }
.good-text { color: var(--good); } .warn-text { color: var(--warn); } .bad-text { color: var(--bad); }
.bars { display: grid; gap: .55rem; margin-top: .85rem; }
.bar { display: grid; grid-template-columns: 8rem 1fr 4rem; gap: .6rem; align-items: center; color: var(--muted); font-size: .82rem; }
.bar-track { height: .55rem; border-radius: 999px; background: rgba(100, 116, 139, .18); overflow: hidden; }
.bar-fill { height: 100%; width: 0%; border-radius: inherit; background: linear-gradient(90deg, var(--accent), var(--accent-2)); transition: width .25s ease; }
@media (max-width: 980px) { .shell { grid-template-columns: 1fr; } .sidebar { position: relative; height: auto; } .hero-inner { grid-template-columns: 1fr; } .card, .card.wide { grid-column: 1 / -1; } }
"""


_DASHBOARD_JS = r"""
const fmtJson = (v) => JSON.stringify(v ?? {}, null, 2);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
async function getJson(path) {
  const response = await fetch(path, {cache: 'no-store'});
  if (!response.ok) throw new Error(`${path} ${response.status}`);
  return response.json();
}
function stateClass(value) {
  if (value === true || value === 'ok' || value === 'running' || value === 'started' || value === 'connected') return 'good';
  if (value === false || value === 'down' || value === 'error' || value === 'failed' || value === 'disconnected') return 'bad';
  return 'warn';
}
function setText(id, value) { const el = document.getElementById(id); if (el) el.textContent = value; }
function setDot(id, value) { const el = document.getElementById(id); if (el) el.className = `status-dot ${stateClass(value)}`; }
function setPanel(name, value) {
  const primary = value?.connected ?? value?.started ?? value?.mode ?? value?.state ?? 'unknown';
  setDot(`${name}-dot`, primary);
  setText(`${name}-primary`, String(primary));
  setText(`${name}-json`, fmtJson(value));
}
function statusText(status) {
  const parts = [];
  if (status.radio) parts.push(`radio: ${status.radio.started ?? status.radio.state ?? 'unknown'}`);
  if (status.kiss) parts.push(`kiss: ${status.kiss.mode ?? status.kiss.state ?? 'unknown'}`);
  if (status.mqtt) parts.push(`mqtt: ${status.mqtt.connected ?? 'unknown'}`);
  if (status.mqtt_connected !== undefined) parts.push(`mqtt: ${status.mqtt_connected}`);
  return parts.join(' · ') || 'waiting for status';
}
function renderPackets(packets) {
  const rows = document.getElementById('packet-rows');
  if (!packets.length) { rows.innerHTML = '<tr><td colspan="7" class="muted">No packet events yet</td></tr>'; return; }
  rows.innerHTML = packets.slice().reverse().map(p => {
    const status = p.status || '';
    const cls = /good|ok|done/i.test(status) ? 'good-text' : /bad|err|crc/i.test(status) ? 'bad-text' : 'warn-text';
    return `<tr>
      <td>${esc(p.timestamp || p.time || '')}</td>
      <td><span class="pill">${esc(p.direction || '')}</span></td>
      <td class="${cls}">${esc(status)}</td>
      <td>${esc(p.payload_len ?? p.length ?? '')}</td>
      <td>${esc(p.frequency_hz || '')} ${esc(p.spreading_factor ? 'SF'+p.spreading_factor : '')} ${esc(p.bandwidth_hz || '')}</td>
      <td>${esc(p.rssi_dbm ?? '')} / ${esc(p.snr_db ?? '')}</td>
      <td><code>${esc(p.payload_hex || '')}</code></td>
    </tr>`;
  }).join('');
}
function renderCounterBars(c) {
  const keys = ['rx_good_count', 'rx_crc_ok_count', 'rx_bad_crc_count', 'tx_done_count', 'tx_error_count'];
  const max = Math.max(1, ...keys.map(k => num(c[k])));
  const rows = keys.map(k => {
    const v = num(c[k]);
    const pct = Math.max(2, Math.round((v / max) * 100));
    return `<div class="bar"><div>${esc(k.replace(/_count$/, ''))}</div><div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div><div>${v}</div></div>`;
  });
  document.getElementById('counter-bars').innerHTML = rows.join('');
}
async function refresh() {
  try {
    const [status, counters, packets, config] = await Promise.all([
      getJson('/api/status'), getJson('/api/counters'), getJson('/api/packets'), getJson('/api/config')
    ]);
    const c = counters.counters || {};
    const totalRx = num(c.rx_good_count) + num(c.rx_bad_crc_count) + num(c.rx_unknown_crc_count);
    const totalTx = num(c.tx_done_count) + num(c.tx_error_count) + num(c.tx_requested_count);
    const totalErrors = num(c.rx_bad_crc_count) + num(c.rx_dropped_count) + num(c.tx_error_count) + num(c.kiss_decode_error_count);
    const node = status.node_id || config.node_id || 'unknown';
    setText('node-id', node);
    setText('side-node', node);
    setText('hero-status', statusText(status));
    setText('rx-total', totalRx);
    setText('rx-total-card', totalRx);
    setText('tx-total', totalTx);
    setText('error-total', totalErrors);
    setText('event-total', packets.length);
    setText('updated', `updated ${new Date().toLocaleTimeString()}`);
    setText('side-endpoint', config.kiss?.symlink || config.kiss?.serial_port || config.kiss?.mode || 'unknown');
    setText('side-dashboard', `${config.dashboard?.bind_host || ''}:${config.dashboard?.port || ''}`);
    setPanel('radio', status.radio || status.sx1302 || {state: status.radio_state || 'unknown'});
    setPanel('mqtt', status.mqtt || {connected: status.mqtt_connected ?? false});
    setPanel('kiss', status.kiss || {mode: config.kiss?.mode || 'unknown'});
    setText('counters-json', fmtJson(counters.counters || counters));
    setText('config-json', fmtJson(config));
    renderCounterBars(c);
    renderPackets(packets || []);
  } catch (err) {
    setText('updated', `dashboard error: ${err.message}`);
  }
}
refresh();
setInterval(refresh, 2000);
"""


def _dashboard_html(node_id: str) -> bytes:
    safe_node_id = html.escape(node_id)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SX1302 MeshCore KISS - {safe_node_id}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
  <style>{_DASHBOARD_CSS}</style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand"><div class="logo">SX</div><div><h1>SX1302 KISS</h1><small>MeshCore bridge</small></div></div>
      <nav class="nav" aria-label="Dashboard sections">
        <a href="#overview">Overview</a>
        <a href="#radio">Radio / KISS / MQTT</a>
        <a href="#traffic">Traffic</a>
        <a href="#events">Packet events</a>
        <a href="#config">Config</a>
      </nav>
      <div class="side-card"><div class="label">Node</div><div class="value" id="side-node">{safe_node_id}</div></div>
      <div class="side-card"><div class="label">KISS endpoint</div><div class="value" id="side-endpoint">loading…</div></div>
      <div class="side-card"><div class="label">Dashboard bind</div><div class="value" id="side-dashboard">loading…</div></div>
    </aside>
    <main class="main">
      <div class="topbar"><h2>Repeater bridge dashboard</h2><div class="updated" id="updated">loading…</div></div>
      <section class="hero" id="overview">
        <div class="hero-inner">
          <div>
            <h1 class="hero-title">Node <span id="node-id">{safe_node_id}</span></h1>
            <p class="hero-copy" id="hero-status">Waiting for live status…</p>
            <div class="chip-row">
              <span class="chip"><span id="radio-dot" class="status-dot"></span>Radio <strong id="radio-primary">loading</strong></span>
              <span class="chip"><span id="kiss-dot" class="status-dot"></span>KISS <strong id="kiss-primary">loading</strong></span>
              <span class="chip"><span id="mqtt-dot" class="status-dot"></span>MQTT <strong id="mqtt-primary">loading</strong></span>
            </div>
          </div>
          <div class="metrics-row">
            <div class="mini"><div class="k">RX events</div><div class="v" id="rx-total">0</div></div>
            <div class="mini"><div class="k">TX events</div><div class="v" id="tx-total">0</div></div>
            <div class="mini"><div class="k">Errors</div><div class="v" id="error-total">0</div></div>
            <div class="mini"><div class="k">Buffered packets</div><div class="v" id="event-total">0</div></div>
          </div>
        </div>
      </section>
      <section class="grid" id="radio">
        <article class="card"><h3>Radio</h3><pre id="radio-json">{{}}</pre></article>
        <article class="card"><h3>KISS</h3><pre id="kiss-json">{{}}</pre></article>
        <article class="card"><h3>MQTT</h3><pre id="mqtt-json">{{}}</pre></article>
        <article class="card wide" id="traffic"><h3>Counters</h3><div class="metric" id="rx-total-card">0</div><div class="submetric">RX / TX / error activity from the daemon API</div><div class="bars" id="counter-bars"></div></article>
        <article class="card" id="counters"><h3>Raw counters</h3><pre id="counters-json">{{"rx_good_count":0,"tx_done_count":0,"payload_hex":"API field"}}</pre></article>
        <article class="card full" id="events">
          <h3>Latest packet events <span class="pill">live ring buffer</span></h3>
          <div class="table-wrap"><table aria-label="Latest packet events"><thead><tr><th>Time</th><th>Dir</th><th>Status</th><th>Len</th><th>RF</th><th>RSSI / SNR</th><th>payload_hex</th></tr></thead><tbody id="packet-rows"><tr><td colspan="7" class="muted">Loading packet events…</td></tr></tbody></table></div>
        </article>
        <article class="card full" id="config"><h3>Active config <span class="pill">secrets redacted</span></h3><pre id="config-json">Loading config…</pre></article>
      </section>
    </main>
  </div>
  <script>{_DASHBOARD_JS}</script>
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
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _json(self, payload: Any, code: int = 200) -> None:
                self._send(json.dumps(payload, sort_keys=True).encode(), "application/json", code)

            def do_HEAD(self) -> None:
                self.do_GET()

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
