from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .metadata import RadioConfig, RxPacket, SX1302Stats, TxPacket, TxResult


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SX1302Adapter:
    def __init__(self, radio_factory: Optional[Callable[..., Any]] = None) -> None:
        if radio_factory is None:
            from sx1302_meshcore_kiss.sx1302.radio import SX1302Radio
            radio_factory = SX1302Radio
        self.radio_factory = radio_factory
        self.config = RadioConfig()
        self.radio: Any = None
        self._started = False

    async def configure(self, config: RadioConfig) -> None:
        old_radio = self.radio
        self.config = config
        self.radio = self.radio_factory(
            frequency=config.frequency_hz,
            bandwidth=config.bandwidth_hz,
            spreading_factor=config.spreading_factor,
            coding_rate=config.coding_rate,
            tx_power=config.tx_power_dbm,
            preamble_length=config.preamble_len or 17,
            sync_word=config.sync_word,
            com_path=config.spi_device,
            sx1261_spi_path=config.sx1261_spi_path,
            reset_enabled=config.reset_enabled,
            reset_required=config.reset_required,
            gpio_chip=config.gpio_chip,
            power_enable_pin=config.power_enable_pin,
            sx1302_reset_pin=config.sx1302_reset_pin,
            sx1261_reset_pin=config.sx1261_reset_pin,
            adc_reset_pin=config.adc_reset_pin,
            duty_cycle_enforcement=config.duty_cycle_enforcement,
        )
        if old_radio is not None and old_radio is not self.radio and hasattr(old_radio, "cleanup"):
            old_radio.cleanup()
        if self._started:
            self.radio.begin()

    async def start(self) -> None:
        if self.radio is None:
            await self.configure(self.config)
        if not self._started:
            self.radio.begin()
            self._started = True

    async def stop(self) -> None:
        if self.radio is not None and hasattr(self.radio, "cleanup"):
            self.radio.cleanup()
        self._started = False

    async def transmit(self, packet: TxPacket) -> TxResult:
        if self.radio is None:
            raise RuntimeError("SX1302Adapter not configured")
        started_at = _now_iso()
        try:
            meta = await self.radio.send(packet.payload)
            return TxResult(
                ok=bool(meta.get("success", True)) if isinstance(meta, dict) else True,
                error=None,
                airtime_ms=float(meta["airtime_ms"]) if isinstance(meta, dict) and meta.get("airtime_ms") is not None else None,
                started_at=started_at,
                completed_at=_now_iso(),
                raw_metadata=dict(meta or {}) if isinstance(meta, dict) else {},
            )
        except Exception as exc:
            return TxResult(ok=False, error=str(exc), airtime_ms=None, started_at=started_at, completed_at=_now_iso(), raw_metadata={})

    async def receive(self) -> RxPacket:
        if self.radio is None:
            raise RuntimeError("SX1302Adapter not configured")
        payload = await self.radio.wait_for_rx()
        status = self.radio.get_status() if hasattr(self.radio, "get_status") else {}
        rssi = self.radio.get_last_signal_rssi() if hasattr(self.radio, "get_last_signal_rssi") else None
        snr = self.radio.get_last_snr() if hasattr(self.radio, "get_last_snr") else None
        return RxPacket(
            payload=bytes(payload),
            crc_ok=True,
            frequency_hz=self.config.frequency_hz,
            bandwidth_hz=self.config.bandwidth_hz,
            spreading_factor=self.config.spreading_factor,
            coding_rate=self.config.coding_rate,
            rssi_dbm=float(rssi) if rssi is not None else None,
            snr_db=float(snr) if snr is not None else None,
            channel=None,
            concentrator_timestamp_us=None,
            raw_metadata=dict(status or {}),
        )

    async def get_stats(self) -> SX1302Stats:
        status = self.radio.get_status() if self.radio is not None and hasattr(self.radio, "get_status") else {}
        return SX1302Stats(started=bool(status.get("started", False)), status=dict(status or {}))
