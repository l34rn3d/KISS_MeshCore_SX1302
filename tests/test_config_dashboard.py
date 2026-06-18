import json
from unittest.mock import patch
from urllib import request

from sx1302_meshcore_kiss.config import AppConfig, load_config, radio_profiles, redact_config
from sx1302_meshcore_kiss.dashboard.app import DashboardServer
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer


def test_load_config_defaults_to_network_dashboard_and_pty(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('node_id: "node-1"\n')

    config = load_config(path)

    assert config.node_id == "node-1"
    assert config.transport == "pymc_tcp"
    assert config.kiss.mode == "pty"
    assert config.kiss.symlink == "/run/sx1302-meshcore-kiss/sx1302-kiss"
    assert config.dashboard.bind_host == "0.0.0.0"
    assert config.dashboard.max_packet_events == 50
    assert config.crc.forward_unknown_crc is True
    assert config.crc.publish_bad_crc_payload is False
    assert config.mqtt.enabled is False
    assert config.radio.frequency_hz is None
    assert config.radio.bandwidth_hz is None
    assert config.radio.spreading_factor is None
    assert config.radio.coding_rate is None
    assert config.radio.tx_power_dbm is None
    assert config.radio.sync_word is None
    assert config.radio.sx1261_spi_path is None
    assert config.radio.sx1302_reset_pin == 17
    assert config.radio.sx1261_reset_pin is None
    assert config.radio.reset_script_env["CONCENTRATOR_RESET_PIN"] == "17"
    assert config.manual_radio.enabled is False
    assert "meshcore_eu_default" in radio_profiles()


def test_manual_radio_profile_populates_rf_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('manual_radio:\n  enabled: true\n  profile: "meshcore_eu_default"\n  auto_start: true\n')

    config = load_config(path)

    assert config.manual_radio.enabled is True
    assert config.manual_radio.profile == "meshcore_eu_default"
    assert config.manual_radio.auto_start is True
    assert config.radio.frequency_hz == 869_618_000
    assert config.radio.bandwidth_hz == 62_500
    assert config.radio.spreading_factor == 8
    assert config.radio.coding_rate == 5
    assert config.radio.tx_power_dbm == 22


def test_manual_radio_profile_preserves_explicit_radio_overrides(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('manual_radio:\n  enabled: true\n  profile: "meshcore_eu_default"\nradio:\n  frequency_hz: 915000000\n')

    config = load_config(path)

    assert config.radio.frequency_hz == 915_000_000
    assert config.radio.bandwidth_hz == 62_500


def test_load_config_rejects_unknown_transport(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('transport: "both"\n')

    try:
        load_config(path)
    except ValueError as exc:
        assert "transport" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_redact_config_hides_mqtt_password_and_keeps_payload_flags():
    config = AppConfig()
    config.mqtt.password = "secret"
    config.crc.dashboard_show_bad_crc_payload = True

    redacted = redact_config(config)

    assert redacted["mqtt"]["password"] == "<redacted>"
    assert redacted["crc"]["dashboard_show_bad_crc_payload"] is True


def test_dashboard_api_returns_status_counters_packets_and_redacted_config():
    ring = PacketRingBuffer(maxlen=50)
    ring.add({"status": "good", "payload_hex": "01"})
    config = AppConfig(node_id="node")
    config.dashboard.port = 0
    config.mqtt.password = "secret"
    server = DashboardServer(config=config, counters=Counters(rx_good_count=1), ring=ring, status_provider=lambda: {"radio": "ok"})
    server.start()
    try:
        base = f"http://{server.bind_host}:{server.port}"
        status = json.loads(request.urlopen(base + "/api/status", timeout=2).read())
        counters = json.loads(request.urlopen(base + "/api/counters", timeout=2).read())
        packets = json.loads(request.urlopen(base + "/api/packets", timeout=2).read())
        redacted = json.loads(request.urlopen(base + "/api/config", timeout=2).read())
        hotspots = json.loads(request.urlopen(base + "/api/hotspot-profiles", timeout=2).read())
        radio_profile_data = json.loads(request.urlopen(base + "/api/radio-profiles", timeout=2).read())

        assert status["node_id"] == "node"
        assert status["radio"] == "ok"
        assert counters["counters"]["rx_good_count"] == 1
        assert packets[0]["payload_hex"] == "01"
        assert redacted["mqtt"]["password"] == "<redacted>"
        assert "sensecap-fl1" in hotspots
        assert "meshcore_eu_default" in radio_profile_data
    finally:
        server.stop()


def test_dashboard_can_apply_hotspot_profile():
    config = AppConfig(node_id="node")
    config.dashboard.port = 0
    server = DashboardServer(config=config, counters=Counters(), ring=PacketRingBuffer(maxlen=10))
    server.start()
    try:
        base = f"http://{server.bind_host}:{server.port}"
        body = json.dumps({"hotspot": "rak-fl1"}).encode()
        req = request.Request(base + "/api/hotspot-profile", data=body, headers={"Content-Type": "application/json"}, method="POST")
        applied = json.loads(request.urlopen(req, timeout=2).read())

        assert applied["ok"] is True
        assert config.startup.hotspot == "rak-fl1"
        assert config.radio.spi_device == "/dev/spidev0.0"
        assert config.radio.sx1302_reset_pin == 25
        assert config.radio.reset_script_env["CONCENTRATOR_RESET_PIN"] == "25"
    finally:
        server.stop()


def test_dashboard_can_apply_radio_profile():
    config = AppConfig(node_id="node")
    config.dashboard.port = 0
    server = DashboardServer(config=config, counters=Counters(), ring=PacketRingBuffer(maxlen=10))
    server.start()
    try:
        base = f"http://{server.bind_host}:{server.port}"
        body = json.dumps({"profile": "meshcore_us_default", "auto_start": True}).encode()
        req = request.Request(base + "/api/radio-profile", data=body, headers={"Content-Type": "application/json"}, method="POST")
        applied = json.loads(request.urlopen(req, timeout=2).read())

        assert applied["ok"] is True
        assert config.manual_radio.enabled is True
        assert config.manual_radio.auto_start is True
        assert config.manual_radio.profile == "meshcore_us_default"
        assert config.radio.frequency_hz == 915_000_000
        assert config.radio.bandwidth_hz == 125_000
    finally:
        server.stop()


def test_dashboard_can_save_transport(tmp_path):
    config_path = tmp_path / "config.yaml"
    config = AppConfig(node_id="node")
    config.dashboard.port = 0
    server = DashboardServer(config=config, counters=Counters(), ring=PacketRingBuffer(maxlen=10), config_path=config_path)
    server.start()
    try:
        base = f"http://{server.bind_host}:{server.port}"
        body = json.dumps({"transport": "kiss"}).encode()
        req = request.Request(base + "/api/transport", data=body, headers={"Content-Type": "application/json"}, method="POST")
        applied = json.loads(request.urlopen(req, timeout=2).read())

        assert applied["ok"] is True
        assert applied["transport"] == "kiss"
        assert config.transport == "kiss"
        assert config.pymc_tcp.enabled is False
        assert "transport: kiss" in config_path.read_text()
    finally:
        server.stop()


def test_dashboard_restart_endpoint_schedules_process_restart():
    config = AppConfig(node_id="node")
    config.dashboard.port = 0
    server = DashboardServer(config=config, counters=Counters(), ring=PacketRingBuffer(maxlen=10))
    server.start()
    try:
        base = f"http://{server.bind_host}:{server.port}"
        with patch.object(DashboardServer, "_restart_process_later") as restart:
            req = request.Request(base + "/api/restart", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
            applied = json.loads(request.urlopen(req, timeout=2).read())

        assert applied == {"ok": True, "restart": "scheduled"}
        restart.assert_called_once()
    finally:
        server.stop()


def test_dashboard_homepage_is_usable_status_ui():
    ring = PacketRingBuffer(maxlen=50)
    ring.add({
        "direction": "rx",
        "status": "good",
        "payload_len": 3,
        "payload_hex": "aabbcc",
        "rssi_dbm": -98,
        "snr_db": 6.5,
        "frequency_hz": 915000000,
    })
    config = AppConfig(node_id="node-ui")
    config.dashboard.port = 0
    server = DashboardServer(
        config=config,
        counters=Counters(rx_good_count=1, tx_done_count=2),
        ring=ring,
        status_provider=lambda: {"radio": {"started": True}, "mqtt": {"connected": False}, "kiss": {"mode": "pty"}},
    )
    server.start()
    try:
        base = f"http://{server.bind_host}:{server.port}"
        with request.urlopen(base + "/", timeout=2) as response:
            html = response.read().decode()
            content_type = response.headers["Content-Type"]

        assert response.status == 200
        assert "text/html" in content_type
        assert "node-ui" in html
        assert "Radio" in html
        assert "MQTT" in html
        assert "Compatibility / MQTT" in html
        assert "Host Interface" in html
        assert "Save transport" in html
        assert "Restart service" in html
        assert "Counters" in html
        assert "Latest packet events" in html
        assert "fetch('/api/status')" in html or "getJson('/api/status')" in html
        assert "rx_good_count" in html
        assert "payload_hex" in html
        assert "Radio State" in html
        assert "62.5 kHz / SX1261" in html
        assert "Activity Counters" in html
        assert "Active config" in html
        assert "config-groups" in html
        assert "Live daemon logs" in html
        assert "EventSource('/api/live-logs')" in html
        assert "aabbcc" not in html  # packet payloads come from API fetches, not server-rendered history
    finally:
        server.stop()
