# pyMC_SX1302_Driver

pyMC-compatible SX1302/WM1302 driver and modem service.

This branch adds a host-facing modem service around a daemon-local SX1302/WM1302 driver. The default host transport is pyMC's native `0xAA | CMD | LEN | PAYLOAD | CRC16` protocol on TCP port `5055`, with the existing KISS PTY/serial path kept as a compatibility transport. The service owns the concentrator hardware using a driver layer that follows Semtech `libloragw` / `lgw_*` HAL semantics for board, RF chain, IF chain, RX, TX, status, and airtime operations.

```text
pyMC_Repeater / pyMC_core
        ⇅
Native pyMC TCP, usually host:5055, or KISS PTY / serial fallback
        ⇅
pyMC_SX1302_Driver service
        ⇅
Semtech-style SX1302/WM1302 driver layer
        ⇅
Linux SPI + GPIO reset/power control
        ⇅
SX1302 / SX1303 concentrator + SX1250 radios, e.g. WM1302
```

## What this is for

Use this when you want existing MeshCore/pyMC software to use an SX1302/WM1302 concentrator through this driver service instead of talking to the radio hardware directly.

The daemon is responsible for:

- listening for the configured host transport;
- creating the host endpoint, such as native pyMC TCP or a compatibility PTY/serial endpoint;
- accepting host TX, radio config, status, noise, CAD, and RX-start commands;
- configuring the SX1302/WM1302 radio from daemon config and host requests;
- resetting and starting the concentrator using board-specific SPI/GPIO settings;
- transmitting and receiving LoRa packets through the SX1302 driver layer;
- returning RX/TX/status frames, metadata, counters, MQTT telemetry, and dashboard state.

pyMC/pyMC_Repeater should use the configured service transport, normally `TCPLoRaRadio` / `pymc_tcp`, unless you explicitly need the compatibility KISS endpoint.

## Status

Prototype/alpha. The driver service, host protocol handling, config path, dashboard APIs, and adapter behavior are unit-tested. Hardware deployment still requires board-specific validation of SPI, GPIO reset pins, RF settings, legal TX configuration, and pyMC integration.

Current expected test result on a development machine:

```text
41 passed
```

## Implemented

- Native pyMC TCP modem protocol for `TCPLoRaRadio` on `0.0.0.0:5055` by default.
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
- Standalone SX1302 adapter with `semtech_c_hal` backend selection; vanilla pyMC is only a KISS peer.
- CRC policy inside the driver service boundary:
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
  - `/api/startup-profiles`
- `/api/hotspot-profiles`
- `/api/hotspot-profile` for selecting the installed hotspot model and applying its SPI/reset GPIO defaults

Hotspot dropdown entries are loaded from one YAML file per hotspot. Packaged defaults live in `src/sx1302_meshcore_kiss/hotspot_profiles/`; installed/editable copies are seeded into `/etc/sx1302-meshcore-kiss/hotspots/`. Copy `_template.yaml` to a new `name.yaml` file to add a board, or edit an existing file to change its SPI/reset pins. Re-running the installer keeps existing files in `/etc/sx1302-meshcore-kiss/hotspots/`.
- In-memory packet ring buffer only, default max 50 events.
- Config redaction for secrets.
- Basic systemd unit example.
- 62.5 kHz TX/RX investigation notes and live verification fields are documented in [`docs/62k5-tx-rx-notes.md`](docs/62k5-tx-rx-notes.md).

## Documentation

- [`docs/install.md`](docs/install.md): full install, service, config, and validation guide.
- [`docs/cricket-test-deploy.md`](docs/cricket-test-deploy.md): clean SenseCAP Cricket redeploy checklist for the SX1302 driver service.
- [`docs/architecture.md`](docs/architecture.md): daemon architecture and boundary notes.
- [`docs/62k5-tx-rx-notes.md`](docs/62k5-tx-rx-notes.md): 62.5 kHz SX1302/SX1261 TX/RX fixes, current split, diagnostics, and live test values.

## Quick start

### 1. Clone and install the SX1302 driver service

```bash
git clone --branch pymc-tcp-dev --single-branch https://github.com/l34rn3d/pymc_tcp_SX1302_Driver.git pyMC_SX1302_Driver
cd pyMC_SX1302_Driver
sudo ./scripts/install.sh --start
sudo editor /etc/sx1302-meshcore-kiss/config.yaml
```

The included default config is now based on the known-good `cricket` SenseCAP/WM1302 deployment: Semtech C HAL backend, `/dev/spidev0.0` + `/dev/spidev0.1`, reset pins `23/22`, sync word `5156`, LBT enabled, MQTT disabled, and CRC policy matching cricket. Only change identity/location or board-specific values if this device is genuinely wired differently. The dashboard listens on `0.0.0.0:8080` by default, so after start it should be reachable at:

```text
http://<device-ip>:8080/
```

The default host endpoint listens here:

```text
0.0.0.0:5055
```

The dashboard also has a **Host Interface** card where you can switch between `pymc_tcp` and standalone `kiss`, save the config, and restart the service. Transport changes take effect after restart.

Hardware profile confidence varies by board. `sensecap-fl1` has been live-checked on Cricket and applies the full SenseCAP reset profile. Other hotspot profiles should be treated as best-effort until tested on matching hardware, because SX1302 boards may need more than a single concentrator reset pin. See [`docs/install.md`](docs/install.md#hardware-profile-confidence).

### 2. Install pyMC_Repeater and edit its config

Install pyMC_Repeater using its normal installer/instructions, then edit its config file:

```bash
sudo editor /etc/pymc_repeater/config.yaml
```

Set pyMC_Repeater to the service transport and point it at the local driver endpoint:

```yaml
radio_type: pymc_tcp
mesh:
  path_hash_mode: 1
radio:
  frequency: 915075000
  bandwidth: 125000
  spreading_factor: 9
  coding_rate: 5
  sync_word: 13380
  tx_power: 26
pymc_tcp:
  host: "127.0.0.1"
  port: 5055
  token: ""
```

Restart pyMC_Repeater after the SX1302 driver service is running:

```bash
sudo systemctl restart pymc-repeater.service
```

### 3. Clean up / remove the SX1302 driver service

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
git clone --branch pymc-tcp-dev --single-branch https://github.com/l34rn3d/pymc_tcp_SX1302_Driver.git pyMC_SX1302_Driver
cd pyMC_SX1302_Driver
sudo ./scripts/install.sh --start
```

The installer sets up apt prerequisites, `/opt/sx1302-meshcore-kiss`, the `sx1302kiss` service user, a venv, builds the cricket-style Semtech C HAL bridge at `/opt/sx1302-meshcore-kiss/build/c_hal/libmeshcore_lgw.so`, creates `/etc/sx1302-meshcore-kiss/config.yaml`, installs the systemd unit, enables the service, and starts it when `--start` is supplied.

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

The packaged `config.example.yaml` is a SenseCAP/WM1302 baseline copied from the known-good cricket deployment. It defaults to the `pymc_tcp` host transport on port `5055`. The service config owns hardware/backend/reset/dashboard policy; pyMC sends live RF values after it connects.

Key driver defaults are:

```yaml
radio:
  backend: "semtech_c_hal"
  c_hal_lib: "/opt/sx1302-meshcore-kiss/build/c_hal/libmeshcore_lgw.so"
  sync_word: 5156
  spi_device: "/dev/spidev0.0"
  sx1261_spi_path: "/dev/spidev0.1"
  reset_script_path: "/opt/sx1302-meshcore-kiss/tools/reset_lgw_nebra.sh"
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

Do **not** add the old generic `915000000/SF8/null SX1261` values to the driver config. Use the pyMC snippet above for the MeshCore RF settings: `915075000`, BW125, SF9, CR4/5, sync word `13380`, TX power `26`.

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

For pyMC_Repeater on the same host, point its radio config at the driver service endpoint:

```yaml
radio_type: pymc_tcp
mesh:
  path_hash_mode: 1
radio:
  frequency: 915075000
  bandwidth: 125000
  spreading_factor: 9
  coding_rate: 5
  sync_word: 13380
  tx_power: 26
pymc_tcp:
  host: "127.0.0.1"
  port: 5055
  token: ""
```

## Validate a deployment

Check the service and logs:

```bash
systemctl status sx1302-meshcore-kiss.service
journalctl -u sx1302-meshcore-kiss.service -f
```

Expected signs of a good deployment:

- `/api/status` reports `transport: "pymc_tcp"`;
- `/api/status` reports `pymc_tcp.connected_clients: 1` after pyMC starts;
- the service user can open `/dev/spidev0.0` and `/dev/gpiochip*`;
- the GPIO reset sequence completes without permission errors;
- the SX1302/WM1302 start path succeeds;
- pyMC_Repeater logs `TCPLoRaRadio initialized successfully`;
- pyMC configures radio frequency, bandwidth, spreading factor, coding rate, and TX power through the service;
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
cd pyMC_SX1302_Driver
PYTHONPATH=src pytest -q
```

Current expected result:

```text
41 passed
```

## Development roadmap

Next practical hardening steps:

1. Harden authenticated host-client broadcast handling.
2. Add multi-client TX serialization if `max_clients > 1` is used.
3. Add end-to-end integration test with a fake pyMC host client and fake SX1302 adapter.
4. Keep KISS fallback tested for compatibility users.
5. Improve periodic MQTT/status publishing and real connection tracking.
