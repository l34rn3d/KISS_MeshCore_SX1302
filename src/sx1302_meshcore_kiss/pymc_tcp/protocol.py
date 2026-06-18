from __future__ import annotations

import struct

PROTO_SYNC = 0xAA
MAX_LORA_PAYLOAD = 255
MAX_FRAME_PAYLOAD = 0xFFFF

CMD_TX_REQUEST = 0x01
CMD_SET_CONFIG = 0x10
CMD_GET_CONFIG = 0x11
CMD_STATUS_REQ = 0x20
CMD_NOISE_REQ = 0x22
CMD_CAD_REQUEST = 0x30
CMD_RX_START = 0x31
CMD_SET_CAD_PARAMS = 0x34
CMD_SET_WIFI = 0x41
CMD_AUTH = 0x50
CMD_WIFI_RESET = 0x60
CMD_GET_WIFI = 0x61
CMD_GET_VERSION = 0x70
CMD_PING = 0xFF

CMD_TX_DONE = 0x02
CMD_TX_FAIL = 0x03
CMD_RX_PACKET = 0x04
CMD_CONFIG_RESP = 0x12
CMD_STATUS_RESP = 0x21
CMD_NOISE_RESP = 0x23
CMD_CAD_RESP = 0x32
CMD_RX_STARTED = 0x33
CMD_CAD_PARAMS_RESP = 0x35
CMD_AUTH_OK = 0x51
CMD_WIFI_STATUS = 0x62
CMD_VERSION_RESP = 0x71
CMD_ERROR = 0xFE
CMD_PONG = 0xFF

ERR_CRC_MISMATCH = 0x01
ERR_INVALID_CMD = 0x02
ERR_RADIO_BUSY = 0x03
ERR_TX_TIMEOUT = 0x04
ERR_PAYLOAD_TOO_BIG = 0x05
ERR_INVALID_CONFIG = 0x06
ERR_CAD_FAILED = 0x07
ERR_RADIO_INIT = 0x08
ERR_UNAUTHORIZED = 0x09

WIFI_MODE_OFFLINE = 0
WIFI_MODE_STA_CONNECTING = 1
WIFI_MODE_STA_CONNECTED = 2
WIFI_MODE_AP_CONFIG = 3

RADIO_CONFIG_FMT = "<IIBBbHB"
RADIO_CONFIG_SIZE = struct.calcsize(RADIO_CONFIG_FMT)
STATUS_RESP_FMT = "<IIIIhhhbB"
STATUS_RESP_SIZE = struct.calcsize(STATUS_RESP_FMT)


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


def build_frame(command: int, payload: bytes = b"") -> bytes:
    if len(payload) > 0xFFFF:
        raise ValueError("payload exceeds pyMC TCP frame length")
    header = struct.pack("<BH", command & 0xFF, len(payload))
    crc = crc16_ccitt(header + payload)
    return bytes([PROTO_SYNC]) + header + payload + struct.pack("<H", crc)


def parse_frame(buffer: bytearray) -> tuple[int, bytes] | None:
    sync_index = buffer.find(bytes([PROTO_SYNC]))
    if sync_index < 0:
        buffer.clear()
        return None
    if sync_index > 0:
        del buffer[:sync_index]
    if len(buffer) < 6:
        return None
    command = buffer[1]
    length = buffer[2] | (buffer[3] << 8)
    if length > MAX_FRAME_PAYLOAD:
        del buffer[0]
        return None
    frame_size = 1 + 1 + 2 + length + 2
    if len(buffer) < frame_size:
        return None
    header = bytes(buffer[1:4])
    payload = bytes(buffer[4:4 + length])
    received_crc = buffer[4 + length] | (buffer[5 + length] << 8)
    del buffer[:frame_size]
    if received_crc != crc16_ccitt(header + payload):
        return CMD_ERROR, bytes([ERR_CRC_MISMATCH])
    return command, payload
