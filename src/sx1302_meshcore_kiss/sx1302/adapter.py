from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .metadata import RadioConfig, RxPacket, SX1302Stats, TxPacket, TxResult

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SX1302Adapter:
    def __init__(self, radio_factory: Optional[Callable[..., Any]] = None) -> None:
        self._radio_factory_explicit = radio_factory is not None
        if radio_factory is None:
            from sx1302_meshcore_kiss.sx1302.radio import SX1302Radio
            radio_factory = SX1302Radio
        self.radio_factory = radio_factory
        self.config = RadioConfig()
        self.radio: Any = None
        self._radio_signature: tuple[tuple[str, Any], ...] | None = None
        self._started = False
        self.receive_call_count = 0
        self.receive_waiting = False
        self.last_receive_error: str | None = None
        self.last_cad: dict[str, Any] = {"busy": False, "source": "never_run", "method": "none"}

    @staticmethod
    def _signature_value(value: Any) -> Any:
        if isinstance(value, dict):
            return tuple(sorted((k, SX1302Adapter._signature_value(v)) for k, v in value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(SX1302Adapter._signature_value(v) for v in value)
        if isinstance(value, set):
            return tuple(sorted(SX1302Adapter._signature_value(v) for v in value))
        return value

    @staticmethod
    def has_rf_config(config: RadioConfig) -> bool:
        return all(
            value is not None
            for value in (
                config.frequency_hz,
                config.bandwidth_hz,
                config.spreading_factor,
                config.coding_rate,
                config.tx_power_dbm,
            )
        )

    async def configure(self, config: RadioConfig) -> None:
        self.config = config
        if not self.has_rf_config(config):
            logger.info("SX1302 adapter waiting for complete host RF config before radio start")
            return
        logger.info(
            "SX1302 adapter configure: freq=%s bw=%s sf=%s cr=4/%s power=%s started=%s",
            config.frequency_hz, config.bandwidth_hz, config.spreading_factor, config.coding_rate, config.tx_power_dbm, self._started,
        )
        old_radio = self.radio
        radio_factory = self.radio_factory
        if not self._radio_factory_explicit and str(getattr(config, "backend", "python")).lower() in ("c_hal", "semtech_c_hal", "semtech"):
            from sx1302_meshcore_kiss.sx1302.c_hal_radio import SemtechCHalRadio
            radio_factory = SemtechCHalRadio
        kwargs = dict(
            frequency=int(config.frequency_hz),
            bandwidth=int(config.bandwidth_hz),
            spreading_factor=int(config.spreading_factor),
            coding_rate=int(config.coding_rate),
            tx_power=int(config.tx_power_dbm),
            preamble_length=config.preamble_len or 17,
            sync_word=config.sync_word,
            com_path=config.spi_device,
            sx1261_spi_path=config.sx1261_spi_path,
            reset_enabled=config.reset_enabled,
            reset_required=config.reset_required,
            gpio_chip=config.gpio_chip,
            reset_script_path=config.reset_script_path,
            reset_script_args=config.reset_script_args,
            reset_script_env=config.reset_script_env,
            power_enable_pin=config.power_enable_pin,
            sx1302_reset_pin=config.sx1302_reset_pin,
            sx1261_reset_pin=config.sx1261_reset_pin,
            adc_reset_pin=config.adc_reset_pin,
            duty_cycle_enforcement=config.duty_cycle_enforcement,
            lbt_enabled=config.lbt_enabled,
            lbt_rssi_threshold_dbm=config.lbt_rssi_threshold_dbm,
            lbt_max_attempts=config.lbt_max_attempts,
            lbt_backoff_ms=config.lbt_backoff_ms,
            lbt_cad_timeout_ms=config.lbt_cad_timeout_ms,
        )
        if str(getattr(config, "backend", "python")).lower() in ("c_hal", "semtech_c_hal", "semtech"):
            kwargs["c_hal_lib"] = config.c_hal_lib
            kwargs["lorawan_public"] = config.lorawan_public
            kwargs["implicit_header"] = config.implicit_header
            kwargs["invert_iq"] = config.invert_iq
        signature = tuple((key, self._signature_value(value)) for key, value in sorted(kwargs.items()))
        if self.radio is not None and signature == self._radio_signature:
            return
        self.radio = radio_factory(**kwargs)
        self._radio_signature = signature
        if old_radio is not None and old_radio is not self.radio and hasattr(old_radio, "cleanup"):
            old_radio.cleanup()
        if self._started:
            self.radio.begin()

    async def start(self) -> None:
        already_started = self._started
        self._started = True
        logger.info("SX1302 adapter start requested")
        if self.radio is None:
            await self.configure(self.config)
        if self.radio is not None and not already_started:
            self.radio.begin()

    async def stop(self) -> None:
        if self.radio is not None and hasattr(self.radio, "cleanup"):
            self.radio.cleanup()
        self._started = False

    async def transmit(self, packet: TxPacket) -> TxResult:
        if self.radio is None:
            raise RuntimeError("SX1302Adapter not configured")
        logger.info("SX1302 adapter transmit: payload_len=%d", len(packet.payload))
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
        self.receive_call_count += 1
        self.receive_waiting = True
        try:
            payload = await self.radio.wait_for_rx()
        except Exception as exc:
            self.last_receive_error = str(exc)
            raise
        finally:
            self.receive_waiting = False
        status = self.radio.get_status() if hasattr(self.radio, "get_status") else {}
        rssi = self.radio.get_last_signal_rssi() if hasattr(self.radio, "get_last_signal_rssi") else None
        snr = self.radio.get_last_snr() if hasattr(self.radio, "get_last_snr") else None
        raw_status = int(getattr(self.radio, "_last_rx", None).status) if getattr(self.radio, "_last_rx", None) is not None else None
        return RxPacket(
            payload=bytes(payload),
            crc_ok=False if raw_status == 0x11 else True,
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

    def get_airtime(self, payload: Any) -> Optional[int]:
        if self.radio is not None and hasattr(self.radio, "get_airtime"):
            return int(self.radio.get_airtime(payload))
        return None

    def get_current_rssi(self) -> Optional[float]:
        if self.radio is not None and hasattr(self.radio, "get_last_signal_rssi"):
            return self.radio.get_last_signal_rssi()
        if self.radio is not None and hasattr(self.radio, "get_last_rssi"):
            return self.radio.get_last_rssi()
        return None

    def get_last_snr(self) -> Optional[float]:
        if self.radio is not None and hasattr(self.radio, "get_last_snr"):
            return self.radio.get_last_snr()
        status = self.radio.get_status() if self.radio is not None and hasattr(self.radio, "get_status") else {}
        value = status.get("last_snr") or status.get("snr")
        return float(value) if value is not None else None

    def get_noise_floor(self) -> Optional[float]:
        if self.radio is not None and hasattr(self.radio, "get_noise_floor"):
            return self.radio.get_noise_floor()
        return None

    def get_temperature(self) -> Optional[float]:
        if self.radio is not None and hasattr(self.radio, "get_temperature"):
            return self.radio.get_temperature()
        status = self.radio.get_status() if self.radio is not None and hasattr(self.radio, "get_status") else {}
        value = status.get("temperature_c") or status.get("temp_c")
        return float(value) if value is not None else None

    def is_channel_busy(self) -> bool:
        if self.radio is not None and hasattr(self.radio, "is_channel_busy"):
            return bool(self.radio.is_channel_busy())
        status = self.radio.get_status() if self.radio is not None and hasattr(self.radio, "get_status") else {}
        return str(status.get("tx_status", "")).lower() in {"emitting", "scheduled"}

    async def perform_cad(self, *, sym_num: int | None = None, det_peak: int | None = None, det_min: int | None = None, exit_mode: int | None = None, timeout: float = 0.5) -> bool:
        if self.radio is None:
            self.last_cad = {"busy": False, "source": "not_configured", "method": "none", "error": "radio not configured"}
            raise RuntimeError("SX1302Adapter not configured")
        if hasattr(self.radio, "perform_cad"):
            try:
                busy = bool(await self.radio.perform_cad(sym_num=sym_num, det_peak=det_peak, det_min=det_min, exit_mode=exit_mode, timeout=timeout))
                status = self.radio.get_status() if hasattr(self.radio, "get_status") else {}
                raw_cad = status.get("last_cad") if isinstance(status, dict) else None
                self.last_cad = {
                    "busy": busy,
                    "source": "backend_perform_cad",
                    "method": str((raw_cad or {}).get("method") or (raw_cad or {}).get("source") or "backend"),
                    "det_peak": det_peak,
                    "det_min": det_min,
                    "sym_num": sym_num,
                    "exit_mode": exit_mode,
                    "timeout_ms": int(float(timeout) * 1000.0),
                    "raw": dict(raw_cad or {}) if isinstance(raw_cad, dict) else {},
                }
                return busy
            except Exception as exc:
                self.last_cad = {"busy": False, "source": "backend_error", "method": "backend", "error": str(exc)}
                raise
        busy = self.is_channel_busy()
        self.last_cad = {
            "busy": bool(busy),
            "source": "channel_busy_fallback",
            "method": "sx1302_busy_fallback",
            "det_peak": det_peak,
            "det_min": det_min,
            "sym_num": sym_num,
            "exit_mode": exit_mode,
            "timeout_ms": int(float(timeout) * 1000.0),
        }
        return bool(busy)

    def get_cad_status(self) -> dict[str, Any]:
        return dict(self.last_cad)

    async def get_stats(self) -> SX1302Stats:
        status = self.radio.get_status() if self.radio is not None and hasattr(self.radio, "get_status") else {}
        return SX1302Stats(started=bool(status.get("started", False)), status=dict(status or {}))
