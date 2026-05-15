# Install sx1302-meshcore-kiss

This is a source-install guide for the local SX1302 MeshCore KISS daemon.

## 1. Create a virtual environment

```bash
cd /home/chris/mesh2/sx1302-meshcore-kiss
uv venv .venv
. .venv/bin/activate
uv pip install -e '.[dev]'
```

The daemon currently uses the local `pyMC_core` SX1302 library. If editable install resolution is not desired on the target, run with an explicit `PYTHONPATH`:

```bash
PYTHONPATH=src:/home/chris/mesh2/pyMC_core/src sx1302-meshcore-kiss --config config.example.yaml
```

## 2. Create system config

```bash
sudo mkdir -p /etc/sx1302-meshcore-kiss
sudo cp config.example.yaml /etc/sx1302-meshcore-kiss/config.yaml
sudo editor /etc/sx1302-meshcore-kiss/config.yaml
```

Set at least:

- KISS PTY symlink, usually `/tmp/sx1302-kiss`
- SPI device, usually `/dev/spidev0.0`
- reset GPIO chip and pin numbers for the WM1302/SX1302 board
- LoRa frequency/bandwidth/spreading factor/coding rate
- MQTT host/credentials, if MQTT is enabled

Do not commit real MQTT passwords or other secrets. Use `[REDACTED]` in notes and logs.

## 3. Hardware permissions

The service user needs access to SPI and GPIO.

Typical options:

```bash
sudo usermod -aG spi,gpio sx1302-kiss
```

or a board-specific udev/systemd policy that grants access to:

- `/dev/spidev*`
- `/dev/gpiochip*`

If GPIO reset uses the current `gpioset` path from `pyMC_core`, ensure the `gpiod` tools are installed and usable by the service user.

## 4. Install systemd service

```bash
sudo cp packaging/sx1302-meshcore-kiss.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sx1302-meshcore-kiss.service
sudo systemctl start sx1302-meshcore-kiss.service
sudo systemctl status sx1302-meshcore-kiss.service
```

The example unit expects the repo at `/home/chris/mesh2/sx1302-meshcore-kiss` and config at `/etc/sx1302-meshcore-kiss/config.yaml`; edit the unit if installed elsewhere.

## 5. Configure pyMC_Repeater

Point pyMC_Repeater at the daemon-owned PTY as a normal KISS radio:

```yaml
radio_type: kiss
kiss:
  port: "/tmp/sx1302-kiss"
  baud_rate: 115200
```

## 6. Validate safely

Run the unit and check logs:

```bash
journalctl -u sx1302-meshcore-kiss.service -f
```

Validation goals:

- daemon creates `/tmp/sx1302-kiss`
- SPI open and SX1302 start succeed
- reset GPIO pulses without permission errors
- pyMC_Repeater opens the PTY
- received good RF packets appear as KISS `Data 0x00`
- bad CRC packets are counted/published but not forwarded to pyMC
- TX is only tested when legal and intentional
