import asyncio
import struct

import pytest

from sx1302_meshcore_kiss.config import AppConfig, load_config, startup_profiles
from sx1302_meshcore_kiss.mqtt.publisher import MqttPublisher
from sx1302_meshcore_kiss.pymc_tcp.protocol import (
    CMD_CONFIG_RESP,
    CMD_CAD_PARAMS_RESP,
    CMD_CAD_REQUEST,
    CMD_CAD_RESP,
    CMD_ERROR,
    CMD_GET_VERSION,
    CMD_GET_WIFI,
    CMD_PING,
    CMD_PONG,
    CMD_RX_PACKET,
    CMD_RX_START,
    CMD_RX_STARTED,
    CMD_SET_WIFI,
    CMD_SET_CONFIG,
    CMD_SET_CAD_PARAMS,
    CMD_STATUS_REQ,
    CMD_STATUS_RESP,
    CMD_TX_DONE,
    CMD_TX_FAIL,
    CMD_TX_REQUEST,
    CMD_VERSION_RESP,
    CMD_WIFI_RESET,
    ERR_INVALID_CMD,
    ERR_INVALID_CONFIG,
    ERR_RADIO_BUSY,
    RADIO_CONFIG_FMT,
    STATUS_RESP_FMT,
    build_frame,
    parse_frame,
)
from sx1302_meshcore_kiss.pymc_tcp.server import PyMCTcpServer
from sx1302_meshcore_kiss.sx1302.adapter import SX1302Adapter
from sx1302_meshcore_kiss.sx1302.metadata import RadioConfig, RxPacket
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer


class FakeRadio:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.sent = []
        self.begin_count = 0
        self.status = {"started": True, "tx_status": "free"}
        self.cad_calls = []
        self.cad_busy = False

    def begin(self):
        self.begin_count += 1

    def cleanup(self):
        pass

    async def send(self, payload):
        self.sent.append(bytes(payload))
        return {"success": True, "airtime_ms": 12.5}

    def get_status(self):
        return dict(self.status)

    def get_last_signal_rssi(self):
        return -97

    def get_last_snr(self):
        return 4.0

    def get_noise_floor(self):
        return -118.0

    def get_temperature(self):
        return 42.4

    async def perform_cad(self, **kwargs):
        self.cad_calls.append(kwargs)
        return self.cad_busy


async def read_frame(reader):
    buffer = bytearray()
    while True:
        parsed = parse_frame(buffer)
        if parsed is not None:
            return parsed
        buffer.extend(await reader.read(1024))


@pytest.mark.asyncio
async def test_pymc_tcp_ping_config_and_tx_roundtrip():
    config = AppConfig()
    config.pymc_tcp.port = 0
    radio = FakeRadio()
    adapter = SX1302Adapter(radio_factory=lambda **kwargs: radio)
    counters = Counters(rx_bad_crc_count=2, tx_error_count=7)
    server = PyMCTcpServer(config=config, adapter=adapter, counters=counters, ring=PacketRingBuffer(10), mqtt=MqttPublisher())
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)

        writer.write(build_frame(CMD_PING))
        await writer.drain()
        assert await read_frame(reader) == (CMD_PONG, b"")

        writer.write(build_frame(CMD_GET_VERSION))
        await writer.drain()
        command, response = await read_frame(reader)
        assert command == CMD_VERSION_RESP
        assert b"pymc_tcp" in response

        for wifi_command in (CMD_SET_WIFI, CMD_GET_WIFI, CMD_WIFI_RESET):
            writer.write(build_frame(wifi_command))
            await writer.drain()
            assert await read_frame(reader) == (CMD_ERROR, bytes([ERR_INVALID_CMD]))

        payload = struct.pack(RADIO_CONFIG_FMT, 915000000, 125000, 8, 5, 14, 0x12, 16)
        writer.write(build_frame(CMD_SET_CONFIG, payload))
        await writer.drain()
        command, response = await read_frame(reader)
        assert command == CMD_CONFIG_RESP
        assert response == payload
        assert config.radio.frequency_hz == 915000000

        writer.write(build_frame(CMD_TX_REQUEST, b"abc"))
        await writer.drain()
        command, response = await read_frame(reader)
        assert command == CMD_TX_DONE
        assert struct.unpack("<I", response)[0] == 12500
        assert radio.sent == [b"abc"]

        writer.write(build_frame(CMD_STATUS_REQ))
        await writer.drain()
        command, response = await read_frame(reader)
        assert command == CMD_STATUS_RESP
        fields = struct.unpack(STATUS_RESP_FMT, response)
        assert fields[5] == 40
        assert fields[7] == 42
        assert fields[8] == 0

        writer.write(build_frame(CMD_RX_START))
        await writer.drain()
        assert await read_frame(reader) == (CMD_RX_STARTED, b"")
        assert radio.begin_count == 1

        writer.write(build_frame(CMD_RX_START))
        await writer.drain()
        assert await read_frame(reader) == (CMD_RX_STARTED, b"")
        assert radio.begin_count == 1

        writer.write(build_frame(CMD_SET_CAD_PARAMS, bytes([1, 22, 10])))
        await writer.drain()
        assert await read_frame(reader) == (CMD_ERROR, bytes([ERR_INVALID_CONFIG]))

        writer.write(build_frame(CMD_SET_CAD_PARAMS, bytes([1, 22, 10, 0])))
        await writer.drain()
        assert await read_frame(reader) == (CMD_CAD_PARAMS_RESP, b"\x01")
        radio.cad_busy = True
        writer.write(build_frame(CMD_CAD_REQUEST))
        await writer.drain()
        assert await read_frame(reader) == (CMD_CAD_RESP, b"\x01")
        assert radio.cad_calls[-1]["sym_num"] == 1
        assert radio.cad_calls[-1]["det_peak"] == 22
        assert radio.cad_calls[-1]["det_min"] == 10
        assert radio.cad_calls[-1]["exit_mode"] == 0
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_pymc_tcp_parser_accepts_large_non_lora_frame_payloads():
    payload = b"x" * 512
    buffer = bytearray(build_frame(CMD_GET_VERSION, payload))

    assert parse_frame(buffer) == (CMD_GET_VERSION, payload)


def test_pymc_tcp_tx_error_mapping():
    assert PyMCTcpServer._tx_error_code("radio busy") == ERR_RADIO_BUSY
    assert PyMCTcpServer._tx_error_code("invalid config") == ERR_INVALID_CONFIG
    assert PyMCTcpServer._tx_error_code("not configured") != ERR_RADIO_BUSY


@pytest.mark.asyncio
async def test_pymc_tcp_status_reports_tx_state_when_radio_busy():
    config = AppConfig()
    config.pymc_tcp.port = 0
    radio = FakeRadio()
    adapter = SX1302Adapter(radio_factory=lambda **kwargs: radio)
    server = PyMCTcpServer(config=config, adapter=adapter, counters=Counters(), ring=PacketRingBuffer(10), mqtt=MqttPublisher())
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        payload = struct.pack(RADIO_CONFIG_FMT, 915000000, 125000, 8, 5, 14, 0x12, 16)
        writer.write(build_frame(CMD_SET_CONFIG, payload))
        await writer.drain()
        assert (await read_frame(reader))[0] == CMD_CONFIG_RESP

        radio.status["tx_status"] = "emitting"
        writer.write(build_frame(CMD_STATUS_REQ))
        await writer.drain()
        command, response = await read_frame(reader)
        assert command == CMD_STATUS_RESP
        assert struct.unpack(STATUS_RESP_FMT, response)[8] == 1
        assert struct.unpack(STATUS_RESP_FMT, response)[3] == 2
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.asyncio
async def test_pymc_tcp_publish_rx_packet_uses_native_payload_shape():
    config = AppConfig()
    config.pymc_tcp.port = 0
    adapter = SX1302Adapter(radio_factory=lambda **kwargs: FakeRadio(**kwargs))
    server = PyMCTcpServer(config=config, adapter=adapter, counters=Counters(), ring=PacketRingBuffer(10), mqtt=MqttPublisher())
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        packet = RxPacket(b"hello", True, 915000000, 125000, 8, 5, -98.0, 6.5, None, None, {})

        assert await server.publish_rx_packet(packet) is True

        command, payload = await read_frame(reader)
        assert command == CMD_RX_PACKET
        rssi, snr_x10, signal_rssi = struct.unpack("<hhh", payload[:6])
        assert (rssi, snr_x10, signal_rssi) == (-98, 65, -98)
        assert payload[6:] == b"hello"
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_config_loads_native_tcp_and_startup_profile_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('startup:\n  profile: "nebra_helium_docker"\n  hotspot: "pantherx1-fl1"\npymc_tcp:\n  port: 5056\n')

    config = load_config(path)

    assert config.pymc_tcp.enabled is True
    assert config.pymc_tcp.port == 5056
    assert config.startup.profile == "nebra_helium_docker"
    assert "nebra_helium_docker" in startup_profiles()
    assert config.startup.hotspot == "pantherx1-fl1"
    assert config.radio.sx1302_reset_pin == 23
    assert config.radio.reset_script_env["CONCENTRATOR_RESET_PIN"] == "23"
