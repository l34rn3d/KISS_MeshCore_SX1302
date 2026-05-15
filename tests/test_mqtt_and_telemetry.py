from sx1302_meshcore_kiss.mqtt.schemas import build_rx_event, build_tx_requested_event, payload_encodings
from sx1302_meshcore_kiss.sx1302.metadata import RadioConfig, RxPacket, TxPacket
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer


def test_payload_encodings_include_hex_and_base64():
    assert payload_encodings(b"\x01\x02\x03\x04") == {
        "payload_len": 4,
        "payload_hex": "01020304",
        "payload_b64": "AQIDBA==",
    }


def test_rx_good_schema_contains_radio_metadata_and_crc():
    pkt = RxPacket(
        payload=b"abc",
        crc_ok=True,
        frequency_hz=915000000,
        bandwidth_hz=125000,
        spreading_factor=8,
        coding_rate=5,
        rssi_dbm=-97.5,
        snr_db=6.25,
        channel=3,
        concentrator_timestamp_us=123456789,
        raw_metadata={"if_chain": 3},
    )

    event = build_rx_event("repeater-01", pkt, status="good", forwarded_to_pymc=True)

    assert event["schema"] == "sx1302_meshcore_kiss.rx.good.v1"
    assert event["node_id"] == "repeater-01"
    assert event["payload_hex"] == "616263"
    assert event["radio"]["rssi_dbm"] == -97.5
    assert event["crc"] == {"crc_ok": True}
    assert event["raw_metadata"] == {"if_chain": 3}


def test_tx_requested_schema_uses_config_radio_values():
    pkt = TxPacket(payload=b"abc", frequency_hz=1, bandwidth_hz=2, spreading_factor=3, coding_rate=4, tx_power_dbm=5)
    event = build_tx_requested_event("node", pkt)

    assert event["schema"] == "sx1302_meshcore_kiss.tx.requested.v1"
    assert event["direction"] == "tx"
    assert event["radio"] == {
        "frequency_hz": 1,
        "bandwidth_hz": 2,
        "spreading_factor": 3,
        "coding_rate": 4,
        "tx_power_dbm": 5,
    }


def test_ring_buffer_keeps_only_newest_50_events():
    ring = PacketRingBuffer(maxlen=50)
    for i in range(60):
        ring.add({"seq": i})

    snapshot = ring.snapshot()
    assert len(snapshot) == 50
    assert snapshot[0]["seq"] == 10
    assert snapshot[-1]["seq"] == 59


def test_counters_snapshot_has_required_fields():
    counters = Counters(rx_good_count=1, rx_bad_crc_count=2, tx_done_count=3)
    snapshot = counters.snapshot(node_id="node", uptime_seconds=12)

    assert snapshot["schema"] == "sx1302_meshcore_kiss.counters.v1"
    assert snapshot["node_id"] == "node"
    assert snapshot["uptime_seconds"] == 12
    assert snapshot["counters"]["rx_good_count"] == 1
    assert snapshot["counters"]["rx_dropped_count"] == 0
