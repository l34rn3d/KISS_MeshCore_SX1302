from __future__ import annotations
import base64, uuid
from datetime import datetime, timezone
from typing import Any
from sx1302_meshcore_kiss.sx1302.metadata import RxPacket, TxPacket, TxResult


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def payload_encodings(payload: bytes) -> dict[str, Any]:
    payload = bytes(payload)
    return {"payload_len": len(payload), "payload_hex": payload.hex(), "payload_b64": base64.b64encode(payload).decode("ascii")}

def _event_base(schema: str, node_id: str, direction: str, status: str, payload: bytes) -> dict[str, Any]:
    return {"schema": schema, "node_id": node_id, "event_id": str(uuid.uuid4()), "timestamp": now_iso(), "direction": direction, "status": status, **payload_encodings(payload)}

def build_rx_event(node_id: str, pkt: RxPacket, *, status: str, forwarded_to_pymc: bool) -> dict[str, Any]:
    event = _event_base(f"sx1302_meshcore_kiss.rx.{status}.v1", node_id, "rx", status, pkt.payload)
    event.update({
        "forwarded_to_pymc": bool(forwarded_to_pymc),
        "dropped": not bool(forwarded_to_pymc),
        "radio": {"frequency_hz": pkt.frequency_hz, "bandwidth_hz": pkt.bandwidth_hz, "spreading_factor": pkt.spreading_factor, "coding_rate": pkt.coding_rate, "rssi_dbm": pkt.rssi_dbm, "snr_db": pkt.snr_db, "channel": pkt.channel, "concentrator_timestamp_us": pkt.concentrator_timestamp_us},
        "crc": {"crc_ok": pkt.crc_ok},
        "raw_metadata": dict(pkt.raw_metadata or {}),
    })
    return event

def build_tx_requested_event(node_id: str, pkt: TxPacket) -> dict[str, Any]:
    event = _event_base("sx1302_meshcore_kiss.tx.requested.v1", node_id, "tx", "queued", pkt.payload)
    event.update({"source":"pymc_kiss", "radio":{"frequency_hz": pkt.frequency_hz, "bandwidth_hz": pkt.bandwidth_hz, "spreading_factor": pkt.spreading_factor, "coding_rate": pkt.coding_rate, "tx_power_dbm": pkt.tx_power_dbm}})
    return event

def build_tx_result_event(node_id: str, pkt: TxPacket, result: TxResult) -> dict[str, Any]:
    status = "done" if result.ok else "error"
    event = _event_base(f"sx1302_meshcore_kiss.tx.{status}.v1", node_id, "tx", status, pkt.payload)
    event.update({"ok": result.ok, "error": result.error, "airtime_ms": result.airtime_ms, "started_at": result.started_at, "completed_at": result.completed_at, "raw_metadata": dict(result.raw_metadata or {})})
    return event
