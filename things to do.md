# Things to do

Outstanding work for `sx1302-meshcore-kiss` after the initial daemon implementation and KISS compatibility fixes.

Current repo:

```text
sx1302-meshcore-kiss
```

Last verified state:

```text
33 tests passing
```

## Recently completed: standalone SX1302 KISS daemon

Implemented:

- Ported the SX1302/WM1302 SPI/GPIO HAL into the daemon under `sx1302_meshcore_kiss.sx1302`.
- Default adapter now uses daemon-local `SX1302Radio`, not `pymc_core.hardware.sx1302_wrapper`.
- `pyMC_core`/pyMC_Repeater are KISS peers only; vanilla pyMC can run unchanged.
- Packaging runtime dependencies now include the hardware dependencies directly.

## Recently corrected: daemon-only CRC/packaging work

Correction after review:

- `pyMC_core` was returned to its previous state; no CRC receive API changes are kept there.
- The daemon still enforces CRC policy at its own service boundary for injected/future adapter `RxPacket` values.
- Good daemon-side RX packets are forwarded over KISS `Data 0x00`.
- Bad/unknown CRC daemon-side RX packets are not forwarded as KISS data; they are only telemetry/dashboard/MQTT events.
- Added Python packaging metadata and install/service documentation in `docs/install.md`.

## Recently completed: MeshCore SetHardware `0x06` protocol alignment

Implemented from `kiss_modem_protocol.md`:

- `TxDone 0xF8` and `RxMeta 0xF9` now emit as MeshCore `SetHardware 0x06` frames.
- Standard KISS data frames remain unchanged.
- Standard KISS clients can ignore SetHardware frames safely.
- Supported SetHardware requests now return little-endian responses:
  - `0x09 SetRadio`
  - `0x0A SetTxPower`
  - `0x0B GetRadio`
  - `0x0C GetTxPower`
  - `0x0D GetCurrentRssi`
  - `0x0E IsChannelBusy`
  - `0x0F GetAirtime`
  - `0x10 GetNoiseFloor`
  - `0x11 GetVersion`
  - `0x12 GetStats`
  - `0x19 SetSignalReport`
  - `0x1A GetSignalReport`
- Unsupported crypto/device/sensor requests return MeshCore error responses instead of being treated as unknown KISS commands.

Remaining limitations in this area:

- `GetAirtime` is currently a placeholder response.
- `IsChannelBusy` currently reports clear unless deeper hardware LBT/channel activity support is added.
- `GetNoiseFloor` currently returns a placeholder noise floor until SX1261/SX1302-backed noise-floor measurement is implemented.
- Crypto/identity/sensors/reboot are not implemented and return unsupported/no-callback style errors.

## 1. Real WM1302/SX1302 hardware validation

This is the biggest hardware gap. The code is unit-tested, but not yet proven on real concentrator hardware.

Verify on a real host:

- daemon starts with the real SPI device
- GPIO reset sequence works
- `SX1302Radio.begin()` succeeds
- `lgw_start()` succeeds
- RX loop receives real packets
- TX actually transmits when explicitly allowed
- RSSI/SNR metadata is sane
- bad CRC / failed receive metadata can be observed or confirmed unavailable

Acceptance:

- daemon runs against the real WM1302/SX1302 without crashing
- pyMC can send a packet through the daemon to RF
- valid RF packets return to pyMC unchanged over KISS
- MQTT/dashboard show matching RX/TX events

## 2. Real pyMC_Repeater integration test

Need an end-to-end test where `pyMC_Repeater` opens the daemon PTY as a KISS modem.

Flow to prove:

```text
daemon creates /tmp/sx1302-kiss
pyMC_Repeater radio_type: kiss opens it
pyMC sends KISS Data 0x00
daemon receives it
daemon calls SX1302 transmit
daemon injects RX Data 0x00
pyMC receives it
```

Start with a fake SX1302 adapter, then repeat with real hardware.

Acceptance:

- `pyMC_Repeater` can use `/tmp/sx1302-kiss` as its KISS radio port
- outgoing pyMC packets become daemon TX requests
- daemon RX frames are received by pyMC

## 3. Pass CRC/reporting only through KISS daemon path

`pyMC_core` is intentionally not being changed for CRC metadata. The daemon should remain the boundary that decides what is forwarded over KISS:

- KISS `Data 0x00` carries raw MeshCore payload bytes only.
- `RxMeta 0xF9` and `TxDone 0xF8` travel as MeshCore `SetHardware 0x06` frames.
- Bad/unknown CRC packets, when an SX1302-facing daemon path can surface them, should be MQTT/dashboard telemetry only and must not be sent as KISS `Data 0x00`.

Still needed: prove whether the daemon-local SX1302 receive path can surface bad CRC packets from real hardware; if not, keep bad-CRC RF reporting marked unsupported while still enforcing the service-level CRC policy for any `RxPacket(crc_ok=False)` values.

Acceptance:

- good RX packets still reach pyMC unchanged
- bad CRC packets do not reach pyMC
- bad CRC packets publish MQTT `rx/bad_crc`
- unknown CRC packets publish MQTT `rx/unknown_crc`
- dashboard counters reflect real CRC outcomes

## 4. Add periodic MQTT status/config/health/counters publishing

Packet events are currently published, but the full required periodic retained topics still need a loop.

Required topics:

```text
meshcore/sx1302/<node_id>/status
meshcore/sx1302/<node_id>/config/radio
meshcore/sx1302/<node_id>/config/daemon
meshcore/sx1302/<node_id>/stats/counters
meshcore/sx1302/<node_id>/stats/sx1302
meshcore/sx1302/<node_id>/health
```

Recommended policy:

- status/config/health/counters: retained, QoS 1
- packet events: not retained by default

Acceptance:

- MQTT subscribers see current daemon status after connecting
- counters update periodically
- SX1302 status metadata is published periodically
- packet events remain non-retained unless explicitly configured

## 5. Fix MQTT real connection tracking and error handling

Current MQTT publisher marks itself connected immediately after `connect_async()` and `loop_start()`.

Need to add:

- `on_connect`
- `on_disconnect`
- real connected/disconnected state
- reconnect counters
- publish error counting
- dashboard MQTT state
- safe behavior when broker is offline

Acceptance:

- daemon continues KISS/SX1302 operation during MQTT outage
- MQTT reconnect is reflected in dashboard/status
- failed publishes increment counters instead of crashing the daemon

## Recently completed: browser dashboard UI

Implemented:

- `/` now serves a live browser dashboard instead of a placeholder.
- Shows Radio, MQTT, KISS, counters, active redacted config, and latest packet events.
- Refreshes `/api/status`, `/api/counters`, `/api/packets`, and `/api/config` every 2 seconds.
- Keeps packet history in the existing RAM-only ring buffer; no persistent packet storage added.

## 7. Track real uptime

Current `/api/counters` returns `uptime_seconds=0`.

Need to track process start time and return real uptime in:

- `/api/status`
- `/api/counters`
- MQTT status/counters

Acceptance:

- uptime increases while daemon runs
- uptime resets after restart

## 8. Optional TCP KISS endpoint

The original guide listed TCP as optional.

Currently implemented:

- PTY endpoint
- serial endpoint

Missing:

- TCP KISS server endpoint

Acceptance, if needed:

- daemon can listen on configured TCP bind/port
- a KISS client can connect over TCP
- framing behavior matches PTY/serial modes

## 9. Stronger config validation

Config loading is currently shallow. Invalid config may fail later at runtime.

Add validation for:

- allowed KISS modes
- allowed bandwidths, including the known experimental 62.5 kHz mode
- spreading factor range
- coding rate range
- TX power range
- MQTT QoS range
- dashboard max packet event limits
- invalid GPIO pin values
- impossible or unsafe combinations

Acceptance:

- bad config fails at startup with a clear error message
- valid example config passes validation

## 10. Main loop hardening

Current loops are basic and should be made more robust before unattended service use.

Improve:

- RX loop recovery after adapter receive errors
- KISS endpoint recovery after PTY/serial failure
- task cancellation with clear exception logging
- daemon state transitions: starting, running, degraded, stopping
- dashboard failure isolation
- clean shutdown ordering

Acceptance:

- one transient RX error does not kill the daemon silently
- MQTT/dashboard failure does not stop radio operation
- shutdown does not leave PTY symlink/device resources behind

## 11. Apply retain/QoS policy consistently

Publisher supports retain/QoS, but service calls do not yet consistently apply the required policy.

Need:

- retained QoS 1 for status/config/health/counters
- non-retained packet events by default
- configurable packet event QoS
- no retained raw payload events unless explicitly enabled

Acceptance:

- MQTT retained messages are only status/config/health/counters by default
- packet payload events are not retained by default

## 12. Complete raw payload privacy controls

Config exists for payload behavior, but MQTT schemas currently include payload hex/base64 for packet events.

Relevant config:

```yaml
logging:
  log_raw_payloads: false

crc:
  publish_bad_crc_payload: true
  dashboard_show_bad_crc_payload: true
```

Need to apply these controls consistently:

- logging should not include raw payloads unless enabled
- bad CRC payload publication should be configurable
- dashboard bad CRC payload display should be configurable
- optional future setting for suppressing all MQTT payload bytes if needed

Acceptance:

- default logs contain event IDs/status, not raw payloads
- config can hide bad CRC payload bytes from MQTT/dashboard

## 13. Verify exact `TxDone 0xF8` payload behavior

Current daemon sends:

```text
0xF8 payload 0x01 = success
0xF8 payload 0x00 = failure
```

The implementation guide requires `TxDone 0xF8`, but does not fully define payload bytes.

Need to compare against a known MeshCore SX12xx KISS modem implementation.

Acceptance:

- `TxDone 0xF8` payload exactly matches MeshCore client expectations
- standard KISS clients still ignore it safely

## 14. Confirm pyMC handling of `RxMeta 0xF9`

The daemon emits:

```text
RxMeta 0xF9
payload = [snr_i8_x4][rssi_i8]
```

But the current `pyMC_core` KISS wrapper appears to ignore non-Data frames.

Need to confirm whether pyMC should consume `RxMeta 0xF9`, or whether metadata should remain MQTT/dashboard only.

Acceptance:

- pyMC payload path remains unaffected
- if pyMC should expose RSSI/SNR from KISS, update its KISS wrapper or integration path

## 15. Packaging and install docs

Current repo has:

- `pyproject.toml`
- README
- systemd unit example
- config example

Still missing:

- install commands
- venv/uv setup instructions
- example `/etc/sx1302-meshcore-kiss/config.yaml`
- system user/group creation instructions
- service install/enable commands
- hardware permissions notes for SPI/GPIO

Acceptance:

- a fresh Linux host can install and run the daemon by following docs
- systemd service starts cleanly with documented paths

## Recommended next implementation order

1. Periodic MQTT status/config/health/counters publishing.
2. MQTT real connection tracking and publish error counters.
3. Fake end-to-end PTY integration test with fake SX1302 adapter.
4. Process uptime and better dashboard status.
5. Hardware-prove the daemon-local SX1302 receive metadata path so bad CRC packets can be reported instead of assuming all received packets are good.
6. Real WM1302/SX1302 hardware validation.
