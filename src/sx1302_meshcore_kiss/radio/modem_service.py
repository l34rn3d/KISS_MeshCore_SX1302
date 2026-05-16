from __future__ import annotations
import hashlib
import logging
import os
import struct
from typing import Any, Optional
from sx1302_meshcore_kiss.kiss.codec import (
    CMD_DATA,
    KISS_CMD_FULLDUP,
    KISS_CMD_PERSIST,
    KISS_CMD_RETURN,
    KISS_CMD_SETHARDWARE,
    KISS_CMD_SLOTTIME,
    KISS_CMD_TXDELAY,
    KISS_CMD_TXTAIL,
    MESHCORE_ERROR_INVALID_LENGTH,
    MESHCORE_ERROR_INVALID_PARAM,
    MESHCORE_ERROR_NO_CALLBACK,
    MESHCORE_ERROR_UNKNOWN_CMD,
    MESHCORE_SUB_ERROR,
    MESHCORE_SUB_GET_AIRTIME,
    MESHCORE_SUB_GET_BATTERY,
    MESHCORE_SUB_GET_CURRENT_RSSI,
    MESHCORE_SUB_GET_DEVICE_NAME,
    MESHCORE_SUB_GET_IDENTITY,
    MESHCORE_SUB_GET_MCU_TEMP,
    MESHCORE_SUB_GET_NOISE_FLOOR,
    MESHCORE_SUB_GET_RANDOM,
    MESHCORE_SUB_GET_RADIO,
    MESHCORE_SUB_GET_SENSORS,
    MESHCORE_SUB_GET_SIGNAL_REPORT,
    MESHCORE_SUB_GET_STATS,
    MESHCORE_SUB_GET_TX_POWER,
    MESHCORE_SUB_GET_VERSION,
    MESHCORE_SUB_HASH,
    MESHCORE_SUB_IS_CHANNEL_BUSY,
    MESHCORE_SUB_OK,
    MESHCORE_SUB_PING,
    MESHCORE_SUB_REBOOT,
    MESHCORE_SUB_RXMETA,
    MESHCORE_SUB_SET_RADIO,
    MESHCORE_SUB_SET_SIGNAL_REPORT,
    MESHCORE_SUB_SET_TX_POWER,
    MESHCORE_SUB_TXDONE,
    KissFrame,
    _clamp_i8,
)
from sx1302_meshcore_kiss.mqtt.schemas import build_rx_event, build_tx_requested_event, build_tx_result_event
from sx1302_meshcore_kiss.sx1302.metadata import RadioConfig, RxPacket, TxPacket
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer

logger = logging.getLogger(__name__)

class ModemService:
    # This daemon is a radio modem bridge. MeshCore identity/crypto operations
    # require a real node key store, so they intentionally report NoCallback.
    _CRYPTO_SUBCOMMANDS = {0x03, 0x04, 0x05, 0x06, 0x07}

    _CONFIG_COMMAND_NAMES = {
        KISS_CMD_TXDELAY: "txdelay",
        KISS_CMD_PERSIST: "persist",
        KISS_CMD_SLOTTIME: "slottime",
        KISS_CMD_TXTAIL: "txtail",
        KISS_CMD_FULLDUP: "fulldup",
        KISS_CMD_RETURN: "return",
    }

    def __init__(self, *, node_id: str, radio_config: RadioConfig, sx1302: Any, kiss: Any, mqtt: Any, ring: PacketRingBuffer, counters: Counters, forward_unknown_crc: bool = False, emit_rxmeta: bool = True, emit_txdone: bool = True) -> None:
        self.node_id=node_id; self.radio_config=radio_config; self.sx1302=sx1302; self.kiss=kiss; self.mqtt=mqtt; self.ring=ring; self.counters=counters; self.forward_unknown_crc=forward_unknown_crc; self.emit_rxmeta=emit_rxmeta; self.emit_txdone=emit_txdone
        self.kiss_config: dict[str, int | bool | None] = {}
        self.modem_identity = hashlib.sha256(f"sx1302-meshcore-kiss:{node_id}".encode()).digest()

    def _has_rf_config(self) -> bool:
        return all(
            value is not None
            for value in (
                self.radio_config.frequency_hz,
                self.radio_config.bandwidth_hz,
                self.radio_config.spreading_factor,
                self.radio_config.coding_rate,
                self.radio_config.tx_power_dbm,
            )
        )

    def _tx_packet(self, payload: bytes) -> TxPacket:
        if not self._has_rf_config():
            raise RuntimeError("radio not configured by host SetRadio/SetTxPower")
        return TxPacket(payload=bytes(payload), frequency_hz=int(self.radio_config.frequency_hz), bandwidth_hz=int(self.radio_config.bandwidth_hz), spreading_factor=int(self.radio_config.spreading_factor), coding_rate=int(self.radio_config.coding_rate), tx_power_dbm=int(self.radio_config.tx_power_dbm), preamble_len=self.radio_config.preamble_len, sync_word=self.radio_config.sync_word, invert_iq=self.radio_config.invert_iq)

    async def handle_kiss_frame(self, frame: KissFrame) -> bool:
        if frame.command in self._CONFIG_COMMAND_NAMES:
            return await self._handle_config_command(frame)
        if frame.command == KISS_CMD_SETHARDWARE:
            return await self._handle_sethardware(frame)
        if frame.command != CMD_DATA:
            self.counters.kiss_unknown_command_count += 1
            await self.mqtt.publish_event("kiss/error", {"status":"unknown_command", "command": frame.command})
            self.ring.add({"direction":"kiss", "status":"unknown_command", "command": frame.command})
            return False
        if not (1 <= len(frame.payload) <= 255):
            self.counters.tx_error_count += 1
            logger.warning("KISS data frame rejected: invalid payload_len=%d", len(frame.payload))
            await self.mqtt.publish_event("tx/error", {"status":"invalid_payload_length", "payload_len": len(frame.payload)})
            self.ring.add({"direction":"tx", "status":"invalid_payload_length", "payload_len": len(frame.payload)})
            return False
        try:
            pkt = self._tx_packet(frame.payload)
        except RuntimeError as exc:
            self.counters.tx_error_count += 1
            logger.warning("KISS TX rejected before radio configure: payload_len=%d error=%s", len(frame.payload), exc)
            event = {"direction":"tx", "status":"radio_not_configured", "error": str(exc), "payload_len": len(frame.payload)}
            self.ring.add(event)
            await self.mqtt.publish_event("tx/error", event)
            return False
        self.counters.tx_requested_count += 1
        logger.info(
            "KISS TX request: payload_len=%d freq=%s bw=%s sf=%s cr=4/%s power=%s",
            len(pkt.payload), pkt.frequency_hz, pkt.bandwidth_hz, pkt.spreading_factor, pkt.coding_rate, pkt.tx_power_dbm,
        )
        requested = build_tx_requested_event(self.node_id, pkt)
        self.ring.add({**requested, "status":"tx_requested"})
        await self.mqtt.publish_event("tx/requested", requested)
        result = await self.sx1302.transmit(pkt)
        logger.info(
            "KISS TX result: ok=%s error=%s airtime_ms=%s metadata=%s",
            result.ok, result.error, result.airtime_ms, result.raw_metadata,
        )
        result_event = build_tx_result_event(self.node_id, pkt, result)
        if result.ok:
            self.counters.tx_done_count += 1
            topic = "tx/done"
            ring_status = "tx_done"
        else:
            self.counters.tx_error_count += 1
            topic = "tx/error"
            ring_status = "tx_error"
        self.ring.add({**result_event, "status": ring_status})
        await self.mqtt.publish_event(topic, result_event)
        if self.emit_txdone:
            await self.kiss.write_frame(KISS_CMD_SETHARDWARE, bytes([MESHCORE_SUB_TXDONE, 0x01 if result.ok else 0x00]))
        return result.ok

    async def _write_sethardware(self, subcommand: int, payload: bytes = b"") -> None:
        await self.kiss.write_frame(KISS_CMD_SETHARDWARE, bytes([subcommand & 0xFF]) + bytes(payload))

    async def _write_sethardware_error(self, error_code: int) -> None:
        await self._write_sethardware(MESHCORE_SUB_ERROR, bytes([error_code & 0xFF]))

    async def _handle_sethardware(self, frame: KissFrame) -> bool:
        if not frame.payload:
            await self._write_sethardware_error(MESHCORE_ERROR_INVALID_LENGTH)
            await self.mqtt.publish_event("kiss/error", {"status": "sethardware_invalid_length"})
            return False

        subcmd = frame.payload[0]
        data = frame.payload[1:]
        try:
            handled = await self._dispatch_sethardware(subcmd, data)
        except (ValueError, struct.error):
            await self._write_sethardware_error(MESHCORE_ERROR_INVALID_PARAM)
            await self.mqtt.publish_event("kiss/error", {"status": "sethardware_invalid_param", "subcommand": subcmd})
            return False
        if handled:
            await self.mqtt.publish_event("kiss/sethardware", {"status": "handled", "subcommand": subcmd})
        return handled

    async def _dispatch_sethardware(self, subcmd: int, data: bytes) -> bool:
        if subcmd == MESHCORE_SUB_GET_IDENTITY:
            await self._write_sethardware(MESHCORE_SUB_GET_IDENTITY | 0x80, self.modem_identity)
            return True
        if subcmd == MESHCORE_SUB_GET_RANDOM:
            if len(data) != 1 or data[0] < 1 or data[0] > 64:
                await self._write_sethardware_error(MESHCORE_ERROR_INVALID_PARAM)
                return False
            await self._write_sethardware(MESHCORE_SUB_GET_RANDOM | 0x80, os.urandom(data[0]))
            return True
        if subcmd == MESHCORE_SUB_HASH:
            await self._write_sethardware(MESHCORE_SUB_HASH | 0x80, hashlib.sha256(data).digest())
            return True
        if subcmd == MESHCORE_SUB_SET_RADIO:
            if len(data) != 10:
                await self._write_sethardware_error(MESHCORE_ERROR_INVALID_LENGTH)
                return False
            freq, bw, sf, cr = struct.unpack("<IIBB", data)
            if sf < 5 or sf > 12 or cr < 5 or cr > 8 or bw <= 0 or freq <= 0:
                await self._write_sethardware_error(MESHCORE_ERROR_INVALID_PARAM)
                return False
            logger.info("Host SetRadio: freq=%d bw=%d sf=%d cr=4/%d", freq, bw, sf, cr)
            self.radio_config.frequency_hz = freq
            self.radio_config.bandwidth_hz = bw
            self.radio_config.spreading_factor = sf
            self.radio_config.coding_rate = cr
            if hasattr(self.sx1302, "configure"):
                await self.sx1302.configure(self.radio_config)
            await self._write_sethardware(MESHCORE_SUB_OK)
            return True
        if subcmd == MESHCORE_SUB_SET_TX_POWER:
            if len(data) != 1:
                await self._write_sethardware_error(MESHCORE_ERROR_INVALID_LENGTH)
                return False
            self.radio_config.tx_power_dbm = int.from_bytes(data, "little", signed=True)
            logger.info("Host SetTxPower: power=%d dBm", self.radio_config.tx_power_dbm)
            if hasattr(self.sx1302, "configure"):
                await self.sx1302.configure(self.radio_config)
            await self._write_sethardware(MESHCORE_SUB_OK)
            return True
        if subcmd == MESHCORE_SUB_GET_RADIO:
            if not all(v is not None for v in (self.radio_config.frequency_hz, self.radio_config.bandwidth_hz, self.radio_config.spreading_factor, self.radio_config.coding_rate)):
                await self._write_sethardware_error(MESHCORE_ERROR_INVALID_PARAM)
                return False
            await self._write_sethardware(
                MESHCORE_SUB_GET_RADIO | 0x80,
                struct.pack("<IIBB", int(self.radio_config.frequency_hz), int(self.radio_config.bandwidth_hz), int(self.radio_config.spreading_factor), int(self.radio_config.coding_rate)),
            )
            return True
        if subcmd == MESHCORE_SUB_GET_TX_POWER:
            if self.radio_config.tx_power_dbm is None:
                await self._write_sethardware_error(MESHCORE_ERROR_INVALID_PARAM)
                return False
            await self._write_sethardware(MESHCORE_SUB_GET_TX_POWER | 0x80, bytes([int(self.radio_config.tx_power_dbm) & 0xFF]))
            return True
        if subcmd == MESHCORE_SUB_GET_CURRENT_RSSI:
            rssi = getattr(self.sx1302, "last_rssi_dbm", None)
            if rssi is None and hasattr(self.sx1302, "get_current_rssi"):
                rssi = self.sx1302.get_current_rssi()
            if rssi is None and hasattr(self.sx1302, "get_last_signal_rssi"):
                rssi = self.sx1302.get_last_signal_rssi()
            await self._write_sethardware(MESHCORE_SUB_GET_CURRENT_RSSI | 0x80, bytes([_clamp_i8(round(rssi if rssi is not None else -120)) & 0xFF]))
            return True
        if subcmd == MESHCORE_SUB_IS_CHANNEL_BUSY:
            busy = bool(self.sx1302.is_channel_busy()) if hasattr(self.sx1302, "is_channel_busy") else False
            await self._write_sethardware(MESHCORE_SUB_IS_CHANNEL_BUSY | 0x80, b"\x01" if busy else b"\x00")
            return True
        if subcmd == MESHCORE_SUB_GET_AIRTIME:
            if len(data) != 1:
                await self._write_sethardware_error(MESHCORE_ERROR_INVALID_LENGTH)
                return False
            airtime = None
            if hasattr(self.sx1302, "get_airtime"):
                airtime = self.sx1302.get_airtime(data[0])
            if airtime is None:
                airtime = self._estimate_lora_airtime_ms(data[0])
            await self._write_sethardware(MESHCORE_SUB_GET_AIRTIME | 0x80, int(airtime).to_bytes(4, "little"))
            return True
        if subcmd == MESHCORE_SUB_GET_NOISE_FLOOR:
            noise_floor = self.sx1302.get_noise_floor() if hasattr(self.sx1302, "get_noise_floor") else None
            await self._write_sethardware(MESHCORE_SUB_GET_NOISE_FLOOR | 0x80, int(round(noise_floor if noise_floor is not None else -120)).to_bytes(2, "little", signed=True))
            return True
        if subcmd == MESHCORE_SUB_GET_VERSION:
            await self._write_sethardware(MESHCORE_SUB_GET_VERSION | 0x80, b"\x01\x00")
            return True
        if subcmd == MESHCORE_SUB_GET_STATS:
            rx = self.counters.rx_good_count
            tx = self.counters.tx_done_count
            errors = self.counters.rx_bad_crc_count + self.counters.rx_dropped_count + self.counters.tx_error_count
            await self._write_sethardware(MESHCORE_SUB_GET_STATS | 0x80, struct.pack("<III", rx, tx, errors))
            return True
        if subcmd == MESHCORE_SUB_GET_BATTERY:
            await self._write_sethardware(MESHCORE_SUB_GET_BATTERY | 0x80, int(0).to_bytes(2, "little"))
            return True
        if subcmd == MESHCORE_SUB_GET_MCU_TEMP:
            await self._write_sethardware(MESHCORE_SUB_GET_MCU_TEMP | 0x80, int(0).to_bytes(2, "little", signed=True))
            return True
        if subcmd == MESHCORE_SUB_GET_SENSORS:
            await self._write_sethardware(MESHCORE_SUB_GET_SENSORS | 0x80)
            return True
        if subcmd == MESHCORE_SUB_GET_DEVICE_NAME:
            await self._write_sethardware(MESHCORE_SUB_GET_DEVICE_NAME | 0x80, b"sx1302-meshcore-kiss")
            return True
        if subcmd == MESHCORE_SUB_PING:
            await self._write_sethardware(MESHCORE_SUB_PING | 0x80)
            return True
        if subcmd == MESHCORE_SUB_REBOOT:
            await self._write_sethardware(MESHCORE_SUB_OK)
            return True
        if subcmd == MESHCORE_SUB_SET_SIGNAL_REPORT:
            if len(data) != 1:
                await self._write_sethardware_error(MESHCORE_ERROR_INVALID_LENGTH)
                return False
            self.emit_rxmeta = data[0] != 0
            await self._write_sethardware(MESHCORE_SUB_OK)
            return True
        if subcmd == MESHCORE_SUB_GET_SIGNAL_REPORT:
            await self._write_sethardware(MESHCORE_SUB_GET_SIGNAL_REPORT | 0x80, b"\x01" if self.emit_rxmeta else b"\x00")
            return True

        logger.info("Unsupported SetHardware subcommand: 0x%02X", subcmd)
        await self._write_sethardware_error(MESHCORE_ERROR_NO_CALLBACK if subcmd in self._CRYPTO_SUBCOMMANDS else MESHCORE_ERROR_UNKNOWN_CMD)
        return False

    def _estimate_lora_airtime_ms(self, payload_len: int) -> int:
        if not (0 <= payload_len <= 255):
            raise ValueError("payload length must be between 0 and 255 bytes")
        bw = int(self.radio_config.bandwidth_hz or 0)
        sf = int(self.radio_config.spreading_factor or 0)
        cr = int(self.radio_config.coding_rate or 0)
        if bw <= 0 or sf <= 0 or cr < 5 or cr > 8:
            raise ValueError("radio config is incomplete for airtime calculation")
        tsym_s = (2 ** sf) / float(bw)
        de = 1 if tsym_s >= 0.016 else 0
        denominator = 4 * (sf - 2 * de)
        numerator = 8 * payload_len - 4 * sf + 28 + 16
        payload_symbols = 8 + max(((numerator + denominator - 1) // denominator) * cr, 0)
        preamble = int(self.radio_config.preamble_len or 8)
        return int((preamble + 4.25 + payload_symbols) * tsym_s * 1000.0 + 0.999999)

    async def _handle_config_command(self, frame: KissFrame) -> bool:
        name = self._CONFIG_COMMAND_NAMES[frame.command]
        value: int | bool | None = frame.payload[0] if frame.payload else None
        if frame.command == KISS_CMD_FULLDUP and value is not None:
            value = bool(value)
        self.kiss_config[name] = value
        event = {
            "direction": "kiss",
            "status": "kiss_config",
            "command": frame.command,
            "command_name": name,
            "port": frame.port,
            "value": value,
        }
        self.ring.add(event)
        await self.mqtt.publish_event("kiss/config", event)
        return True

    def _may_forward_uncertain_crc(self, pkt: RxPacket) -> bool:
        if not self.forward_unknown_crc or not (1 <= len(pkt.payload) <= 255):
            return False
        # Permissive forwarding is only for the experimental SX1261 path where
        # MeshCore-like payload bytes may be available even when PHY CRC status
        # is not trustworthy. Normal SX1302 RX should never inject bad CRC data
        # into pyMC because it can corrupt short exchanges such as pings.
        return pkt.raw_metadata.get("rx_backend") == "sx1261"

    async def handle_rx_packet(self, pkt: RxPacket) -> bool:
        if pkt.crc_ok is True:
            if not (1 <= len(pkt.payload) <= 255):
                self.counters.rx_dropped_count += 1
                status = "dropped"
                forwarded = False
            else:
                self.counters.rx_good_count += 1
                self.counters.rx_crc_ok_count += 1
                status = "good"
                forwarded = True
        elif pkt.crc_ok is False:
            self.counters.rx_bad_crc_count += 1
            if self._may_forward_uncertain_crc(pkt):
                status="bad_crc"; forwarded=True
            else:
                self.counters.rx_dropped_count += 1; status="bad_crc"; forwarded=False
        else:
            self.counters.rx_unknown_crc_count += 1
            if self._may_forward_uncertain_crc(pkt):
                status="unknown_crc"; forwarded=True
            else:
                self.counters.rx_dropped_count += 1; status="unknown_crc"; forwarded=False
        event = build_rx_event(self.node_id, pkt, status=status, forwarded_to_pymc=forwarded)
        self.ring.add(event)
        await self.mqtt.publish_event(f"rx/{status}", event)
        if forwarded:
            await self.kiss.write_frame(CMD_DATA, pkt.payload)
            if self.emit_rxmeta and pkt.rssi_dbm is not None and pkt.snr_db is not None:
                await self.kiss.write_frame(KISS_CMD_SETHARDWARE, bytes([MESHCORE_SUB_RXMETA, _clamp_i8(round(pkt.snr_db*4)) & 0xff, _clamp_i8(round(pkt.rssi_dbm)) & 0xff]))
        return forwarded
