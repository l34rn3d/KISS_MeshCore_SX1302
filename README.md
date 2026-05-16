# sx1302-meshcore-kiss

MeshCore-compatible KISS modem daemon for SX1302/WM1302 concentrators.

This project is **not a pyMC radio implementation** and pyMC does not drive the hardware directly. It is a standalone modem bridge: pyMC/pyMC_Repeater talks normal KISS over a PTY or serial device, while this daemon owns the SX1302/WM1302 hardware using a daemon-local driver layer that follows Semtech `libloragw` / `lgw_*` HAL semantics for board, RF chain, IF chain, RX, TX, status, and airtime operations.

```text
pyMC_Repeater / pyMC_core
        ⇅
KISS serial / PTY interface, usually /run/sx1302-meshcore-kiss/sx1302-kiss
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
41 passed
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
- PTY endpoint with default symlink `/run/sx1302-meshcore-kiss/sx1302-kiss`.
- Serial endpoint for real tty devices.
- Standalone SX1302 adapter around daemon-local `sx1302_meshcore_kiss.sx1302.radio.SX1302Radio`; vanilla pyMC is only a KISS peer.
- CRC policy inside the KISS daemon/service boundary:
  - `crc_ok=True`: forward to pyMC, MQTT `rx/good`, dashboard event.
  - `crc_ok=False`: drop from pyMC, MQTT `rx/bad_crc`, dashboard event.
  - `crc_ok=None`: drop by default, optional lab-only forwarding.
- MQTT JSON telemetry schemas with payload hex and base64.
- Network-reachable HTTP dashboard with browser UI and APIs, bound to `0.0.0.0:8080` by default:
  - `/` live dashboard page
  - `/api/status`
  - `/api/counters`
  - `/api/packets`
  - `/api/config`
- In-memory packet ring buffer only, default max 50 events.
- Config redaction for secrets.
- Basic systemd unit example.
- 62.5 kHz TX/RX investigation notes and live verification fields are documented in [`docs/62k5-tx-rx-notes.md`](docs/62k5-tx-rx-notes.md).

## Documentation

- [`docs/install.md`](docs/install.md): full install, service, config, and validation guide.
- [`docs/architecture.md`](docs/architecture.md): daemon architecture and boundary notes.
- [`docs/62k5-tx-rx-notes.md`](docs/62k5-tx-rx-notes.md): 62.5 kHz SX1302/SX1261 TX/RX fixes, current split, diagnostics, and live test values.

## Quick start

### 1. Clone and install the SX1302 KISS bridge

```bash
git clone --branch semtech-driver --single-branch https://github.com/l34rn3d/KISS_MeshCore_SX1302.git sx1302-meshcore-kiss
cd sx1302-meshcore-kiss
sudo ./scripts/install.sh
sudo editor /etc/sx1302-meshcore-kiss/config.yaml
sudo systemctl start sx1302-meshcore-kiss.service
```

The included default config is now based on the known-good `cricket` SenseCAP/WM1302 deployment: Semtech C HAL backend, `/dev/spidev0.0` + `/dev/spidev0.1`, reset pins `23/22`, sync word `5156`, LBT enabled, MQTT disabled, and CRC policy matching cricket. Only change identity/location or board-specific values if this device is genuinely wired differently. The dashboard listens on `0.0.0.0:8080` by default, so after start it should be reachable at:

```text
http://<device-ip>:8080/
```

The bridge creates the KISS PTY here by default:

```text
/run/sx1302-meshcore-kiss/sx1302-kiss
```

### 2. Install pyMC_Repeater and edit its config

Install pyMC_Repeater using its normal installer/instructions, then edit its config file:

```bash
sudo editor /etc/pymc_repeater/config.yaml
```

Set pyMC_Repeater to KISS mode, point it at the SX1302 bridge device path, and use the same MeshCore radio/path-hash values as cricket:

```yaml
radio_type: kiss
mesh:
  path_hash_mode: 1
radio:
  frequency: 915075000
  bandwidth: 125000
  spreading_factor: 9
  coding_rate: 5
  sync_word: 13380
  tx_power: 26
kiss:
  port: "/run/sx1302-meshcore-kiss/sx1302-kiss"
  baud_rate: 115200
```

Restart pyMC_Repeater after the SX1302 bridge is running:

```bash
sudo systemctl restart pymc-repeater.service
```

### 3. Clean up / remove the SX1302 bridge

Preview what would be removed:

```bash
sudo ./scripts/cleanup.sh
```

Remove the systemd service and installed app directory, but keep config:

```bash
sudo ./scripts/cleanup.sh --yes
```

Full removal including saved config and service user:

```bash
sudo ./scripts/cleanup.sh --yes --purge-config --remove-user
```

## Install / deploy

Full install/service notes are in [`docs/install.md`](docs/install.md). The easiest path on the target SBC is:

```bash
git clone --branch semtech-driver --single-branch https://github.com/l34rn3d/KISS_MeshCore_SX1302.git sx1302-meshcore-kiss
cd sx1302-meshcore-kiss
sudo ./scripts/install.sh
```

The installer sets up apt prerequisites, `/opt/sx1302-meshcore-kiss`, the `sx1302kiss` service user, a venv, builds the cricket-style Semtech C HAL bridge at `/opt/sx1302-meshcore-kiss/build/c_hal/libmeshcore_lgw.so`, creates `/etc/sx1302-meshcore-kiss/config.yaml`, and installs the systemd unit. It enables the service but does not start it unless you pass `--start`, so you can edit board-specific GPIO/SPI settings first.

To remove the service later, use the cleanup helper. It is dry-run by default and prints exactly what it would remove; pass `--yes` to actually stop/disable the service and remove the installed app directory. Config is kept unless `--purge-config` is provided.

```bash
sudo ./scripts/cleanup.sh
sudo ./scripts/cleanup.sh --yes
sudo ./scripts/cleanup.sh --yes --purge-config --remove-user
```

Manual venv-only development install:

```bash
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

The packaged `config.example.yaml` is a SenseCAP/WM1302 baseline copied from the known-good cricket deployment, except it keeps the safer `/run/sx1302-meshcore-kiss/sx1302-kiss` KISS path instead of cricket's older `/tmp` path. The bridge config owns hardware/backend/reset/dashboard policy; pyMC sends live RF values over MeshCore KISS `SetRadio` / `SetTxPower` after it connects.

Key bridge defaults are:

```yaml
radio:
  backend: "semtech_c_hal"
  c_hal_lib: "/opt/sx1302-meshcore-kiss/build/c_hal/libmeshcore_lgw.so"
  sync_word: 5156
  spi_device: "/dev/spidev0.0"
  sx1261_spi_path: "/dev/spidev0.1"
  reset_script_path: "/opt/sx1302-meshcore-kiss/tools/reset_wm1302_pinctrl.sh"
  sx1302_reset_pin: 23
  sx1261_reset_pin: 22
  lbt_enabled: true
crc:
  forward_unknown_crc: true
  publish_bad_crc_payload: false
mqtt:
  enabled: false
dashboard:
  bind_host: "0.0.0.0"
```

Do **not** add the old generic `915000000/SF8/null SX1261` values to the bridge config. Use the pyMC snippet above for the MeshCore RF settings: `915075000`, BW125, SF9, CR4/5, sync word `13380`, TX power `26`.

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

For pyMC_Repeater on the same host, point its radio config at the daemon's PTY symlink. This path must match `kiss.symlink` in `/etc/sx1302-meshcore-kiss/config.yaml`; the default is `/run/sx1302-meshcore-kiss/sx1302-kiss`. Do **not** use the old `/tmp/sx1302-kiss` path on systemd hosts, because Linux protected-symlink rules can prevent the `repeater` user from opening it.

For existing installs, change both sides to the same device path:

- SX1302 daemon: `/etc/sx1302-meshcore-kiss/config.yaml` → `kiss.symlink`
- pyMC_Repeater: `/etc/pymc_repeater/config.yaml` → `kiss.port`

```yaml
radio_type: kiss
mesh:
  path_hash_mode: 1
radio:
  frequency: 915075000
  bandwidth: 125000
  spreading_factor: 9
  coding_rate: 5
  sync_word: 13380
  tx_power: 26
kiss:
  port: "/run/sx1302-meshcore-kiss/sx1302-kiss"
  baud_rate: 115200
```

## Validate a deployment

Check the service and logs:

```bash
systemctl status sx1302-meshcore-kiss.service
journalctl -u sx1302-meshcore-kiss.service -f
```

Expected signs of a good deployment:

- `/run/sx1302-meshcore-kiss/sx1302-kiss` exists when using PTY mode;
- the service user can open `/dev/spidev0.0` and `/dev/gpiochip*`;
- the GPIO reset sequence completes without permission errors;
- the SX1302/WM1302 start path succeeds;
- pyMC_Repeater opens `/run/sx1302-meshcore-kiss/sx1302-kiss` as a KISS modem;
- pyMC SetHardware frames configure radio frequency, bandwidth, spreading factor, coding rate, and TX power;
- TX is tested only when legal and intentional.

## Safety notes

- Dashboard binds to `0.0.0.0:8080` by default so it is reachable over Tailscale/LAN interfaces; firewall or tunnel access should be controlled at the host/network layer.
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
41 passed
```

## Development roadmap

Next practical hardening steps:

1. Real WM1302/SX1302 smoke test with the daemon in PTY mode.
2. Verify pyMC_Repeater opens `/run/sx1302-meshcore-kiss/sx1302-kiss` and can exchange KISS frames.
3. Add end-to-end integration test with a fake pyMC KISS client and fake SX1302 adapter.
4. Add optional TCP KISS endpoint if needed.
5. Improve periodic MQTT/status publishing and real connection tracking.
