from __future__ import annotations
import asyncio
from sx1302_meshcore_kiss.kiss.codec import KissCodec, encode_frame

class SerialEndpoint:
    def __init__(self, *, port: str, baud_rate: int = 115200, timeout: float = 0.1) -> None:
        self.port=port; self.baud_rate=baud_rate; self.timeout=timeout; self.serial=None; self.codec=KissCodec()
    async def start(self) -> None:
        import serial
        self.serial = serial.Serial(self.port, self.baud_rate, timeout=self.timeout)
    async def stop(self) -> None:
        if self.serial is not None: self.serial.close(); self.serial=None
    async def read_bytes(self, max_bytes: int = 4096) -> bytes:
        if self.serial is None: raise RuntimeError("serial not started")
        return await asyncio.to_thread(self.serial.read, max_bytes)
    async def write_frame(self, command: int, payload: bytes) -> None:
        if self.serial is None: raise RuntimeError("serial not started")
        await asyncio.to_thread(self.serial.write, encode_frame(command, payload))
