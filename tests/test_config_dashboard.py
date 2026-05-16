import json
from urllib import request

from sx1302_meshcore_kiss.config import AppConfig, load_config, redact_config
from sx1302_meshcore_kiss.dashboard.app import DashboardServer
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer


def test_load_config_defaults_to_network_dashboard_and_pty(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('node_id: "node-1"\n')

    config = load_config(path)

    assert config.node_id == "node-1"
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
    assert config.radio.sx1261_reset_pin == 5


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
    config.mqtt.password = "secret"
    server = DashboardServer(config=config, counters=Counters(rx_good_count=1), ring=ring, status_provider=lambda: {"radio": "ok"})
    server.start()
    try:
        base = f"http://{server.bind_host}:{server.port}"
        status = json.loads(request.urlopen(base + "/api/status", timeout=2).read())
        counters = json.loads(request.urlopen(base + "/api/counters", timeout=2).read())
        packets = json.loads(request.urlopen(base + "/api/packets", timeout=2).read())
        redacted = json.loads(request.urlopen(base + "/api/config", timeout=2).read())

        assert status["node_id"] == "node"
        assert status["radio"] == "ok"
        assert counters["counters"]["rx_good_count"] == 1
        assert packets[0]["payload_hex"] == "01"
        assert redacted["mqtt"]["password"] == "<redacted>"
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
        assert "KISS" in html
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
