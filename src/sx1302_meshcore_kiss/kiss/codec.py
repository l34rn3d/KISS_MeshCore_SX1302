from __future__ import annotations

from dataclasses import dataclass
from typing import List

FEND = 0xC0
FESC = 0xDB
TFEND = 0xDC
TFESC = 0xDD
CMD_DATA = 0x00
KISS_CMD_TXDELAY = 0x01
KISS_CMD_PERSIST = 0x02
KISS_CMD_SLOTTIME = 0x03
KISS_CMD_TXTAIL = 0x04
KISS_CMD_FULLDUP = 0x05
KISS_CMD_SETHARDWARE = 0x06
KISS_CMD_RETURN = 0xFF

MESHCORE_SUB_GET_IDENTITY = 0x01
MESHCORE_SUB_GET_RANDOM = 0x02
MESHCORE_SUB_VERIFY_SIGNATURE = 0x03
MESHCORE_SUB_SIGN_DATA = 0x04
MESHCORE_SUB_ENCRYPT_DATA = 0x05
MESHCORE_SUB_DECRYPT_DATA = 0x06
MESHCORE_SUB_KEY_EXCHANGE = 0x07
MESHCORE_SUB_HASH = 0x08
MESHCORE_SUB_SET_RADIO = 0x09
MESHCORE_SUB_SET_TX_POWER = 0x0A
MESHCORE_SUB_GET_RADIO = 0x0B
MESHCORE_SUB_GET_TX_POWER = 0x0C
MESHCORE_SUB_GET_CURRENT_RSSI = 0x0D
MESHCORE_SUB_IS_CHANNEL_BUSY = 0x0E
MESHCORE_SUB_GET_AIRTIME = 0x0F
MESHCORE_SUB_GET_NOISE_FLOOR = 0x10
MESHCORE_SUB_GET_VERSION = 0x11
MESHCORE_SUB_GET_STATS = 0x12
MESHCORE_SUB_SET_SIGNAL_REPORT = 0x19
MESHCORE_SUB_GET_SIGNAL_REPORT = 0x1A
MESHCORE_SUB_OK = 0xF0
MESHCORE_SUB_ERROR = 0xF1
MESHCORE_SUB_TXDONE = 0xF8
MESHCORE_SUB_RXMETA = 0xF9

# Backwards-compatible aliases for callers that imported the old names. These
# are MeshCore SetHardware sub-command values, not top-level KISS commands.
CMD_TXDONE = MESHCORE_SUB_TXDONE
CMD_RXMETA = MESHCORE_SUB_RXMETA

MESHCORE_ERROR_INVALID_LENGTH = 0x01
MESHCORE_ERROR_INVALID_PARAM = 0x02
MESHCORE_ERROR_NO_CALLBACK = 0x03
MESHCORE_ERROR_UNKNOWN_CMD = 0x05

MAX_MESHCORE_PAYLOAD = 255
_STANDARD_KISS_COMMANDS = {
    CMD_DATA,
    KISS_CMD_TXDELAY,
    KISS_CMD_PERSIST,
    KISS_CMD_SLOTTIME,
    KISS_CMD_TXTAIL,
    KISS_CMD_FULLDUP,
    KISS_CMD_SETHARDWARE,
}


@dataclass(frozen=True)
class KissFrame:
    command: int
    payload: bytes
    port: int = 0
    raw_command: int | None = None

    def __post_init__(self) -> None:
        if self.raw_command is None:
            object.__setattr__(self, "raw_command", self.command & 0xFF)


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
    return encode_frame(KISS_CMD_SETHARDWARE, bytes([MESHCORE_SUB_RXMETA, snr_i8_x4, rssi_i8]))


def encode_txdone(*, ok: bool = True) -> bytes:
    return encode_frame(KISS_CMD_SETHARDWARE, bytes([MESHCORE_SUB_TXDONE, 0x01 if ok else 0x00]))


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
                    raw_command = self._buf[0]
                    command, port = self._normalize_command(raw_command)
                    payload = bytes(self._buf[1:])
                    frames.append(KissFrame(command, payload, port=port, raw_command=raw_command))
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

    @staticmethod
    def _normalize_command(raw_command: int) -> tuple[int, int]:
        if raw_command == KISS_CMD_RETURN:
            return raw_command, 0
        low_command = raw_command & 0x0F
        if low_command in _STANDARD_KISS_COMMANDS:
            return low_command, (raw_command & 0xF0) >> 4
        return raw_command, 0

    def _drop_bad_frame(self, message: str) -> None:
        self.decode_error_count += 1
        self._buf.clear()
        self._in_frame = False
        self._escaped = False
        raise KissDecodeError(message)
