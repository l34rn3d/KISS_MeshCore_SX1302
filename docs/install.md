# Install / deploy pyMC_SX1302_Driver

This is the deployment guide for the SX1302 MeshCore driver and modem service.

The service is a Python daemon, but the radio side is not a pyMC hardware wrapper. It owns the SX1302/WM1302 concentrator and exposes a Semtech `libloragw` / `lgw_*`-style driver boundary for board config, RF chain config, IF chain config, start/stop, RX, TX, status, and airtime. The default host transport for pyMC/pyMC_Repeater is `pymc_tcp` on port `5055`; KISS remains a compatibility transport.

## 1. Prerequisites

Install system tools and hardware access packages appropriate for the target SBC:

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-dev build-essential gpiod
```

The daemon needs access to:

- `/dev/spidev*` for the SX1302/WM1302 SPI bus;
- `/dev/gpiochip*` for reset/power GPIOs;
- optional serial devices if using `kiss.mode: serial` instead of PTY.

Enable SPI in the board firmware/config before starting the service.

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

## 2. Fetch and install the repository

The easiest path is to clone the repo and run the installer:

```bash
git clone --branch pymc-tcp-dev --single-branch https://github.com/l34rn3d/pymc_tcp_SX1302_Driver.git pyMC_SX1302_Driver
cd pyMC_SX1302_Driver
sudo ./scripts/install.sh
```

The script installs prerequisites, copies the tracked source to `/opt/sx1302-meshcore-kiss`, creates the `sx1302kiss` service user, builds the venv, builds the cricket-style Semtech C HAL bridge at `/opt/sx1302-meshcore-kiss/build/c_hal/libmeshcore_lgw.so`, creates `/etc/sx1302-meshcore-kiss/config.yaml` if missing, installs/enables the systemd unit, and runs a basic import/help verification. It does **not** start the daemon unless you pass `--start` so board-specific GPIO/SPI config can be edited first.

Useful installer options:

```bash
sudo ./scripts/install.sh --help
sudo ./scripts/install.sh --start              # install and start/restart systemd service
sudo ./scripts/install.sh --skip-apt           # skip apt-get on pre-provisioned hosts
sudo ./scripts/install.sh --dev                # include test/dev dependencies
sudo ./scripts/install.sh --source-dir /path/to/checkout
```

If deploying from an already downloaded source tree instead of GitHub, run the installer from that checkout or pass `--source-dir`.

To remove the service later, run the cleanup helper. It is dry-run by default and prints what would be removed. With `--yes`, it stops/disables the service, removes `/etc/systemd/system/sx1302-meshcore-kiss.service`, and removes `/opt/sx1302-meshcore-kiss`. It keeps `/etc/sx1302-meshcore-kiss` unless `--purge-config` is supplied, so board/radio settings are not deleted accidentally.

```bash
sudo ./scripts/cleanup.sh
sudo ./scripts/cleanup.sh --yes
sudo ./scripts/cleanup.sh --yes --purge-config --remove-user
```

Manual install steps are below for troubleshooting or custom layouts.

### Manual fetch

```bash
sudo mkdir -p /opt
cd /opt
sudo git clone --branch pymc-tcp-dev --single-branch https://github.com/l34rn3d/pymc_tcp_SX1302_Driver.git sx1302-meshcore-kiss
cd /opt/sx1302-meshcore-kiss
```

If deploying from an already downloaded source tree instead of GitHub, copy that tree to:

```text
/opt/sx1302-meshcore-kiss
```

## 3. Create the service user

```bash
sudo useradd --system --home /opt/sx1302-meshcore-kiss --shell /usr/sbin/nologin sx1302kiss || true
sudo usermod -aG spi,gpio sx1302kiss
```

If the board uses different groups for SPI/GPIO, add `sx1302kiss` to those groups as well. You can verify device ownership with:

```bash
ls -l /dev/spidev* /dev/gpiochip* 2>/dev/null
```

## 4. Create the Python environment

The source tree includes `pyproject.toml` and can be installed editable:

```bash
cd /opt/sx1302-meshcore-kiss
sudo python3 -m venv .venv
sudo .venv/bin/pip install --upgrade pip wheel setuptools
sudo .venv/bin/pip install -e '.[dev]'
sudo chown -R sx1302kiss:sx1302kiss /opt/sx1302-meshcore-kiss
```

If `uv` is preferred and already installed:

```bash
cd /opt/sx1302-meshcore-kiss
sudo uv venv .venv
sudo uv pip install --python .venv/bin/python -e '.[dev]'
```

## 5. Create system config

```bash
sudo mkdir -p /etc/sx1302-meshcore-kiss
sudo cp /opt/sx1302-meshcore-kiss/config.example.yaml /etc/sx1302-meshcore-kiss/config.yaml
sudo editor /etc/sx1302-meshcore-kiss/config.yaml
```

Set at least:

- `transport: "pymc_tcp"` for the native pyMC TCP path;
- `pymc_tcp.bind_host` and `pymc_tcp.port`, usually `0.0.0.0:5055`;
- `dashboard.bind_host`, default `0.0.0.0` for Tailscale/LAN access; use `127.0.0.1` if you want local-only dashboard access;
- `node_id` and MQTT client/topic names for the individual repeater identity;
- only change reset GPIO/SPI values if the target is not wired like the known-good cricket SenseCAP/WM1302.

The default driver radio block is intentionally cricket-aligned for backend/hardware/reset policy. Do **not** swap it back to the old generic fallback values. RF channel values are intentionally owned by pyMC through the configured host transport.

```yaml
radio:
  backend: "semtech_c_hal"
  c_hal_lib: "/opt/sx1302-meshcore-kiss/build/c_hal/libmeshcore_lgw.so"
  sync_word: 5156
  spi_device: "/dev/spidev0.0"
  sx1261_spi_path: "/dev/spidev0.1"
  reset_enabled: true
  reset_required: false
  reset_script_path: "/opt/sx1302-meshcore-kiss/tools/reset_lgw_nebra.sh"
  gpio_chip: "gpiochip0"
  power_enable_pin: 18
  sx1302_reset_pin: 23
  sx1261_reset_pin: 22
  adc_reset_pin: 13
  lbt_enabled: true
crc:
  forward_unknown_crc: true
  publish_bad_crc_payload: false
mqtt:
  enabled: false
dashboard:
  bind_host: "0.0.0.0"
```

Use the pyMC snippet below for the live MeshCore RF values: `915075000`, BW125, SF9, CR4/5, sync word `13380`, TX power `26`.

## 6. Install systemd service

```bash
sudo cp /opt/sx1302-meshcore-kiss/packaging/sx1302-meshcore-kiss.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sx1302-meshcore-kiss.service
sudo systemctl start sx1302-meshcore-kiss.service
sudo systemctl status sx1302-meshcore-kiss.service
```

The example unit expects:

```text
WorkingDirectory=/opt/sx1302-meshcore-kiss
ExecStart=/opt/sx1302-meshcore-kiss/.venv/bin/python -m sx1302_meshcore_kiss --config /etc/sx1302-meshcore-kiss/config.yaml
User=sx1302kiss
Group=sx1302kiss
```

Edit the unit if you install to another path or use another service user.

## 7. Configure pyMC_Repeater

pyMC_Repeater should not use its direct radio hardware mode for this path. Point it at the driver service endpoint on `127.0.0.1:5055`.

If you want standalone KISS instead, set `transport: "kiss"` in the driver config or use the dashboard **Host Interface** selector, then point pyMC at the configured KISS PTY/serial path.

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

Then restart pyMC_Repeater after the SX1302 driver service is running:

```bash
sudo systemctl restart pymc-repeater.service
```

## 8. Validate safely

Check the daemon logs:

```bash
journalctl -u sx1302-meshcore-kiss.service -f
```

Validation goals:

- service starts without Python import errors;
- dashboard `/api/status` reports `transport: "pymc_tcp"`;
- dashboard `/api/status` reports `pymc_tcp.connected_clients: 1` after pyMC starts;
- service user can access SPI and GPIO;
- GPIO reset sequence completes without permission errors;
- SX1302/WM1302 start succeeds;
- pyMC_Repeater logs `TCPLoRaRadio initialized successfully`;
- pyMC config requests configure the radio;
- received good RF packets are delivered through the configured host transport;
- bad CRC packets are counted/published but not forwarded to pyMC;
- TX is tested only when legal and intentional.

Useful commands:

```bash
systemctl status sx1302-meshcore-kiss.service
journalctl -u sx1302-meshcore-kiss.service -n 100 --no-pager
curl -fsS http://127.0.0.1:8080/api/status
sudo -u sx1302kiss test -r /dev/spidev0.0 && echo spi_ok
```

## 9. Troubleshooting

### pyMC does not connect

Check that the service is listening and pyMC points at it:

```bash
grep -n "transport\|pymc_tcp\|port" /etc/sx1302-meshcore-kiss/config.yaml
sudo grep -n "radio_type\|pymc_tcp\|host\|port" /etc/pymc_repeater/config.yaml
journalctl -u sx1302-meshcore-kiss.service -n 100 --no-pager
```

### SPI permission denied

Check device permissions and groups:

```bash
id sx1302kiss
ls -l /dev/spidev* /dev/gpiochip* 2>/dev/null
```

Add the service user to the right hardware groups, then restart the service.

### GPIO reset fails

Install `gpiod` and verify the configured GPIO chip and line numbers. Board revisions can use different reset pins; do not assume the example pinout is correct for every WM1302/SenseCAP carrier.

### pyMC cannot connect

pyMC should use the configured service transport, not direct SX1302/WM1302 hardware mode, when this service owns the concentrator.
