# 62.5 kHz SX1302/SX1261 Notes

## Confirmed TX Fix

The original 62.5 kHz TX path was not actually transmitting at 62.5 kHz. The bridge accepted `bandwidth_hz=62500`, but `map_bw(62500)` returned `BW_125KHZ`, so `mc_lgw_send()` populated `pkt.bandwidth` as 125 kHz. This made the modem appear to support 62.5 kHz while silently falling back to 125 kHz.

The fix is to build against a patched, project-local Semtech HAL copy instead of modifying the read-only baseline HAL tree directly.

`tools/build_semtech_bridge.sh` now:

- Copies `/kiss/pyMC_Repeater_working_copy_20260515-213738/sx1302_hal` to `build/c_hal/hal_patched`.
- Adds `BW_62K5 = 0x03`.
- Extends `IS_LORA_BW()` to accept `BW_62K5`.
- Extends `lgw_bw_getval()` to return `62500` for `BW_62K5`.
- Extends LoRa airtime helper timing with `bw_pow = 0.5` for 62.5 kHz.
- Extends SX1302 TX start-delay handling for `BW_62K5` using an extrapolated SX1250 delay.
- Builds the bridge against that patched HAL copy.

The bridge now maps `62500` to `BW_62K5` instead of `BW_125KHZ`.

Live verification fields were added to status:

- `hal_bandwidth_code`
- `hal_bandwidth_hz`

For true 62.5 kHz TX, live status should show:

```text
hal_bandwidth_code: 3
hal_bandwidth_hz: 62500
```

The user confirmed the transmitted signal is visible as 62.5 kHz on the receiving device after this patch.

## Current 62.5 kHz Split

The current architecture is:

- SX1302/SX1250 path for TX.
- SX1261 companion path for RX.
- No SX1302 RX IF chain is configured for 62.5 kHz.
- Status reports `rx_backend: "sx1261"` in 62.5 kHz mode.

## RX Debugging Findings

Several blockers were found and fixed while proving RX:

- Automatic SX1261 spectral scans from `GetNoiseFloor` could steal the SX1261 away from LoRa packet RX. Automatic scans are disabled in 62.5 kHz packet RX mode.
- A stale Python guard skipped all `mc_lgw_receive()` calls when bandwidth was 62.5 kHz. That guard was removed.
- SX1261 preamble/header diagnostic IRQs were initially treated as failure conditions, causing RX to reset before `RX_DONE`. Preamble/header-valid are now diagnostic only.
- The C bridge exposes SX1261 RX diagnostics in status under `sx1261_rx_debug`.

Useful RX diagnostics include:

- `receive_entry_count`
- `sx1261_branch_count`
- `poll_count`
- `preamble_count`
- `syncword_count`
- `header_valid_count`
- `header_err_count`
- `crc_err_count`
- `rx_done_count`
- `last_irq_flags`

## Permissive Forwarding

MeshCore traffic is not treated like a LoRaWAN packet. If SX1261 returns `RX_DONE` with CRC error, the bridge now reads the buffer and marks it as `STAT_CRC_BAD` instead of dropping it in C.

The daemon config currently uses:

```yaml
crc:
  forward_unknown_crc: true
```

With that setting, bad-CRC payloads that have actual bytes are forwarded to pyMC. This cannot bypass SX1261 PHY header decode: if the chip only reports `HEADER_ERR`, there is no decoded payload buffer to forward.

## Current Live Test Values

The live 62.5 kHz test configuration has used:

```text
frequency: 916575000
bandwidth: 62500
spreading factor: 7
coding rate: 4/8
tx power: 26 dBm
sync word: 0x1424
invert_iq: false
```

Keep `hal_bandwidth_code` visible until 62.5 kHz TX regressions are unlikely.
