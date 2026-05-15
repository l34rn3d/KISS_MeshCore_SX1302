from __future__ import annotations
import asyncio, os, pty
from pathlib import Path
from sx1302_meshcore_kiss.kiss.codec import KissCodec, encode_frame

class PtyEndpoint:
    def __init__(self, *, symlink: str = "/tmp/sx1302-kiss") -> None:
        self.symlink=symlink; self.master_fd=None; self.slave_name=None; self.codec=KissCodec()
    async def start(self) -> None:
        self.master_fd, slave_fd = pty.openpty(); self.slave_name=os.ttyname(slave_fd); os.close(slave_fd)
        path=Path(self.symlink)
        if path.exists() or path.is_symlink(): path.unlink()
        path.symlink_to(self.slave_name)
    async def stop(self) -> None:
        if self.master_fd is not None: os.close(self.master_fd); self.master_fd=None
        p=Path(self.symlink)
        if p.exists() or p.is_symlink(): p.unlink()
    async def read_bytes(self, max_bytes: int = 4096) -> bytes:
        if self.master_fd is None: raise RuntimeError("PTY not started")
        return await asyncio.to_thread(os.read, self.master_fd, max_bytes)
    async def write_frame(self, command: int, payload: bytes) -> None:
        if self.master_fd is None: raise RuntimeError("PTY not started")
        await asyncio.to_thread(os.write, self.master_fd, encode_frame(command, payload))
