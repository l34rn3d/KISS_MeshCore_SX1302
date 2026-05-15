from __future__ import annotations
from typing import Any, Optional
from sx1302_meshcore_kiss.kiss.codec import CMD_DATA, CMD_RXMETA, CMD_TXDONE, KissFrame
from sx1302_meshcore_kiss.mqtt.schemas import build_rx_event, build_tx_requested_event, build_tx_result_event
from sx1302_meshcore_kiss.sx1302.metadata import RadioConfig, RxPacket, TxPacket
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer

class ModemService:
    def __init__(self, *, node_id: str, radio_config: RadioConfig, sx1302: Any, kiss: Any, mqtt: Any, ring: PacketRingBuffer, counters: Counters, forward_unknown_crc: bool = False, emit_rxmeta: bool = True, emit_txdone: bool = True) -> None:
        self.node_id=node_id; self.radio_config=radio_config; self.sx1302=sx1302; self.kiss=kiss; self.mqtt=mqtt; self.ring=ring; self.counters=counters; self.forward_unknown_crc=forward_unknown_crc; self.emit_rxmeta=emit_rxmeta; self.emit_txdone=emit_txdone

    def _tx_packet(self, payload: bytes) -> TxPacket:
        return TxPacket(payload=bytes(payload), frequency_hz=self.radio_config.frequency_hz, bandwidth_hz=self.radio_config.bandwidth_hz, spreading_factor=self.radio_config.spreading_factor, coding_rate=self.radio_config.coding_rate, tx_power_dbm=self.radio_config.tx_power_dbm, preamble_len=self.radio_config.preamble_len, sync_word=self.radio_config.sync_word, invert_iq=self.radio_config.invert_iq)

    async def handle_kiss_frame(self, frame: KissFrame) -> bool:
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
            await self.kiss.write_frame(CMD_TXDONE, b"\x01" if result.ok else b"\x00")
        return result.ok

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
                from sx1302_meshcore_kiss.kiss.codec import _clamp_i8
                await self.kiss.write_frame(CMD_RXMETA, bytes([_clamp_i8(round(pkt.snr_db*4)) & 0xff, _clamp_i8(round(pkt.rssi_dbm)) & 0xff]))
        return forwarded
