# Architecture

`sx1302-meshcore-kiss` is intentionally a modem shim, not a MeshCore router and not a pyMC hardware wrapper.

The application is a Python daemon, but its radio boundary is modeled after the Semtech SX1302 `libloragw` / `lgw_*` driver interface. The daemon owns the concentrator hardware and pyMC only sees a KISS modem.

## High-level ownership

```text
pyMC_Repeater / pyMC_core
  owns: MeshCore routing/application behavior
  talks: KISS frames only

sx1302-meshcore-kiss
  owns: KISS endpoint, MeshCore KISS SetHardware handling, dashboard/MQTT telemetry
  talks: Semtech-style SX1302 adapter API

SX1302/WM1302 driver layer
  owns: board/RF/IF config, SPI access, GPIO reset/power, RX/TX/status/airtime
  talks: Linux spidev + gpiochip/gpioset
```

## Packet paths

### pyMC to RF

```text
pyMC KISS Data 0x00
  -> KISS decoder
  -> 1..255 byte payload validation
  -> MQTT tx/requested + dashboard ring event
  -> SX1302Adapter.transmit()
  -> Semtech-style lgw_send path
  -> MQTT tx/done or tx/error
  -> KISS TxDone 0xF8 as SetHardware event
```

### RF to pyMC

```text
Semtech-style lgw_receive path
  -> SX1302Adapter.receive()
  -> CRC policy
  -> if good: KISS Data 0x00 + optional RxMeta 0xF9
  -> MQTT rx/good|rx/bad_crc|rx/unknown_crc
  -> dashboard ring event
```

## Runtime radio configuration

Daemon config provides startup hardware and known-good SenseCAP/WM1302 radio baseline settings:

- Semtech C HAL backend and library path;
- SPI device paths for SX1302 and SX1261 (`/dev/spidev0.0`, `/dev/spidev0.1`);
- GPIO chip and reset/power pins matching the working cricket deployment (`23/22` resets);
- MeshCore sync-word/LBT/CRC defaults matching cricket;
- duty-cycle policy.

pyMC can still override live frequency, bandwidth, spreading factor, coding rate, and TX power after connect:

- `SetRadio 0x09`: frequency, bandwidth, spreading factor, coding rate;
- `SetTxPower 0x0A`: TX power;
- matching getters report the current applied daemon state.

The host should not import or call pyMC SX1302 hardware wrappers for this path.

## Persistence rule

Raw packet history is not stored to disk. The dashboard uses `collections.deque(maxlen=50)` via `PacketRingBuffer`.

## CRC rule

KISS has no CRC/FCS in this implementation. RF CRC is handled by the SX1302 driver/HAL and then enforced by daemon policy. Bad CRC RF packets are telemetry only and are not forwarded to pyMC.

## Deployment shape

The intended Linux service layout is:

```text
/opt/sx1302-meshcore-kiss/                  source checkout + venv
/etc/sx1302-meshcore-kiss/config.yaml       daemon config
/etc/systemd/system/sx1302-meshcore-kiss.service
/run/sx1302-meshcore-kiss/sx1302-kiss                            PTY symlink used by pyMC_Repeater
```

The service user needs access to SPI and GPIO devices but pyMC does not.
