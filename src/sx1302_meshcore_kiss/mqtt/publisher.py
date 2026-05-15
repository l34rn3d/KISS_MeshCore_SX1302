from __future__ import annotations
import asyncio, json, logging
from typing import Any, Optional

log = logging.getLogger(__name__)

class MqttPublisher:
    def __init__(self, *, enabled: bool = True, host: str = "127.0.0.1", port: int = 1883, username: str = "", password: str = "", client_id: str = "sx1302-meshcore-kiss", base_topic: str = "meshcore/sx1302/repeater-01", qos_events: int = 0, qos_status: int = 1, retain_status: bool = True, retain_packet_events: bool = False) -> None:
        self.enabled=enabled; self.host=host; self.port=port; self.username=username; self.password=password; self.client_id=client_id; self.base_topic=base_topic.rstrip('/'); self.qos_events=qos_events; self.qos_status=qos_status; self.retain_status=retain_status; self.retain_packet_events=retain_packet_events; self.client: Optional[Any]=None; self.connected=False
    async def start(self) -> None:
        if not self.enabled: return
        import paho.mqtt.client as mqtt
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        if self.username or self.password: self.client.username_pw_set(self.username, self.password)
        self.client.connect_async(self.host, self.port, keepalive=60); self.client.loop_start(); self.connected=True
    async def stop(self) -> None:
        if self.client is not None:
            self.client.loop_stop(); self.client.disconnect(); self.connected=False
    async def publish_event(self, topic_suffix: str, payload: dict[str, Any], *, retain: bool = False, qos: Optional[int] = None) -> None:
        if not self.enabled or self.client is None: return
        topic = f"{self.base_topic}/{topic_suffix.strip('/')}"
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        await asyncio.to_thread(self.client.publish, topic, body, qos=self.qos_events if qos is None else qos, retain=retain)
