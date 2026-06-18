from __future__ import annotations

import html
import json
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from sx1302_meshcore_kiss.config import AppConfig, apply_hotspot_profile, apply_radio_profile, hotspot_profiles, radio_profiles, redact_config, startup_profiles
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
      --panel-3: #071321;
      --border: #26384f;
      --text: #e6edf6;
      --muted: #91a4bc;
      --good: #4ade80;
      --warn: #facc15;
      --bad: #fb7185;
      --accent: #38bdf8;
      --chip: #16263a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at 18% -10%, rgba(56,189,248,.18), transparent 34rem), linear-gradient(180deg, #06101a, var(--bg));
      color: var(--text);
    }}
    header {{ padding: 1.1rem 1.25rem; border-bottom: 1px solid var(--border); background: rgba(7, 16, 25, 0.9); position: sticky; top: 0; backdrop-filter: blur(10px); z-index: 2; }}
    .header-inner {{ max-width: 1520px; margin: 0 auto; display: flex; justify-content: space-between; gap: 1rem; align-items: center; }}
    h1 {{ margin: 0; font-size: clamp(1.35rem, 3vw, 2.1rem); }}
    h2 {{ margin: 0; font-size: 1rem; color: #dbeafe; }}
    h3 {{ margin: 1rem 0 .45rem; color: #bfdbfe; font-size: .92rem; text-transform: uppercase; letter-spacing: .06em; }}
    .subtitle {{ color: var(--muted); margin-top: .35rem; }}
    main {{ padding: 1rem; max-width: 1520px; margin: 0 auto; }}
    .layout {{ display: grid; gap: 1rem; grid-template-columns: minmax(0, 1.05fr) minmax(360px, .95fr); align-items: start; }}
    .stack {{ display: grid; gap: 1rem; }}
    .topline {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .75rem; margin-bottom: 1rem; }}
    .card {{ background: linear-gradient(180deg, rgba(16, 27, 42, .96), rgba(13, 22, 35, .96)); border: 1px solid var(--border); border-radius: 16px; padding: 1rem; box-shadow: 0 12px 40px rgba(0,0,0,.24); }}
    .summary {{ padding: .8rem; min-height: 6.2rem; display: grid; gap: .45rem; align-content: space-between; }}
    .card-head {{ display: flex; justify-content: space-between; gap: 1rem; align-items: center; margin-bottom: .8rem; }}
    .metric {{ font-size: 1.65rem; font-weight: 800; letter-spacing: -.03em; line-height: 1; }}
    .metric small {{ display: block; font-size: .76rem; font-weight: 600; color: var(--muted); margin-top: .35rem; letter-spacing: 0; }}
    .muted {{ color: var(--muted); }}
    .status-dot {{ display: inline-block; width: .7rem; height: .7rem; border-radius: 50%; margin-right: .45rem; background: var(--muted); }}
    .status-dot.good {{ background: var(--good); }}
    .status-dot.warn {{ background: var(--warn); }}
    .status-dot.bad {{ background: var(--bad); }}
    .pill {{ display: inline-flex; align-items: center; border: 1px solid var(--border); background: var(--chip); border-radius: 999px; padding: .16rem .6rem; color: #c7d2fe; font-size: .82rem; white-space: nowrap; }}
    .pill.good {{ color: var(--good); }} .pill.warn {{ color: var(--warn); }} .pill.bad {{ color: var(--bad); }}
    dl {{ display: grid; grid-template-columns: minmax(8rem, .75fr) 1.25fr; gap: .42rem .8rem; margin: 0; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; word-break: break-word; }}
    .kv-list {{ display: grid; gap: .38rem; }}
    .kv-row {{ display: grid; grid-template-columns: minmax(9rem, .35fr) 1fr; gap: .75rem; padding: .36rem 0; border-bottom: 1px solid rgba(38,56,79,.65); }}
    .kv-row:last-child {{ border-bottom: 0; }}
    .kv-key {{ color: var(--muted); }}
    .kv-val {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #dbeafe; overflow-wrap: anywhere; }}
    .counter-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .65rem; }}
    .counter {{ background: rgba(6,16,27,.55); border: 1px solid var(--border); border-radius: 12px; padding: .7rem; }}
    .counter .num {{ font-size: 1.35rem; font-weight: 800; }}
    .counter .label {{ color: var(--muted); font-size: .82rem; margin-top: .15rem; }}
    .event-list {{ display: grid; gap: .65rem; margin-top: .8rem; max-height: 38rem; overflow-y: auto; padding-right: .2rem; }}
    .event {{ border: 1px solid var(--border); border-radius: 14px; background: rgba(6,16,27,.6); padding: .75rem; }}
    .event-title {{ display: flex; flex-wrap: wrap; gap: .45rem; align-items: center; margin-bottom: .45rem; }}
    .event-lines {{ display: grid; gap: .25rem; }}
    .decoded {{ margin-top: .55rem; padding-top: .55rem; border-top: 1px solid rgba(38,56,79,.65); }}
    .line {{ display: grid; grid-template-columns: 7.5rem 1fr; gap: .6rem; }}
    .line .label {{ color: var(--muted); }}
    code {{ color: #bae6fd; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; overflow-wrap: anywhere; }}
    .empty {{ color: var(--muted); padding: .75rem; border: 1px dashed var(--border); border-radius: 12px; }}
    details {{ border: 1px solid var(--border); border-radius: 14px; background: rgba(6,16,27,.35); }}
    summary {{ cursor: pointer; padding: .85rem 1rem; color: #dbeafe; font-weight: 700; }}
    details[open] summary {{ border-bottom: 1px solid var(--border); }}
    .details-body {{ padding: 0 1rem 1rem; }}
    .diag {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .45rem .8rem; }}
    .diag div {{ min-width: 0; }}
    .diag .kv-val {{ font-size: .9rem; }}
    .log-box {{ height: 24rem; overflow-y: auto; background: rgba(0, 0, 0, .38); border: 1px solid var(--border); border-radius: 12px; padding: .75rem; font: .82rem/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }}
    .log-line {{ color: #dbeafe; border-bottom: 1px solid rgba(38,56,79,.28); padding: .12rem 0; }}
    .log-line.error {{ color: var(--bad); }}
    .log-line.warn {{ color: var(--warn); }}
    .form-row {{ display: flex; gap: .5rem; margin-top: .75rem; flex-wrap: wrap; }}
    select, button {{ border: 1px solid var(--border); border-radius: 10px; background: var(--panel-3); color: var(--text); padding: .55rem .65rem; }}
    button {{ cursor: pointer; background: #075985; font-weight: 700; }}
    @media (max-width: 980px) {{
      .header-inner {{ display: block; }}
      .layout {{ grid-template-columns: 1fr; }}
      .topline {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .counter-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 560px) {{
      main {{ padding: .65rem; }}
      .topline {{ grid-template-columns: 1fr; }}
      .counter-grid {{ grid-template-columns: 1fr; }}
      dl, .kv-row, .line, .diag {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-inner">
      <div>
        <h1>SX1302 MeshCore KISS</h1>
        <div class="subtitle">Node <strong id="node-id">{safe_node_id}</strong> · <span id="updated">loading…</span></div>
      </div>
      <span id="overall-badge" class="pill warn">starting</span>
    </div>
  </header>
  <main>
    <section class="topline">
      <article class="card summary"><span class="muted">Radio</span><div class="metric" id="summary-radio">--<small>waiting</small></div></article>
      <article class="card summary"><span class="muted">Link</span><div class="metric" id="summary-link">--<small>frequency</small></div></article>
      <article class="card summary"><span class="muted">Packets</span><div class="metric" id="summary-packets">0 / 0<small>RX good / TX done</small></div></article>
      <article class="card summary"><span class="muted">Environment</span><div class="metric" id="summary-noise">--<small>temp / noise</small></div></article>
    </section>
    <section class="layout">
      <div class="stack">
        <article class="card"><div class="card-head"><h2>Radio State</h2><span id="radio-badge" class="pill warn">loading</span></div><dl id="radio-list"></dl></article>
        <article class="card"><div class="card-head"><h2>62.5 kHz / SX1261</h2><span id="sx1261-badge" class="pill warn">loading</span></div><div id="sx1261-diag" class="diag"></div></article>
        <article class="card"><div class="card-head"><h2>Activity Counters</h2><span class="pill" id="packet-total">0 total</span></div><div class="counter-grid" id="counter-grid"></div></article>
        <details>
          <summary>Active config</summary>
          <div class="details-body" id="config-groups"></div>
        </details>
      </div>
      <div class="stack">
        <article class="card"><div class="card-head"><h2>pyMC TCP</h2><span id="pymc-tcp-badge" class="pill warn">loading</span></div><dl id="pymc-tcp-list"></dl><h3>Hotspot Hardware</h3><dl id="startup-list"></dl><div class="form-row"><select id="hotspot-select" aria-label="Hotspot model"></select><button id="hotspot-apply" type="button">Apply pins</button></div><div class="muted" id="hotspot-help">Select the installed hotspot model to load Nebra reset GPIO and SPI defaults.</div></article>
        <article class="card"><div class="card-head"><h2>KISS / MQTT</h2><span id="kiss-badge" class="pill warn">loading</span></div><dl id="kiss-list"></dl><h3>MQTT</h3><dl id="mqtt-list"></dl></article>
        <article class="card">
          <div class="card-head"><h2>Latest packet events</h2><span class="muted">newest first</span></div>
          <div id="packet-list" class="event-list"><div class="empty">Loading packet events…</div></div>
        </article>
        <article class="card">
          <div class="card-head"><h2>Live daemon logs</h2><span id="logs-badge" class="pill warn">connecting</span></div>
          <div class="muted">Live-only daemon log stream from page load onward. No historical log backlog is loaded.</div>
          <div id="live-logs" class="log-box" aria-live="polite"><div class="muted">Waiting for new log lines…</div></div>
        </article>
      </div>
    </section>
  </main>
  <script>
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    const title = (s) => String(s || '').replace(/_/g, ' ').split(' ').map(part => part ? part[0].toUpperCase() + part.slice(1) : '').join(' ');
    const has = (v) => v !== undefined && v !== null && v !== '';
    const fmtHz = (hz) => has(hz) ? `${{(Number(hz)/1e6).toFixed(3)}} MHz` : '';
    const fmtBw = (hz) => has(hz) ? `${{(Number(hz)/1000).toFixed(Number(hz) % 1000 ? 1 : 0)}} kHz` : '';
    async function getJson(path) {{
      const response = await fetch(path, {{cache: 'no-store'}});
      if (!response.ok) throw new Error(`${{path}} ${{response.status}}`);
      return response.json();
    }}
    function badgeClass(value) {{
      const s = String(value ?? '').toLowerCase();
      if (value === true || ['ok','running','started','active','connected','tx_done','good'].includes(s)) return 'good';
      if (value === false || ['down','error','failed','bad','tx_error','radio_not_configured'].includes(s)) return 'bad';
      return 'warn';
    }}
    function setBadge(id, value) {{
      const el = document.getElementById(id);
      if (!el) return;
      el.className = `pill ${{badgeClass(value)}}`;
      el.textContent = String(value ?? 'unknown');
    }}
    function setSummary(id, value, caption) {{
      document.getElementById(id).innerHTML = `${{esc(value)}}<small>${{esc(caption)}}</small>`;
    }}
    function renderDl(id, rows) {{
      const el = document.getElementById(id);
      const filtered = rows.filter(([_, v]) => has(v));
      el.innerHTML = filtered.length ? filtered.map(([k, v]) => `<dt>${{esc(k)}}</dt><dd>${{esc(v)}}</dd>`).join('') : '<dt>Status</dt><dd class="muted">No data yet</dd>';
    }}
    function renderKvRows(obj) {{
      return Object.entries(obj || {{}})
        .filter(([_, v]) => typeof v !== 'object' || v === null)
        .map(([k, v]) => `<div class="kv-row"><div class="kv-key">${{esc(title(k))}}</div><div class="kv-val">${{esc(v)}}</div></div>`).join('');
    }}
    function renderConfig(config) {{
      const groups = ['pymc_tcp', 'startup', 'manual_radio', 'kiss', 'radio', 'crc', 'mqtt', 'status', 'dashboard', 'logging'];
      const html = groups.map(g => `<h3>${{esc(g)}}</h3><div class="kv-list">${{renderKvRows(config[g]) || '<div class="muted">No entries</div>'}}</div>`).join('');
      document.getElementById('config-groups').innerHTML = html;
    }}
    function renderCounters(counters) {{
      const c = counters.counters || counters || {{}};
      const wanted = [
        ['rx_good_count', 'RX good'], ['rx_bad_crc_count', 'RX bad CRC'], ['rx_unknown_crc_count', 'RX unknown CRC'], ['rx_dropped_count', 'RX dropped'],
        ['tx_requested_count', 'TX requested'], ['tx_done_count', 'TX done'], ['tx_error_count', 'TX error'],
        ['radio_preamble_count', 'LoRa preambles'], ['radio_syncword_count', 'Sync words'], ['radio_header_valid_count', 'Valid headers'], ['radio_meshcore_candidate_count', 'MeshCore-like'],
        ['kiss_decode_error_count', 'KISS decode errors'], ['kiss_unknown_command_count', 'KISS unknown']
      ];
      const total = wanted.reduce((sum, [k]) => sum + Number(c[k] || 0), 0);
      document.getElementById('packet-total').textContent = `${{total}} total`;
      document.getElementById('counter-grid').innerHTML = wanted.map(([k, label]) => `<div class="counter"><div class="num">${{esc(c[k] || 0)}}</div><div class="label">${{esc(label)}}</div></div>`).join('');
      return c;
    }}
    function renderDiag(id, rows) {{
      const el = document.getElementById(id);
      const filtered = rows.filter(([_, v]) => has(v));
      el.innerHTML = filtered.length ? filtered.map(([k, v]) => `<div><div class="kv-key">${{esc(k)}}</div><div class="kv-val">${{esc(v)}}</div></div>`).join('') : '<div class="muted">No diagnostics yet</div>';
    }}
    function bytesFromHex(hex) {{
      const clean = String(hex || '').replace(/[^0-9a-f]/gi, '');
      if (!clean || clean.length % 2) return [];
      const bytes = [];
      for (let i = 0; i < clean.length; i += 2) bytes.push(parseInt(clean.slice(i, i + 2), 16));
      return bytes;
    }}
    function asciiPreview(bytes) {{
      if (!bytes.length) return '';
      const chars = bytes.map(b => b >= 32 && b <= 126 ? String.fromCharCode(b) : '.').join('');
      const printable = bytes.filter(b => b >= 32 && b <= 126).length;
      return printable ? chars.slice(0, 96) : '';
    }}
    function payloadDecodeRows(p, meta) {{
      const payloadHex = p.payload_hex || meta.payload_hex || '';
      const bytes = bytesFromHex(payloadHex);
      if (!bytes.length) return [];
      const prefix = bytes.slice(0, 16).map(b => b.toString(16).padStart(2, '0')).join(' ');
      const suffix = bytes.length > 16 ? bytes.slice(-8).map(b => b.toString(16).padStart(2, '0')).join(' ') : '';
      const first = bytes[0];
      const second = bytes.length > 1 ? bytes[1] : null;
      const hints = [];
      hints.push(`first=0x${{first.toString(16).padStart(2, '0')}}`);
      if (second !== null) hints.push(`second=0x${{second.toString(16).padStart(2, '0')}}`);
      if (bytes.every(b => b >= 32 && b <= 126 || b === 9 || b === 10 || b === 13)) hints.push('printable payload');
      else hints.push('binary / likely encrypted');
      return [
        ['Decoded len', `${{bytes.length}} bytes`],
        ['Hex prefix', prefix],
        ['Hex suffix', suffix],
        ['ASCII preview', asciiPreview(bytes)],
        ['Basic hints', hints.join(' · ')],
      ].filter(([_, v]) => has(v));
    }}
    function renderHotspotSelector(profiles, current) {{
      const select = document.getElementById('hotspot-select');
      if (!select || !profiles) return;
      const value = select.value || current || '';
      select.innerHTML = Object.entries(profiles).map(([key, p]) => `<option value="${{esc(key)}}">${{esc(p.friendly || key)}} (${{esc(key)}})</option>`).join('');
      select.value = profiles[value] ? value : current;
    }}
    async function applyHotspot() {{
      const select = document.getElementById('hotspot-select');
      const help = document.getElementById('hotspot-help');
      if (!select?.value) return;
      try {{
        const response = await fetch('/api/hotspot-profile', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{hotspot: select.value}})}});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || response.status);
        help.textContent = `Applied ${{payload.hotspot}} pins. Restart/reconfigure the radio if it was already running.`;
        await refresh();
      }} catch (err) {{
        help.textContent = `Apply failed: ${{err.message}}`;
      }}
    }}
    function eventLines(p) {{
      const radio = p.radio || {{}};
      const meta = p.raw_metadata || {{}};
      return [
        ['Time', p.timestamp || p.time],
        ['Radio', [fmtHz(p.frequency_hz || radio.frequency_hz || meta.freq_hz), fmtBw(p.bandwidth_hz || radio.bandwidth_hz || meta.bw), has(p.spreading_factor || radio.spreading_factor || meta.sf) ? `SF${{p.spreading_factor || radio.spreading_factor || meta.sf}}` : '', has(p.coding_rate || radio.coding_rate) ? `CR4/${{p.coding_rate || radio.coding_rate}}` : ''].filter(Boolean).join(' · ')],
        ['Signal', [has(p.rssi_dbm) ? `${{p.rssi_dbm}} dBm` : '', has(p.snr_db) ? `${{p.snr_db}} dB SNR` : ''].filter(Boolean).join(' · ')],
        ['Payload len', p.payload_len ?? p.length ?? meta.size],
        ['Payload hex', p.payload_hex],
        ['Payload b64', p.payload_b64],
        ['Command', p.command_name || p.command],
        ['Error', p.error],
      ].filter(([_, v]) => has(v));
    }}
    function renderPackets(packets) {{
      const list = document.getElementById('packet-list');
      if (!packets.length) {{ list.innerHTML = '<div class="empty">No packet events yet</div>'; return; }}
      list.innerHTML = packets.slice().reverse().map(p => {{
        const meta = p.raw_metadata || {{}};
        const lines = eventLines(p).map(([k, v]) => `<div class="line"><span class="label">${{esc(k)}}</span><span>${{k.includes('Payload') ? `<code>${{esc(v)}}</code>` : esc(v)}}</span></div>`).join('');
        const decoded = payloadDecodeRows(p, meta).map(([k, v]) => `<div class="line"><span class="label">${{esc(k)}}</span><span><code>${{esc(v)}}</code></span></div>`).join('');
        const status = p.status || 'event';
        return `<section class="event"><div class="event-title"><span class="pill">${{esc(p.direction || 'event')}}</span><span class="pill ${{badgeClass(status)}}">${{esc(status)}}</span></div><div class="event-lines">${{lines || '<span class="muted">No details</span>'}}</div>${{decoded ? `<div class="event-lines decoded">${{decoded}}</div>` : ''}}</section>`;
      }}).join('');
    }}
    async function refresh() {{
      try {{
        const [status, counters, packets, config, hotspots] = await Promise.all([
          getJson('/api/status'), getJson('/api/counters'), getJson('/api/packets'), getJson('/api/config'), getJson('/api/hotspot-profiles')
        ]);
        document.getElementById('node-id').textContent = status.node_id || config.node_id || '{safe_node_id}';
        const transport = status.transport || config.transport || 'pymc_tcp';
        const radio = status.radio || status.sx1302 || {{}};
        const kiss = status.kiss || {{mode: config.kiss?.mode}};
        const pymcTcp = status.pymc_tcp || config.pymc_tcp || {{enabled: false}};
        const startup = status.startup || config.startup || {{}};
        const mqtt = status.mqtt || {{connected: status.mqtt_connected}};
        const sx1302 = radio.sx1302 || {{}};
        const dbg = sx1302.sx1261_rx_debug || radio.sx1261_rx_debug || status.sx1261_rx_debug || {{}};
        const noise = sx1302.last_noise_floor ?? radio.last_noise_floor ?? radio.noise_floor_dbm ?? status.last_noise_floor;
        const temp = radio.temperature_c ?? sx1302.temperature_c ?? status.temperature_c;
        const cad = radio.last_cad || sx1302.last_cad || status.last_cad || {{}};
        const txBw = sx1302.hal_bandwidth_hz || radio.hal_bandwidth_hz || radio.bandwidth || radio.bandwidth_hz || radio.bw;
        const rxBackend = sx1302.rx_backend || radio.rx_backend || (dbg.lora_rx_enabled ? 'sx1261' : '');
        const radioState = radio.started ? 'started' : (radio.state || 'waiting for SetRadio');
        const sx1261State = rxBackend === 'sx1261' || dbg.lora_rx_enabled ? 'sx1261' : 'inactive';
        setBadge('radio-badge', radio.started ? 'started' : (radio.state || 'waiting for SetRadio'));
        setBadge('overall-badge', radioState);
        setBadge('sx1261-badge', sx1261State);
        setBadge('kiss-badge', kiss.mode || 'unknown');
        setBadge('pymc-tcp-badge', transport === 'pymc_tcp' && pymcTcp.enabled ? (pymcTcp.connected_clients ? 'connected' : 'listening') : `transport: ${{transport}}`);
        setBadge('mqtt-badge', mqtt.connected ? 'connected' : 'disabled/down');
        renderDl('radio-list', [['Started', radio.started], ['Frequency', fmtHz(radio.frequency || radio.frequency_hz || radio.freq_hz)], ['Configured BW', fmtBw(radio.bandwidth || radio.bandwidth_hz || radio.bw)], ['HAL TX BW', fmtBw(sx1302.hal_bandwidth_hz || radio.hal_bandwidth_hz)], ['HAL BW code', sx1302.hal_bandwidth_code || radio.hal_bandwidth_code], ['RX backend', rxBackend], ['Spreading factor', radio.spreading_factor || radio.sf], ['Coding rate', radio.coding_rate || radio.cr], ['TX power', has(radio.tx_power) ? `${{radio.tx_power}} dBm` : radio.tx_power_dbm], ['Current RSSI', has(radio.current_rssi_dbm) ? `${{radio.current_rssi_dbm}} dBm` : ''], ['Temperature', has(temp) ? `${{Number(temp).toFixed(1)}} C` : ''], ['Noise floor', has(noise) ? `${{noise}} dBm` : ''], ['Last CAD', has(cad.source) ? `${{cad.busy ? 'busy' : 'clear'}} via ${{cad.method || cad.source}}` : ''], ['CAD params', has(cad.det_peak) || has(cad.det_min) ? `peak=${{cad.det_peak ?? ''}} min=${{cad.det_min ?? ''}}` : ''], ['Channel busy', radio.channel_busy]]);
        renderDiag('sx1261-diag', [['LoRa RX enabled', dbg.lora_rx_enabled], ['Poll count', dbg.poll_count], ['Branch count', dbg.sx1261_branch_count], ['RX done', dbg.rx_done_count], ['CRC err', dbg.crc_err_count], ['Header valid', dbg.header_valid_count], ['Header err', dbg.header_err_count], ['Preamble', dbg.preamble_count], ['Sync word', dbg.syncword_count], ['Last IRQ', dbg.last_irq_flags], ['Last RX size', dbg.last_rx_size], ['Fallback count', dbg.sx1302_fallback_count], ['Last scan', sx1302.last_noise_scan_at || radio.last_noise_scan_at], ['Last scan floor', has(noise) ? `${{noise}} dBm` : '']]);
        renderDl('kiss-list', [['Mode', kiss.mode || config.kiss?.mode], ['PTY symlink', config.kiss?.symlink], ['Serial', config.kiss?.serial_port], ['Baud', config.kiss?.baud_rate], ['MQTT connected', mqtt.connected]]);
        renderDl('pymc-tcp-list', [['Transport', transport], ['Enabled', transport === 'pymc_tcp' && pymcTcp.enabled], ['Bind', `${{pymcTcp.bind_host || config.pymc_tcp?.bind_host || ''}}:${{pymcTcp.port || config.pymc_tcp?.port || ''}}`], ['Clients', pymcTcp.connected_clients], ['Max clients', pymcTcp.max_clients], ['Auth required', pymcTcp.auth_required]]);
        renderDl('startup-list', [['Startup profile', startup.profile || config.startup?.profile], ['Hotspot', startup.hotspot || config.startup?.hotspot], ['Reset script', startup.reset_script_path || config.radio?.reset_script_path], ['SPI device', config.radio?.spi_device], ['Concentrator reset pin', config.radio?.sx1302_reset_pin], ['Optional SX125x reset pin', config.radio?.sx1261_reset_pin]]);
        renderHotspotSelector(hotspots, startup.hotspot || config.startup?.hotspot);
        renderDl('mqtt-list', [['Host', config.mqtt?.host], ['Base topic', config.mqtt?.base_topic], ['Retain status', config.mqtt?.retain_status]]);
        const c = renderCounters(counters);
        setSummary('summary-radio', radioState, [rxBackend || 'rx pending', fmtBw(txBw)].filter(Boolean).join(' · '));
        setSummary('summary-link', fmtHz(radio.frequency || radio.frequency_hz || radio.freq_hz) || '--', [has(radio.spreading_factor || radio.sf) ? `SF${{radio.spreading_factor || radio.sf}}` : '', has(radio.coding_rate || radio.cr) ? `CR4/${{radio.coding_rate || radio.cr}}` : ''].filter(Boolean).join(' · ') || 'not configured');
        setSummary('summary-packets', `${{c.rx_good_count || 0}} / ${{c.tx_done_count || 0}}`, 'RX good / TX done');
        setSummary('summary-noise', has(temp) ? `${{Number(temp).toFixed(1)}} C` : (has(noise) ? `${{noise}} dBm` : '--'), has(temp) && has(noise) ? `noise ${{noise}} dBm` : (has(radio.channel_busy) ? `busy: ${{radio.channel_busy}}` : 'temp / noise'));
        renderConfig(config);
        renderPackets(packets || []);
        document.getElementById('updated').textContent = `updated ${{new Date().toLocaleTimeString()}}`;
      }} catch (err) {{
        document.getElementById('updated').textContent = `dashboard error: ${{err.message}}`;
      }}
    }}
    function appendLogLine(line) {{
      const box = document.getElementById('live-logs');
      if (box.querySelector('.muted')) box.innerHTML = '';
      const div = document.createElement('div');
      const s = String(line || '');
      div.className = 'log-line' + (/\b(error|failed|traceback)\b/i.test(s) ? ' error' : (/\b(warn|timeout)\b/i.test(s) ? ' warn' : ''));
      div.textContent = s;
      box.appendChild(div);
      while (box.children.length > 300) box.removeChild(box.firstChild);
      box.scrollTop = box.scrollHeight;
    }}
    function startLiveLogs() {{
      const badge = document.getElementById('logs-badge');
      if (!window.EventSource) {{ badge.textContent = 'unsupported'; badge.className = 'pill bad'; return; }}
      const source = new EventSource('/api/live-logs');
      source.onopen = () => {{ badge.textContent = 'live'; badge.className = 'pill good'; }};
      source.onmessage = (event) => appendLogLine(event.data);
      source.onerror = () => {{ badge.textContent = 'reconnecting'; badge.className = 'pill warn'; }};
    }}
    refresh();
    startLiveLogs();
    document.getElementById('hotspot-apply')?.addEventListener('click', applyHotspot);
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

            def _live_logs(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                log_path = Path(getattr(outer.config.logging, "live_log_path", "/tmp/sx1302-meshcore-kiss-live.log"))
                try:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    log_path.touch(exist_ok=True)
                    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(0, 2)  # live-only: start at EOF, no historical backlog
                        last_heartbeat = time.monotonic()
                        while True:
                            line = fh.readline()
                            if line:
                                clean = line.rstrip("\r\n")
                                self.wfile.write(f"data: {clean}\n\n".encode())
                                self.wfile.flush()
                                last_heartbeat = time.monotonic()
                                continue
                            if time.monotonic() - last_heartbeat > 15:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                                last_heartbeat = time.monotonic()
                            time.sleep(0.25)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as exc:
                    try:
                        self.wfile.write(f"data: live log stream unavailable: {exc}\n\n".encode())
                        self.wfile.flush()
                    except Exception:
                        pass

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == "/":
                    self._send(_dashboard_html(outer.config.node_id), "text/html; charset=utf-8")
                    return
                if path == "/api/status":
                    self._json({"node_id": outer.config.node_id, **outer.status_provider()})
                    return
                if path == "/api/live-logs":
                    self._live_logs()
                    return
                if path == "/api/counters":
                    try:
                        outer.status_provider()
                    except Exception:
                        pass
                    self._json(outer.counters.snapshot(node_id=outer.config.node_id, uptime_seconds=0))
                    return
                if path == "/api/packets":
                    self._json(outer.ring.snapshot())
                    return
                if path == "/api/config":
                    self._json(redact_config(outer.config))
                    return
                if path == "/api/startup-profiles":
                    self._json(startup_profiles())
                    return
                if path == "/api/hotspot-profiles":
                    self._json(hotspot_profiles())
                    return
                if path == "/api/radio-profiles":
                    self._json(radio_profiles())
                    return
                self._json({"error": "not_found"}, 404)

            def do_POST(self) -> None:
                path = self.path.split("?", 1)[0]
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    self._json({"error": "invalid_json"}, 400)
                    return
                if path == "/api/hotspot-profile":
                    hotspot = str(payload.get("hotspot", ""))
                    if not apply_hotspot_profile(outer.config, hotspot):
                        self._json({"error": "unknown_hotspot", "hotspot": hotspot}, 400)
                        return
                    self._json({"ok": True, "hotspot": hotspot, "config": redact_config(outer.config)})
                    return
                if path == "/api/radio-profile":
                    profile = str(payload.get("profile", ""))
                    outer.config.manual_radio.enabled = True
                    outer.config.manual_radio.auto_start = bool(payload.get("auto_start", outer.config.manual_radio.auto_start))
                    if not apply_radio_profile(outer.config, profile):
                        self._json({"error": "unknown_radio_profile", "profile": profile}, 400)
                        return
                    self._json({"ok": True, "profile": profile, "config": redact_config(outer.config)})
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
