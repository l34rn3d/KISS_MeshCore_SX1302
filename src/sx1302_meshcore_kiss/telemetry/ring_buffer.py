from __future__ import annotations
from collections import deque
from threading import Lock
from typing import Any

class PacketRingBuffer:
    def __init__(self, maxlen: int = 50) -> None:
        self.maxlen = int(maxlen)
        self._items = deque(maxlen=self.maxlen)
        self._lock = Lock()
    def add(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._items.append(dict(event))
        return event
    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._items]
