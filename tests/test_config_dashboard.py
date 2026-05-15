import json
from urllib import request

from sx1302_meshcore_kiss.config import AppConfig, load_config, redact_config
from sx1302_meshcore_kiss.dashboard.app import DashboardServer
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer


def test_load_config_defaults_to_local_dashboard_and_pty(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('node_id: "node-1"\n')

    config = load_config(path)

    assert config.node_id == "node-1"
    assert config.kiss.mode == "pty"
    assert config.kiss.symlink == "/tmp/sx1302-kiss"
    assert config.dashboard.bind_host == "127.0.0.1"
    assert config.dashboard.max_packet_events == 50
    assert config.crc.forward_unknown_crc is False


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
