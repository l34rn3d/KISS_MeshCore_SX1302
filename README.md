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
- MeshCore `SetHardware 0x06` extension frames for radio config/status requests.
- `RxMeta 0xF9` and `TxDone 0xF8` are emitted as MeshCore SetHardware events:
  - `0x06 0xF9 <snr_i8_x4> <rssi_i8>`
  - `0x06 0xF8 <result>`
- Supported SetHardware requests:
  - `SetRadio 0x09`, `SetTxPower 0x0A`
  - `GetRadio 0x0B`, `GetTxPower 0x0C`
  - `GetCurrentRssi 0x0D`, `IsChannelBusy 0x0E`
  - `GetAirtime 0x0F`, `GetNoiseFloor 0x10`
  - `GetVersion 0x11`, `GetStats 0x12`
  - `SetSignalReport 0x19`, `GetSignalReport 0x1A`
- Unsupported crypto/device/sensor SetHardware requests return MeshCore error responses instead of being treated as raw KISS commands.
- PTY endpoint with default symlink `/tmp/sx1302-kiss`.
- Serial endpoint for real tty devices.
- SX1302 adapter around `pymc_core.hardware.sx1302_wrapper.SX1302Radio`.
- CRC policy inside the KISS daemon/service boundary:
  - `crc_ok=True`: forward to pyMC, MQTT `rx/good`, dashboard event.
  - `crc_ok=False`: drop from pyMC, MQTT `rx/bad_crc`, dashboard event.
  - `crc_ok=None`: drop by default, optional lab-only forwarding.
- MQTT JSON telemetry schemas with payload hex and base64.
- Local-only HTTP dashboard with browser UI and APIs:
  - `/` live dashboard page
  - `/api/status`
  - `/api/counters`
  - `/api/packets`
  - `/api/config`
- In-memory packet ring buffer only, default max 50 events.
- Config redaction for secrets.
- Basic systemd unit example.

## Install / run from source

Full install/service notes are in [`docs/install.md`](docs/install.md). Quick source run:

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
33 passed
```

## Development roadmap

Next practical hardening steps:

1. Real WM1302/SX1302 smoke test with the daemon in PTY mode.
2. Verify pyMC_Repeater opens `/tmp/sx1302-kiss` and can exchange KISS frames.
3. Add end-to-end integration test with a fake pyMC KISS client and fake SX1302 adapter.
4. Add optional TCP KISS endpoint if needed.
5. Improve periodic MQTT/status publishing and real connection tracking.
