import asyncio

import pytest

from sx1302_meshcore_kiss.kiss.codec import CMD_DATA, CMD_RXMETA, KISS_CMD_TXDELAY, KissFrame, encode_frame
from sx1302_meshcore_kiss.radio.modem_service import ModemService
from sx1302_meshcore_kiss.sx1302.metadata import RadioConfig, RxPacket, TxResult
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer


class MemoryKissEndpoint:
    def __init__(self):
        self.writes = []

    async def write_frame(self, command, payload):
        self.writes.append((command, payload))


class RecordingPublisher:
    def __init__(self):
        self.published = []

    async def publish_event(self, topic_suffix, payload, *, retain=False, qos=None):
        self.published.append((topic_suffix, payload, retain, qos))


class FakeAdapter:
    def __init__(self):
        self.configured = []
        self.transmitted = []
        self.tx_result = TxResult(ok=True, error=None, airtime_ms=12.5, started_at="s", completed_at="c", raw_metadata={"tx_status": "free"})

    async def configure(self, config):
        self.configured.append(config)

    async def transmit(self, packet):
        self.transmitted.append(packet)
        return self.tx_result


@pytest.fixture
def service():
    return ModemService(
        node_id="repeater-01",
        radio_config=RadioConfig(frequency_hz=915000000, bandwidth_hz=125000, spreading_factor=8, coding_rate=5, tx_power_dbm=14),
        sx1302=FakeAdapter(),
        kiss=MemoryKissEndpoint(),
        mqtt=RecordingPublisher(),
        ring=PacketRingBuffer(maxlen=50),
        counters=Counters(),
    )


@pytest.mark.asyncio
async def test_tx_from_kiss_data_frame_queues_unmodified_payload_and_publishes(service):
    payload = bytes([0x01, 0xC0, 0xDB, 0x02])

    result = await service.handle_kiss_frame(KissFrame(CMD_DATA, payload))

    assert result is True
    assert service.sx1302.transmitted[0].payload == payload
    assert service.counters.tx_requested_count == 1
    assert service.counters.tx_done_count == 1
    assert service.ring.snapshot()[-1]["status"] == "tx_done"
    assert service.mqtt.published[0][0] == "tx/requested"
    assert service.mqtt.published[0][1]["payload_hex"] == payload.hex()
    assert service.mqtt.published[0][1]["payload_b64"] == "AcDbAg=="
    assert service.kiss.writes[-1][0] == 0xF8


@pytest.mark.asyncio
async def test_tx_rejects_payloads_over_255_before_sx1302(service):
    result = await service.handle_kiss_frame(KissFrame(CMD_DATA, b"x" * 256))

    assert result is False
    assert service.sx1302.transmitted == []
    assert service.counters.tx_error_count == 1
    assert service.mqtt.published[-1][0] == "tx/error"


@pytest.mark.asyncio
async def test_standard_kiss_config_command_is_accepted_without_unknown_error(service):
    result = await service.handle_kiss_frame(KissFrame(KISS_CMD_TXDELAY, b"\x1e"))

    assert result is True
    assert service.counters.kiss_unknown_command_count == 0
    assert service.mqtt.published[-1][0] == "kiss/config"
    assert service.ring.snapshot()[-1]["status"] == "kiss_config"


@pytest.mark.asyncio
async def test_unknown_kiss_command_is_ignored_and_reported(service):
    result = await service.handle_kiss_frame(KissFrame(0x42, b"ignored"))

    assert result is False
    assert service.counters.kiss_unknown_command_count == 1
    assert service.mqtt.published[-1][0] == "kiss/error"


@pytest.mark.asyncio
async def test_crc_ok_rx_is_forwarded_to_pymc_with_rxmeta_and_mqtt(service):
    payload = b"meshcore"
    pkt = RxPacket(payload=payload, crc_ok=True, frequency_hz=915000000, bandwidth_hz=125000, spreading_factor=8, coding_rate=5, rssi_dbm=-97.5, snr_db=6.25, channel=3, concentrator_timestamp_us=123, raw_metadata={"if_chain": 3})

    forwarded = await service.handle_rx_packet(pkt)

    assert forwarded is True
    assert service.kiss.writes[0] == (CMD_DATA, payload)
    assert service.kiss.writes[1][0] == CMD_RXMETA
    assert service.counters.rx_good_count == 1
    assert service.counters.rx_crc_ok_count == 1
    assert service.mqtt.published[-1][0] == "rx/good"
    assert service.mqtt.published[-1][1]["forwarded_to_pymc"] is True
    assert service.ring.snapshot()[-1]["status"] == "good"


@pytest.mark.asyncio
async def test_bad_crc_rx_is_not_forwarded_but_reported(service):
    pkt = RxPacket(payload=b"bad", crc_ok=False, frequency_hz=None, bandwidth_hz=None, spreading_factor=None, coding_rate=None, rssi_dbm=-110, snr_db=-7.5, channel=None, concentrator_timestamp_us=None, raw_metadata={})

    forwarded = await service.handle_rx_packet(pkt)

    assert forwarded is False
    assert service.kiss.writes == []
    assert service.counters.rx_bad_crc_count == 1
    assert service.counters.rx_dropped_count == 1
    assert service.mqtt.published[-1][0] == "rx/bad_crc"
    assert service.mqtt.published[-1][1]["forwarded_to_pymc"] is False


@pytest.mark.asyncio
async def test_unknown_crc_rx_drops_by_default_and_can_be_forwarded_for_lab_debug(service):
    pkt = RxPacket(payload=b"maybe", crc_ok=None, frequency_hz=None, bandwidth_hz=None, spreading_factor=None, coding_rate=None, rssi_dbm=None, snr_db=None, channel=None, concentrator_timestamp_us=None, raw_metadata={})

    assert await service.handle_rx_packet(pkt) is False
    assert service.kiss.writes == []
    assert service.counters.rx_unknown_crc_count == 1

    service.forward_unknown_crc = True
    assert await service.handle_rx_packet(pkt) is True
    assert service.kiss.writes[-1] == (CMD_DATA, b"maybe")
