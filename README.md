# sx1302-meshcore-kiss

MeshCore-compatible KISS modem daemon for SX1302/WM1302 concentrators.

This project is **not a pyMC radio implementation** and pyMC does not drive the hardware directly. It is a standalone modem bridge: pyMC/pyMC_Repeater talks normal KISS over a PTY or serial device, while this daemon owns the SX1302/WM1302 hardware using a daemon-local driver layer that follows Semtech `libloragw` / `lgw_*` HAL semantics for board, RF chain, IF chain, RX, TX, status, and airtime operations.

```text
pyMC_Repeater / pyMC_core
        ⇅
KISS serial / PTY interface, usually /tmp/sx1302-kiss
        ⇅
sx1302-meshcore-kiss daemon
        ⇅
Semtech-style SX1302/WM1302 driver layer
        ⇅
Linux SPI + GPIO reset/power control
        ⇅
SX1302 / SX1303 concentrator + SX1250 radios, e.g. WM1302
```

## What this is for

Use this when you want existing MeshCore/pyMC software to treat an SX1302/WM1302 concentrator like a KISS modem without importing or modifying pyMC hardware wrappers.

The daemon is responsible for:

- creating the KISS PTY or opening the configured serial endpoint;
- accepting MeshCore KISS data and SetHardware control frames;
- configuring the SX1302/WM1302 radio from daemon config and host SetHardware requests;
- resetting and starting the concentrator using board-specific SPI/GPIO settings;
- transmitting and receiving LoRa packets through the SX1302 driver layer;
- returning KISS data, `TxDone`, metadata, counters, MQTT telemetry, and dashboard state.

pyMC/pyMC_Repeater should be configured only as a KISS client.

## Status

Prototype/alpha. The daemon, KISS protocol handling, config path, dashboard APIs, and adapter behavior are unit-tested. Hardware deployment still requires board-specific validation of SPI, GPIO reset pins, RF settings, legal TX configuration, and pyMC KISS integration.

Current expected test result on a development machine:

```text
40 passed
```

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
- Standalone SX1302 adapter around daemon-local `sx1302_meshcore_kiss.sx1302.radio.SX1302Radio`; vanilla pyMC is only a KISS peer.
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

## Install / deploy

Full install/service notes are in [`docs/install.md`](docs/install.md). The short path is:

```bash
git clone --branch semtech-driver --single-branch https://github.com/l34rn3d/KISS_MeshCore_SX1302.git sx1302-meshcore-kiss
cd sx1302-meshcore-kiss
uv venv .venv
. .venv/bin/activate
uv pip install -e '.[dev]'
```

Create config:

```bash
sudo mkdir -p /etc/sx1302-meshcore-kiss
sudo cp config.example.yaml /etc/sx1302-meshcore-kiss/config.yaml
sudo editor /etc/sx1302-meshcore-kiss/config.yaml
```

Set only the board-specific hardware values in `radio:`. Frequency, bandwidth, spreading factor, coding rate, and TX power do not need to be pre-set for normal pyMC use because pyMC sends them at runtime with MeshCore `SetRadio` / `SetTxPower` KISS radio commands.

```yaml
radio:
  spi_device: "/dev/spidev0.0"
  reset_enabled: true
  gpio_chip: "gpiochip0"
  power_enable_pin: 18
  sx1302_reset_pin: 17
  sx1261_reset_pin: 5
  adc_reset_pin: 13
```

Install and start the service:

```bash
sudo useradd --system --home /opt/sx1302-meshcore-kiss --shell /usr/sbin/nologin sx1302kiss || true
sudo usermod -aG spi,gpio sx1302kiss
sudo mkdir -p /opt
sudo cp -a . /opt/sx1302-meshcore-kiss
sudo chown -R sx1302kiss:sx1302kiss /opt/sx1302-meshcore-kiss
sudo cp packaging/sx1302-meshcore-kiss.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sx1302-meshcore-kiss.service
sudo systemctl status sx1302-meshcore-kiss.service
```

For pyMC_Repeater on the same host, point its radio config at the daemon's PTY symlink:

```yaml
radio_type: kiss
kiss:
  port: "/tmp/sx1302-kiss"
  baud_rate: 115200
```

## Validate a deployment

Check the service and logs:

```bash
systemctl status sx1302-meshcore-kiss.service
journalctl -u sx1302-meshcore-kiss.service -f
```

Expected signs of a good deployment:

- `/tmp/sx1302-kiss` exists when using PTY mode;
- the service user can open `/dev/spidev0.0` and `/dev/gpiochip*`;
- the GPIO reset sequence completes without permission errors;
- the SX1302/WM1302 start path succeeds;
- pyMC_Repeater opens `/tmp/sx1302-kiss` as a KISS modem;
- pyMC SetHardware frames configure radio frequency, bandwidth, spreading factor, coding rate, and TX power;
- TX is tested only when legal and intentional.

## Safety notes

- Dashboard binds to `127.0.0.1:8080` by default.
- Raw payload logging is disabled by default.
- Packet events are kept in RAM only.
- Raw packet events are not retained in MQTT by default.
- MQTT password is redacted from `/api/config`.
- Bad RF CRC packets are not forwarded to pyMC.
- Always verify local radio regulations before enabling TX.

## Test

```bash
cd sx1302-meshcore-kiss
PYTHONPATH=src pytest -q
```

Current expected result:

```text
40 passed
```

## Development roadmap

Next practical hardening steps:

1. Real WM1302/SX1302 smoke test with the daemon in PTY mode.
2. Verify pyMC_Repeater opens `/tmp/sx1302-kiss` and can exchange KISS frames.
3. Add end-to-end integration test with a fake pyMC KISS client and fake SX1302 adapter.
4. Add optional TCP KISS endpoint if needed.
5. Improve periodic MQTT/status publishing and real connection tracking.
