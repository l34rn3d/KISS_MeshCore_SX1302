# sx1302-meshcore-kiss

MeshCore-compatible KISS modem daemon for using an SX1302/WM1302 concentrator through the Python `pyMC_core` SX1302 library.

```text
pyMC_Repeater / pyMC_core
        ⇅
KISS serial / PTY interface
        ⇅
sx1302-meshcore-kiss daemon
        ⇅
pyMC_core SX1302Radio adapter
        ⇅
SX1302 / WM1302 concentrator hardware
```

## Status

Initial implementation/prototype. The pure daemon behavior is covered by tests; real WM1302/SX1302 RF validation still needs to be done on hardware.

## Implemented

- Standard KISS framing with `FEND/FESC` escaping.
- MeshCore KISS `Data 0x00` frames for TX and RX.
- Standard KISS port-nibble decoding, so `0x10` is handled as Data on port 1.
- Standard KISS config commands `TXDELAY`, `PERSIST`, `SLOTTIME`, `TXTAIL`, `FULLDUP`, and `RETURN` are accepted/recorded without breaking modem operation.
- 1..255 byte MeshCore payload length enforcement.
- No KISS-level CRC/FCS.
- Payload bytes are passed through unchanged.
- `RxMeta 0xF9` emission after valid RX frames when RSSI/SNR exist.
- `TxDone 0xF8` emission after TX attempts.
- PTY endpoint with default symlink `/tmp/sx1302-kiss`.
- Serial endpoint for real tty devices.
- SX1302 adapter around `pymc_core.hardware.sx1302_wrapper.SX1302Radio`.
- CRC policy:
  - `crc_ok=True`: forward to pyMC, MQTT `rx/good`, dashboard event.
  - `crc_ok=False`: drop from pyMC, MQTT `rx/bad_crc`, dashboard event.
  - `crc_ok=None`: drop by default, optional lab-only forwarding.
- MQTT JSON telemetry schemas with payload hex and base64.
- Local-only HTTP dashboard APIs:
  - `/api/status`
  - `/api/counters`
  - `/api/packets`
  - `/api/config`
- In-memory packet ring buffer only, default max 50 events.
- Config redaction for secrets.
- Basic systemd unit example.

## Install / run from source

```bash
cd /home/chris/mesh2/sx1302-meshcore-kiss
PYTHONPATH=src:/home/chris/mesh2/pyMC_core/src python -m sx1302_meshcore_kiss.main --config config.example.yaml
```

For pyMC_Repeater on the same host, point its KISS radio config at the PTY symlink:

```yaml
radio_type: kiss
kiss:
  port: "/tmp/sx1302-kiss"
  baud_rate: 115200
```

The daemon owns SX1302 radio configuration. Match daemon-side radio settings to the mesh.

## Safety notes

- Dashboard binds to `127.0.0.1:8080` by default.
- Raw payload logging is disabled by default.
- Packet events are kept in RAM only.
- Raw packet events are not retained in MQTT by default.
- MQTT password is redacted from `/api/config`.
- Bad RF CRC packets are not forwarded to pyMC.

## Test

```bash
cd /home/chris/mesh2/sx1302-meshcore-kiss
PYTHONPATH=src:/home/chris/mesh2/pyMC_core/src pytest -q
```

Current expected result:

```text
22 passed
```

## Development roadmap

Next practical hardening steps:

1. Real WM1302/SX1302 smoke test with the daemon in PTY mode.
2. Verify pyMC_Repeater opens `/tmp/sx1302-kiss` and can exchange KISS frames.
3. Add end-to-end integration test with a fake pyMC KISS client and fake SX1302 adapter.
4. Add optional TCP KISS endpoint if needed.
5. Add richer dashboard HTML once hardware behavior is proven.
