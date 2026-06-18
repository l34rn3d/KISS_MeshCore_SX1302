from __future__ import annotations

import asyncio
import logging
import struct
import time
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from sx1302_meshcore_kiss.mqtt.schemas import build_rx_event, build_tx_requested_event, build_tx_result_event
from sx1302_meshcore_kiss.sx1302.metadata import RadioConfig, RxPacket, TxPacket

from .protocol import (
    CMD_AUTH,
    CMD_AUTH_OK,
    CMD_CAD_PARAMS_RESP,
    CMD_CAD_REQUEST,
    CMD_CAD_RESP,
    CMD_CONFIG_RESP,
    CMD_ERROR,
    CMD_GET_CONFIG,
    CMD_GET_VERSION,
    CMD_GET_WIFI,
    CMD_NOISE_REQ,
    CMD_NOISE_RESP,
    CMD_PING,
    CMD_PONG,
    CMD_RX_PACKET,
    CMD_RX_START,
    CMD_RX_STARTED,
    CMD_SET_CAD_PARAMS,
    CMD_SET_CONFIG,
    CMD_SET_WIFI,
    CMD_STATUS_REQ,
    CMD_STATUS_RESP,
    CMD_TX_DONE,
    CMD_TX_FAIL,
    CMD_TX_REQUEST,
    CMD_VERSION_RESP,
    CMD_WIFI_RESET,
    ERR_CAD_FAILED,
    ERR_INVALID_CMD,
    ERR_INVALID_CONFIG,
    ERR_PAYLOAD_TOO_BIG,
    ERR_RADIO_BUSY,
    ERR_RADIO_INIT,
    ERR_TX_TIMEOUT,
    ERR_UNAUTHORIZED,
    MAX_LORA_PAYLOAD,
    RADIO_CONFIG_FMT,
    RADIO_CONFIG_SIZE,
    STATUS_RESP_FMT,
    build_frame,
    parse_frame,
)

logger = logging.getLogger(__name__)


def _clamp_i16(value: float | int | None, default: int = 0) -> int:
    try:
        n = int(round(value if value is not None else default))
    except (TypeError, ValueError):
        n = default
    return max(-32768, min(32767, n))


class PyMCTcpServer:
    """Native pyMC TCP modem server backed by the SX1302 adapter."""

    def __init__(self, *, config: Any, adapter: Any, counters: Any, ring: Any, mqtt: Any) -> None:
        self.config = config
        self.adapter = adapter
        self.counters = counters
        self.ring = ring
        self.mqtt = mqtt
        self.bind_host = config.pymc_tcp.bind_host
        self.port = int(config.pymc_tcp.port)
        self.token = str(config.pymc_tcp.token or "")
        self.max_clients = int(config.pymc_tcp.max_clients)
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._client_locks: dict[asyncio.StreamWriter, asyncio.Lock] = {}
        self._started_at = time.monotonic()
        self._crc_errors = 0
        self._cad_params: bytes = b""
        self._cad_settings: dict[str, int] = {}

    @property
    def connected_clients(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.bind_host, self.port)
        socket = self._server.sockets[0] if self._server.sockets else None
        if socket is not None:
            self.port = int(socket.getsockname()[1])
        logger.info("pyMC TCP modem listening on %s:%d", self.bind_host, self.port)

    async def stop(self) -> None:
        for writer in list(self._clients):
            writer.close()
            await writer.wait_closed()
        self._clients.clear()
        self._client_locks.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.config.pymc_tcp.enabled),
            "bind_host": self.bind_host,
            "port": self.port,
            "connected_clients": self.connected_clients,
            "max_clients": self.max_clients,
            "auth_required": bool(self.token),
        }

    async def publish_rx_packet(self, packet: RxPacket) -> bool:
        if not self._clients:
            return False
        if packet.crc_ok is not True or not (1 <= len(packet.payload) <= MAX_LORA_PAYLOAD):
            return False
        payload = struct.pack(
            "<hhh",
            _clamp_i16(packet.rssi_dbm, -120),
            _clamp_i16((packet.snr_db or 0.0) * 10.0),
            _clamp_i16(packet.rssi_dbm, -120),
        ) + bytes(packet.payload)
        stale: list[asyncio.StreamWriter] = []
        for writer in list(self._clients):
            try:
                await self._write(writer, CMD_RX_PACKET, payload)
            except (ConnectionError, OSError):
                stale.append(writer)
        for writer in stale:
            self._clients.discard(writer)
            self._client_locks.pop(writer, None)
        return bool(self._clients)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if len(self._clients) >= self.max_clients:
            writer.write(build_frame(CMD_ERROR, bytes([ERR_RADIO_INIT])))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return
        self._clients.add(writer)
        self._client_locks[writer] = asyncio.Lock()
        authed = not self.token
        logger.info("pyMC TCP client connected: %s", peer)
        buffer = bytearray()
        try:
            while not reader.at_eof():
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    parsed = parse_frame(buffer)
                    if parsed is None:
                        break
                    command, payload = parsed
                    if command == CMD_ERROR:
                        self._crc_errors += 1
                        await self._write(writer, CMD_ERROR, payload)
                        continue
                    if not authed and command != CMD_AUTH:
                        await self._write(writer, CMD_ERROR, bytes([ERR_UNAUTHORIZED]))
                        continue
                    if command == CMD_AUTH:
                        authed = payload.decode("utf-8", errors="replace") == self.token
                        await self._write(writer, CMD_AUTH_OK if authed else CMD_ERROR, b"" if authed else bytes([ERR_UNAUTHORIZED]))
                        continue
                    await self._dispatch(command, payload, writer)
        except Exception:
            logger.exception("pyMC TCP client error: %s", peer)
        finally:
            self._clients.discard(writer)
            self._client_locks.pop(writer, None)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("pyMC TCP client disconnected: %s", peer)

    async def _write(self, writer: asyncio.StreamWriter, command: int, payload: bytes = b"") -> None:
        lock = self._client_locks.get(writer)
        if lock is None:
            writer.write(build_frame(command, payload))
            await writer.drain()
            return
        async with lock:
            writer.write(build_frame(command, payload))
            await writer.drain()

    async def _dispatch(self, command: int, payload: bytes, writer: asyncio.StreamWriter) -> None:
        if command == CMD_PING:
            await self._write(writer, CMD_PONG)
            return
        if command in (CMD_SET_CONFIG, CMD_GET_CONFIG):
            await self._handle_config(payload, writer, update=command == CMD_SET_CONFIG)
            return
        if command == CMD_TX_REQUEST:
            await self._handle_tx(payload, writer)
            return
        if command == CMD_STATUS_REQ:
            await self._write(writer, CMD_STATUS_RESP, self._status_payload())
            return
        if command == CMD_NOISE_REQ:
            noise = self.adapter.get_noise_floor() if hasattr(self.adapter, "get_noise_floor") else None
            await self._write(writer, CMD_NOISE_RESP, struct.pack("<h", _clamp_i16((noise if noise is not None else -120) * 10.0)))
            return
        if command == CMD_GET_VERSION:
            await self._write(writer, CMD_VERSION_RESP, self._version_payload())
            return
        if command in (CMD_SET_WIFI, CMD_GET_WIFI, CMD_WIFI_RESET):
            await self._write(writer, CMD_ERROR, bytes([ERR_INVALID_CMD]))
            return
        if command == CMD_CAD_REQUEST:
            await self._handle_cad_request(writer)
            return
        if command == CMD_RX_START:
            await self._handle_rx_start(writer)
            return
        if command == CMD_SET_CAD_PARAMS:
            if len(payload) != 4:
                await self._write(writer, CMD_ERROR, bytes([ERR_INVALID_CONFIG]))
                return
            self._cad_params = bytes(payload)
            self._cad_settings = {"sym_num": int(payload[0]), "det_peak": int(payload[1]), "det_min": int(payload[2]), "exit_mode": int(payload[3])}
            await self._write(writer, CMD_CAD_PARAMS_RESP, b"\x01")
            return
        await self._write(writer, CMD_ERROR, bytes([ERR_INVALID_CMD]))

    async def _handle_cad_request(self, writer: asyncio.StreamWriter) -> None:
        try:
            if hasattr(self.adapter, "perform_cad"):
                busy = await self.adapter.perform_cad(
                    sym_num=self._cad_settings.get("sym_num"),
                    det_peak=self._cad_settings.get("det_peak"),
                    det_min=self._cad_settings.get("det_min"),
                    exit_mode=self._cad_settings.get("exit_mode"),
                    timeout=max(0.1, float(getattr(self.config.radio, "lbt_cad_timeout_ms", 500)) / 1000.0),
                )
            else:
                busy = bool(self.adapter.is_channel_busy()) if hasattr(self.adapter, "is_channel_busy") else False
        except Exception as exc:
            logger.warning("CAD request failed: %s", exc)
            await self._write(writer, CMD_ERROR, bytes([ERR_CAD_FAILED]))
            return
        await self._write(writer, CMD_CAD_RESP, b"\x01" if busy else b"\x00")

    async def _handle_rx_start(self, writer: asyncio.StreamWriter) -> None:
        radio: RadioConfig = self.config.radio
        if not self.adapter.has_rf_config(radio):
            await self._write(writer, CMD_ERROR, bytes([ERR_INVALID_CONFIG]))
            return
        try:
            await self.adapter.configure(radio)
            await self.adapter.start()
        except Exception as exc:
            logger.exception("RX_START failed: %s", exc)
            await self._write(writer, CMD_ERROR, bytes([ERR_RADIO_INIT]))
            return
        await self._write(writer, CMD_RX_STARTED)

    async def _handle_config(self, payload: bytes, writer: asyncio.StreamWriter, *, update: bool) -> None:
        if update:
            if len(payload) != RADIO_CONFIG_SIZE:
                await self._write(writer, CMD_ERROR, bytes([ERR_INVALID_CONFIG]))
                return
            freq, bw, sf, cr, power, sync_word, preamble = struct.unpack(RADIO_CONFIG_FMT, payload)
            if freq <= 0 or bw <= 0 or sf < 5 or sf > 12 or cr < 5 or cr > 8:
                await self._write(writer, CMD_ERROR, bytes([ERR_INVALID_CONFIG]))
                return
            radio: RadioConfig = self.config.radio
            radio.frequency_hz = int(freq)
            radio.bandwidth_hz = int(bw)
            radio.spreading_factor = int(sf)
            radio.coding_rate = int(cr)
            radio.tx_power_dbm = int(power)
            radio.sync_word = int(sync_word)
            radio.preamble_len = int(preamble)
            await self.adapter.configure(radio)
        await self._write(writer, CMD_CONFIG_RESP, self._config_payload())

    def _config_payload(self) -> bytes:
        radio: RadioConfig = self.config.radio
        return struct.pack(
            RADIO_CONFIG_FMT,
            int(radio.frequency_hz or 0),
            int(radio.bandwidth_hz or 0),
            int(radio.spreading_factor or 0),
            int(radio.coding_rate or 0),
            int(radio.tx_power_dbm or 0),
            int(radio.sync_word or 0),
            int(radio.preamble_len or 0),
        )

    async def _handle_tx(self, payload: bytes, writer: asyncio.StreamWriter) -> None:
        if not (1 <= len(payload) <= MAX_LORA_PAYLOAD):
            await self._write(writer, CMD_ERROR, bytes([ERR_PAYLOAD_TOO_BIG]))
            return
        radio: RadioConfig = self.config.radio
        if not self.adapter.has_rf_config(radio):
            await self._write(writer, CMD_ERROR, bytes([ERR_INVALID_CONFIG]))
            return
        packet = TxPacket(
            payload=bytes(payload),
            frequency_hz=int(radio.frequency_hz),
            bandwidth_hz=int(radio.bandwidth_hz),
            spreading_factor=int(radio.spreading_factor),
            coding_rate=int(radio.coding_rate),
            tx_power_dbm=int(radio.tx_power_dbm),
            preamble_len=radio.preamble_len,
            sync_word=radio.sync_word,
            invert_iq=radio.invert_iq,
        )
        self.counters.tx_requested_count += 1
        requested = build_tx_requested_event(self.config.node_id, packet)
        self.ring.add({**requested, "status": "tx_requested", "transport": "pymc_tcp"})
        await self.mqtt.publish_event("tx/requested", requested)
        result = await self.adapter.transmit(packet)
        result_event = build_tx_result_event(self.config.node_id, packet, result)
        if result.ok:
            self.counters.tx_done_count += 1
            self.ring.add({**result_event, "status": "tx_done", "transport": "pymc_tcp"})
            await self.mqtt.publish_event("tx/done", result_event)
            airtime_us = int((result.airtime_ms or 0.0) * 1000.0)
            await self._write(writer, CMD_TX_DONE, struct.pack("<I", airtime_us))
        else:
            self.counters.tx_error_count += 1
            self.ring.add({**result_event, "status": "tx_error", "transport": "pymc_tcp"})
            await self.mqtt.publish_event("tx/error", result_event)
            await self._write(writer, CMD_TX_FAIL, bytes([self._tx_error_code(result.error)]))

    @staticmethod
    def _version_payload() -> bytes:
        try:
            text = f"sx1302-meshcore-kiss/{version('sx1302-meshcore-kiss')} pymc_tcp"
        except PackageNotFoundError:
            text = "sx1302-meshcore-kiss pymc_tcp"
        return text.encode("ascii", errors="replace")

    @staticmethod
    def _tx_error_code(error: str | None) -> int:
        text = str(error or "").lower()
        if "busy" in text:
            return ERR_RADIO_BUSY
        if "config" in text or "invalid" in text:
            return ERR_INVALID_CONFIG
        if "init" in text or "not configured" in text or "not initialized" in text:
            return ERR_RADIO_INIT
        return ERR_TX_TIMEOUT

    def _status_payload(self) -> bytes:
        uptime = int(time.monotonic() - self._started_at)
        rssi = self.adapter.get_current_rssi() if hasattr(self.adapter, "get_current_rssi") else None
        snr = self.adapter.get_last_snr() if hasattr(self.adapter, "get_last_snr") else None
        noise = self.adapter.get_noise_floor() if hasattr(self.adapter, "get_noise_floor") else None
        temp = self.adapter.get_temperature() if hasattr(self.adapter, "get_temperature") else None
        radio_state = self._radio_state_code()
        return struct.pack(
            STATUS_RESP_FMT,
            uptime,
            int(self.counters.rx_good_count),
            int(self.counters.tx_done_count),
            int(self._crc_errors + self.counters.rx_bad_crc_count),
            _clamp_i16(rssi, -120),
            _clamp_i16((snr if snr is not None else 0.0) * 10.0),
            _clamp_i16((noise if noise is not None else -120) * 10.0),
            max(-128, min(127, int(round(temp if temp is not None else 0)))),
            radio_state,
        )

    def _radio_state_code(self) -> int:
        if not self.adapter.has_rf_config(self.config.radio) or self.adapter.radio is None:
            return 2
        status: dict[str, Any] = {}
        if hasattr(self.adapter.radio, "get_status"):
            try:
                status = dict(self.adapter.radio.get_status() or {})
            except Exception:
                return 2
        if any(str(status.get(key, "")).lower() in {"emitting", "scheduled", "tx"} for key in ("tx_status", "state", "radio_state")):
            return 1
        try:
            if bool(self.adapter.is_channel_busy()):
                return 1
        except Exception:
            return 2
        started = bool(status.get("started", status.get("initialized", getattr(self.adapter, "_started", False))))
        return 0 if started else 2
