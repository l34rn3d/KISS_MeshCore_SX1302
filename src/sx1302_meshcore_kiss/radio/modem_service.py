from __future__ import annotations
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
    MESHCORE_SUB_GET_CURRENT_RSSI,
    MESHCORE_SUB_GET_NOISE_FLOOR,
    MESHCORE_SUB_GET_RADIO,
    MESHCORE_SUB_GET_SIGNAL_REPORT,
    MESHCORE_SUB_GET_STATS,
    MESHCORE_SUB_GET_TX_POWER,
    MESHCORE_SUB_GET_VERSION,
    MESHCORE_SUB_IS_CHANNEL_BUSY,
    MESHCORE_SUB_OK,
    MESHCORE_SUB_PING,
    MESHCORE_SUB_PONG,
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

class ModemService:
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

    def _tx_packet(self, payload: bytes) -> TxPacket:
        return TxPacket(payload=bytes(payload), frequency_hz=self.radio_config.frequency_hz, bandwidth_hz=self.radio_config.bandwidth_hz, spreading_factor=self.radio_config.spreading_factor, coding_rate=self.radio_config.coding_rate, tx_power_dbm=self.radio_config.tx_power_dbm, preamble_len=self.radio_config.preamble_len, sync_word=self.radio_config.sync_word, invert_iq=self.radio_config.invert_iq)

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
            await self.mqtt.publish_event("tx/error", {"status":"invalid_payload_length", "payload_len": len(frame.payload)})
            self.ring.add({"direction":"tx", "status":"invalid_payload_length", "payload_len": len(frame.payload)})
            return False
        pkt = self._tx_packet(frame.payload)
        self.counters.tx_requested_count += 1
        requested = build_tx_requested_event(self.node_id, pkt)
        self.ring.add({**requested, "status":"tx_requested"})
        await self.mqtt.publish_event("tx/requested", requested)
        result = await self.sx1302.transmit(pkt)
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
        if subcmd == MESHCORE_SUB_SET_RADIO:
            if len(data) != 10:
                await self._write_sethardware_error(MESHCORE_ERROR_INVALID_LENGTH)
                return False
            freq, bw, sf, cr = struct.unpack("<IIBB", data)
            if sf < 5 or sf > 12 or cr < 5 or cr > 8 or bw <= 0 or freq <= 0:
                await self._write_sethardware_error(MESHCORE_ERROR_INVALID_PARAM)
                return False
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
            if hasattr(self.sx1302, "configure"):
                await self.sx1302.configure(self.radio_config)
            await self._write_sethardware(MESHCORE_SUB_OK)
            return True
        if subcmd == MESHCORE_SUB_GET_RADIO:
            await self._write_sethardware(
                MESHCORE_SUB_GET_RADIO | 0x80,
                struct.pack("<IIBB", self.radio_config.frequency_hz, self.radio_config.bandwidth_hz, self.radio_config.spreading_factor, self.radio_config.coding_rate),
            )
            return True
        if subcmd == MESHCORE_SUB_GET_TX_POWER:
            await self._write_sethardware(MESHCORE_SUB_GET_TX_POWER | 0x80, bytes([self.radio_config.tx_power_dbm & 0xFF]))
            return True
        if subcmd == MESHCORE_SUB_GET_CURRENT_RSSI:
            rssi = getattr(self.sx1302, "last_rssi_dbm", None)
            if rssi is None and hasattr(self.sx1302, "get_last_signal_rssi"):
                maybe = self.sx1302.get_last_signal_rssi()
                rssi = maybe if maybe is not None else -120
            await self._write_sethardware(MESHCORE_SUB_GET_CURRENT_RSSI | 0x80, bytes([_clamp_i8(round(rssi if rssi is not None else -120)) & 0xFF]))
            return True
        if subcmd == MESHCORE_SUB_IS_CHANNEL_BUSY:
            await self._write_sethardware(MESHCORE_SUB_IS_CHANNEL_BUSY | 0x80, b"\x00")
            return True
        if subcmd == MESHCORE_SUB_GET_AIRTIME:
            if len(data) != 1:
                await self._write_sethardware_error(MESHCORE_ERROR_INVALID_LENGTH)
                return False
            # Conservative placeholder: upper layers still own actual MeshCore retry behavior.
            await self._write_sethardware(MESHCORE_SUB_GET_AIRTIME | 0x80, int(0).to_bytes(4, "little"))
            return True
        if subcmd == MESHCORE_SUB_GET_NOISE_FLOOR:
            await self._write_sethardware(MESHCORE_SUB_GET_NOISE_FLOOR | 0x80, int(-120).to_bytes(2, "little", signed=True))
            return True
        if subcmd == MESHCORE_SUB_GET_VERSION:
            await self._write_sethardware(MESHCORE_SUB_GET_VERSION | 0x80, b"\x01\x00")
            return True
        if subcmd == MESHCORE_SUB_GET_STATS:
            rx = self.counters.rx_good_count
            tx = self.counters.tx_done_count
            errors = self.counters.rx_dropped_count + self.counters.tx_error_count
            await self._write_sethardware(MESHCORE_SUB_GET_STATS | 0x80, struct.pack("<III", rx, tx, errors))
            return True
        if subcmd == MESHCORE_SUB_PING:
            await self._write_sethardware(MESHCORE_SUB_PONG)
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

        no_callback = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18}
        await self._write_sethardware_error(MESHCORE_ERROR_NO_CALLBACK if subcmd in no_callback else MESHCORE_ERROR_UNKNOWN_CMD)
        return False

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
            self.counters.rx_bad_crc_count += 1; self.counters.rx_dropped_count += 1; status="bad_crc"; forwarded=False
        else:
            self.counters.rx_unknown_crc_count += 1
            if self.forward_unknown_crc and 1 <= len(pkt.payload) <= 255:
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
