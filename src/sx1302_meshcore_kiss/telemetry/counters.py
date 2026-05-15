from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

@dataclass
class Counters:
    rx_good_count: int = 0
    rx_crc_ok_count: int = 0
    rx_bad_crc_count: int = 0
    rx_unknown_crc_count: int = 0
    rx_dropped_count: int = 0
    tx_requested_count: int = 0
    tx_done_count: int = 0
    tx_error_count: int = 0
    kiss_decode_error_count: int = 0
    kiss_unknown_command_count: int = 0
    mqtt_publish_error_count: int = 0
    def snapshot(self, *, node_id: str, uptime_seconds: int) -> dict:
        return {"schema":"sx1302_meshcore_kiss.counters.v1","node_id":node_id,"timestamp":_now_iso(),"uptime_seconds":int(uptime_seconds),"counters":asdict(self)}
