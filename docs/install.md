# Install / deploy sx1302-meshcore-kiss

This is the deployment guide for the SX1302 MeshCore KISS daemon.

The service is a Python daemon, but the radio side is not a pyMC hardware wrapper. It owns the SX1302/WM1302 concentrator and exposes a Semtech `libloragw` / `lgw_*`-style driver boundary for board config, RF chain config, IF chain config, start/stop, RX, TX, status, and airtime. pyMC/pyMC_Repeater should remain a KISS client only.

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

## 2. Fetch and install the repository

The easiest path is to clone the repo and run the installer:

```bash
git clone --branch semtech-driver --single-branch https://github.com/l34rn3d/KISS_MeshCore_SX1302.git sx1302-meshcore-kiss
cd sx1302-meshcore-kiss
sudo ./scripts/install.sh
```

The script installs prerequisites, copies the tracked source to `/opt/sx1302-meshcore-kiss`, creates the `sx1302kiss` service user, builds the venv, creates `/etc/sx1302-meshcore-kiss/config.yaml` if missing, installs/enables the systemd unit, and runs a basic import/help verification. It does **not** start the daemon unless you pass `--start` so board-specific GPIO/SPI config can be edited first.

Useful installer options:

```bash
sudo ./scripts/install.sh --help
sudo ./scripts/install.sh --start              # install and start/restart systemd service
sudo ./scripts/install.sh --skip-apt           # skip apt-get on pre-provisioned hosts
sudo ./scripts/install.sh --dev                # include test/dev dependencies
sudo ./scripts/install.sh --source-dir /path/to/checkout
```

If deploying from an already downloaded source tree instead of GitHub, run the installer from that checkout or pass `--source-dir`.

Manual install steps are below for troubleshooting or custom layouts.

### Manual fetch

```bash
sudo mkdir -p /opt
cd /opt
sudo git clone --branch semtech-driver --single-branch https://github.com/l34rn3d/KISS_MeshCore_SX1302.git sx1302-meshcore-kiss
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

- `kiss.mode`, usually `pty`;
- `kiss.symlink`, usually `/run/sx1302-meshcore-kiss/sx1302-kiss`;
- `radio.spi_device`, usually `/dev/spidev0.0`;
- reset GPIO chip and pin numbers for the exact WM1302/SX1302 board;
- MQTT host/credentials only if MQTT is enabled.

The initial `radio.frequency_hz`, `bandwidth_hz`, `spreading_factor`, `coding_rate`, and `tx_power_dbm` values are only daemon fallback/default state. For the normal pyMC_Repeater KISS path, pyMC sends the live radio settings after connect with MeshCore `SetRadio 0x09` and `SetTxPower 0x0A`, so deployment instructions should not rely on hard-coding those initial RF values.

Example hardware-focused radio block:

```yaml
radio:
  spi_device: "/dev/spidev0.0"
  sx1261_spi_path: null
  reset_enabled: true
  reset_required: false
  gpio_chip: "gpiochip0"
  power_enable_pin: 18
  sx1302_reset_pin: 17
  sx1261_reset_pin: 5
  adc_reset_pin: 13
  duty_cycle_enforcement: "raise"
```

Do not commit real MQTT passwords or other secrets. Use `[REDACTED]` in notes and logs.

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

pyMC_Repeater should not use its direct radio hardware mode for this path. Point it at the daemon-owned KISS PTY. The pyMC `kiss.port` value must match the daemon `kiss.symlink` value in `/etc/sx1302-meshcore-kiss/config.yaml`; the default is `/run/sx1302-meshcore-kiss/sx1302-kiss`. Avoid the old `/tmp/sx1302-kiss` path on systemd hosts because Linux protected-symlink rules can block another service user from following a symlink created in sticky `/tmp`.

For existing installs, change both sides to the same device path:

- SX1302 daemon: `/etc/sx1302-meshcore-kiss/config.yaml` → `kiss.symlink`
- pyMC_Repeater: `/etc/pymc_repeater/config.yaml` → `kiss.port`

```yaml
radio_type: kiss
kiss:
  port: "/run/sx1302-meshcore-kiss/sx1302-kiss"
  baud_rate: 115200
```

Then restart pyMC_Repeater after the KISS daemon is running:

```bash
sudo systemctl restart pymc-repeater.service
```

## 8. Validate safely

Check the KISS daemon logs:

```bash
journalctl -u sx1302-meshcore-kiss.service -f
```

Validation goals:

- service starts without Python import errors;
- daemon creates `/run/sx1302-meshcore-kiss/sx1302-kiss` in PTY mode;
- service user can access SPI and GPIO;
- GPIO reset sequence completes without permission errors;
- SX1302/WM1302 start succeeds;
- pyMC_Repeater opens `/run/sx1302-meshcore-kiss/sx1302-kiss`;
- pyMC SetHardware requests configure the radio;
- received good RF packets appear as KISS `Data 0x00`;
- bad CRC packets are counted/published but not forwarded to pyMC;
- TX is tested only when legal and intentional.

Useful commands:

```bash
systemctl status sx1302-meshcore-kiss.service
journalctl -u sx1302-meshcore-kiss.service -n 100 --no-pager
ls -l /run/sx1302-meshcore-kiss/sx1302-kiss
sudo -u sx1302kiss test -r /dev/spidev0.0 && echo spi_ok
```

## 9. Troubleshooting

### PTY does not appear

Check that `kiss.mode` is `pty`, then inspect logs:

```bash
grep -n "mode\|symlink" /etc/sx1302-meshcore-kiss/config.yaml
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

Confirm the symlink exists and pyMC config points at the same path:

```bash
ls -l /run/sx1302-meshcore-kiss/sx1302-kiss
sudo grep -n "radio_type\|kiss:\|port:" /etc/pymc_repeater/config.yaml
```

pyMC should use `radio_type: kiss`, not direct SX1302/WM1302 hardware mode, when this daemon owns the concentrator.
