# Architecture

`sx1302-meshcore-kiss` is intentionally a modem shim, not a MeshCore router.

## Packet paths

### pyMC to RF

```text
pyMC KISS Data 0x00
  -> KISS decoder
  -> 1..255 byte payload validation
  -> MQTT tx/requested + dashboard ring event
  -> SX1302Adapter.transmit()
  -> MQTT tx/done or tx/error
  -> KISS TxDone 0xF8
```

### RF to pyMC

```text
SX1302Adapter.receive()
  -> CRC policy
  -> if good: KISS Data 0x00 + optional RxMeta 0xF9
  -> MQTT rx/good|rx/bad_crc|rx/unknown_crc
  -> dashboard ring event
```

## Persistence rule

Raw packet history is not stored to disk. The dashboard uses `collections.deque(maxlen=50)` via `PacketRingBuffer`.

## CRC rule

KISS has no CRC/FCS in this implementation. RF CRC is handled by the SX1302 driver/HAL and then enforced by daemon policy. Bad CRC RF packets are telemetry only and are not forwarded to pyMC.
