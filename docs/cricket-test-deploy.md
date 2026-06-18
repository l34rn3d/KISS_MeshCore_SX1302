# SenseCAP Cricket Test Deploy

Use this checklist to redeploy the native pyMC TCP SX1302 driver on `sensecap-cricket` from a clean removal.

## 1. Clean Existing Driver

Run on Cricket if you want a fresh install:

```bash
sudo systemctl stop sx1302-meshcore-kiss 2>/dev/null || true
sudo systemctl disable sx1302-meshcore-kiss 2>/dev/null || true
sudo rm -f /etc/systemd/system/sx1302-meshcore-kiss.service
sudo rm -rf /etc/systemd/system/sx1302-meshcore-kiss.service.d
sudo systemctl daemon-reload
sudo systemctl reset-failed sx1302-meshcore-kiss 2>/dev/null || true
sudo rm -rf /opt/sx1302-meshcore-kiss /etc/sx1302-meshcore-kiss /var/log/sx1302-meshcore-kiss /run/sx1302-meshcore-kiss
```

## 2. Install Driver

```bash
cd /home/claude
rm -rf sx1302-meshcore-kiss
git clone --branch pymc-tcp-dev --single-branch https://github.com/l34rn3d/KISS_MeshCore_SX1302.git sx1302-meshcore-kiss
cd sx1302-meshcore-kiss
sudo ./scripts/install.sh --start
```

The installer should create `/opt/sx1302-meshcore-kiss`, build the Semtech C HAL bridge, install `/etc/sx1302-meshcore-kiss/config.yaml`, enable `sx1302-meshcore-kiss.service`, and start it.

## 3. Cricket Config

The default config should be usable for SenseCAP Cricket. Confirm these values:

```bash
sudo grep -n 'transport:\|profile:\|hotspot:\|backend:\|spi_device:\|sx1261_spi_path:\|reset_script_path:\|CONCENTRATOR_RESET_PIN\|port:' /etc/sx1302-meshcore-kiss/config.yaml
```

Expected driver-side basics:

```yaml
transport: "pymc_tcp"
pymc_tcp:
  bind_host: "0.0.0.0"
  port: 5055
startup:
  profile: "nebra_helium_docker"
  hotspot: "sensecap-fl1"
radio:
  backend: "semtech_c_hal"
  spi_device: "/dev/spidev0.0"
  sx1261_spi_path: "/dev/spidev0.1"
```

## 4. Configure pyMC Repeater

Edit `/etc/pymc_repeater/config.yaml` so pyMC uses native TCP instead of KISS:

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

Then restart pyMC:

```bash
sudo systemctl restart pymc-repeater.service
```

## 5. Verify

```bash
systemctl is-active sx1302-meshcore-kiss
systemctl is-active pymc-repeater
sudo journalctl -u sx1302-meshcore-kiss -n 80 --no-pager
sudo journalctl -u pymc-repeater -n 80 --no-pager
curl -fsS http://127.0.0.1:8080/api/status
curl -fsS http://127.0.0.1:8000/api/needs_setup
```

Healthy signs:

- `sx1302-meshcore-kiss` is active.
- `pymc-repeater` is active.
- SX1302 dashboard status shows `transport: "pymc_tcp"`.
- SX1302 dashboard status shows `pymc_tcp.connected_clients: 1` after pyMC starts.
- pyMC `/api/needs_setup` returns `"needs_setup": false`.
- pyMC logs include `TCPLoRaRadio initialized successfully`.

## 6. Roll Back

To remove only the SX1302 driver again:

```bash
sudo systemctl stop sx1302-meshcore-kiss 2>/dev/null || true
sudo systemctl disable sx1302-meshcore-kiss 2>/dev/null || true
sudo rm -f /etc/systemd/system/sx1302-meshcore-kiss.service
sudo rm -rf /etc/systemd/system/sx1302-meshcore-kiss.service.d
sudo systemctl daemon-reload
sudo rm -rf /opt/sx1302-meshcore-kiss /etc/sx1302-meshcore-kiss /var/log/sx1302-meshcore-kiss /run/sx1302-meshcore-kiss
```
