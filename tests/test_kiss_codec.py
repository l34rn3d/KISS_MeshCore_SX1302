import pytest

from sx1302_meshcore_kiss.kiss.codec import (
    FEND,
    FESC,
    TFEND,
    TFESC,
    CMD_DATA,
    CMD_RXMETA,
    CMD_TXDONE,
    KissCodec,
    KissDecodeError,
    encode_frame,
    encode_rxmeta,
    encode_txdone,
)


def test_encode_data_frame_escapes_fend_and_fesc():
    assert encode_frame(CMD_DATA, b"\x01\x02\x03") == bytes([FEND, CMD_DATA, 1, 2, 3, FEND])
    assert encode_frame(CMD_DATA, b"\x01" + bytes([FEND]) + b"\x02") == bytes(
        [FEND, CMD_DATA, 1, FESC, TFEND, 2, FEND]
    )
    assert encode_frame(CMD_DATA, b"\x01" + bytes([FESC]) + b"\x02") == bytes(
        [FEND, CMD_DATA, 1, FESC, TFESC, 2, FEND]
    )


def test_stream_decoder_decodes_multiple_frames_and_preserves_payload_bytes():
    codec = KissCodec()
    wire = encode_frame(CMD_DATA, b"hello") + encode_frame(CMD_DATA, bytes([FEND, FESC]))

    frames = codec.feed(wire)

    assert [(frame.command, frame.payload) for frame in frames] == [
        (CMD_DATA, b"hello"),
        (CMD_DATA, bytes([FEND, FESC])),
    ]


def test_stream_decoder_masks_standard_kiss_port_nibble_for_data_frames():
    codec = KissCodec()
    frame = codec.feed(bytes([FEND, 0x10, 0x01, 0x02, FEND]))[0]

    assert frame.command == CMD_DATA
    assert frame.port == 1
    assert frame.raw_command == 0x10
    assert frame.payload == b"\x01\x02"


def test_stream_decoder_preserves_meshcore_extension_command_bytes():
    codec = KissCodec()
    frame = codec.feed(bytes([FEND, CMD_RXMETA, 25, 0x9F, FEND]))[0]

    assert frame.command == CMD_RXMETA
    assert frame.port == 0
    assert frame.raw_command == CMD_RXMETA
    assert frame.payload == bytes([25, 0x9F])


def test_stream_decoder_reports_invalid_escape_and_drops_frame():
    codec = KissCodec()

    with pytest.raises(KissDecodeError):
        codec.feed(bytes([FEND, CMD_DATA, FESC, 0x00, FEND]))

    assert codec.decode_error_count == 1
    assert codec.feed(encode_frame(CMD_DATA, b"ok"))[0].payload == b"ok"


def test_data_payload_length_limit_is_enforced():
    encode_frame(CMD_DATA, b"x" * 255)
    with pytest.raises(ValueError):
        encode_frame(CMD_DATA, b"x" * 256)
    with pytest.raises(ValueError):
        encode_frame(CMD_DATA, b"")


def test_rxmeta_and_txdone_sethardware_frames():
    assert encode_rxmeta(rssi_dbm=-97.2, snr_db=6.25) == encode_frame(CMD_RXMETA, bytes([25, 0x9F]))
    assert encode_rxmeta(rssi_dbm=-200, snr_db=-40) == encode_frame(CMD_RXMETA, bytes([0x80, 0x80]))
    assert encode_txdone(ok=True) == encode_frame(CMD_TXDONE, b"\x01")
    assert encode_txdone(ok=False) == encode_frame(CMD_TXDONE, b"\x00")
