from __future__ import annotations

from dataclasses import dataclass
from typing import List

FEND = 0xC0
FESC = 0xDB
TFEND = 0xDC
TFESC = 0xDD
CMD_DATA = 0x00
CMD_TXDONE = 0xF8
CMD_RXMETA = 0xF9
MAX_MESHCORE_PAYLOAD = 255


@dataclass(frozen=True)
class KissFrame:
    command: int
    payload: bytes


class KissDecodeError(ValueError):
    pass


def _validate_payload(command: int, payload: bytes) -> None:
    if command == CMD_DATA and not (1 <= len(payload) <= MAX_MESHCORE_PAYLOAD):
        raise ValueError("KISS Data payload length must be 1..255 bytes")


def escape_payload(payload: bytes) -> bytes:
    out = bytearray()
    for b in payload:
        if b == FEND:
            out.extend([FESC, TFEND])
        elif b == FESC:
            out.extend([FESC, TFESC])
        else:
            out.append(b)
    return bytes(out)


def encode_frame(command: int, payload: bytes = b"") -> bytes:
    command &= 0xFF
    payload = bytes(payload)
    _validate_payload(command, payload)
    return bytes([FEND, command]) + escape_payload(payload) + bytes([FEND])


def _clamp_i8(value: int) -> int:
    return max(-128, min(127, int(value)))


def encode_rxmeta(*, rssi_dbm: float, snr_db: float) -> bytes:
    snr_i8_x4 = _clamp_i8(round(float(snr_db) * 4.0)) & 0xFF
    rssi_i8 = _clamp_i8(round(float(rssi_dbm))) & 0xFF
    return encode_frame(CMD_RXMETA, bytes([snr_i8_x4, rssi_i8]))


def encode_txdone(*, ok: bool = True) -> bytes:
    return encode_frame(CMD_TXDONE, b"\x01" if ok else b"\x00")


class KissCodec:
    def __init__(self) -> None:
        self._buf = bytearray()
        self._in_frame = False
        self._escaped = False
        self.decode_error_count = 0

    def feed(self, data: bytes) -> List[KissFrame]:
        frames: List[KissFrame] = []
        for b in data:
            if b == FEND:
                if self._in_frame and self._buf:
                    command = self._buf[0]
                    payload = bytes(self._buf[1:])
                    frames.append(KissFrame(command, payload))
                self._buf.clear()
                self._in_frame = True
                self._escaped = False
                continue
            if not self._in_frame:
                continue
            if self._escaped:
                if b == TFEND:
                    self._buf.append(FEND)
                elif b == TFESC:
                    self._buf.append(FESC)
                else:
                    self._drop_bad_frame(f"invalid KISS escape byte 0x{b:02x}")
                self._escaped = False
                continue
            if b == FESC:
                self._escaped = True
            else:
                self._buf.append(b)
        return frames

    def _drop_bad_frame(self, message: str) -> None:
        self.decode_error_count += 1
        self._buf.clear()
        self._in_frame = False
        self._escaped = False
        raise KissDecodeError(message)
