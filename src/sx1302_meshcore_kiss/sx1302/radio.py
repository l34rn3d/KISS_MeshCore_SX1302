"""SX1302/WM1302 LoRa concentrator driver.

Implements the local :class:`~sx1302_meshcore_kiss.sx1302.base.LoRaRadio` interface using the
pure-Python SX1302 HAL (no C library required).

Hardware notes:

- SX1302 normally exposes 125 / 250 / 500 kHz bandwidth.  ``62_500`` Hz is
  allowed as an experimental trick mode and is encoded through the 125 kHz HAL
  bandwidth setting until the lower-level trick path is fully modeled.
- SX1261 spectral-scan is optional.  If ``sx1261_spi_path`` is not provided,
  ``get_last_rssi()`` returns the fixed default −120 dBm.  After 3 consecutive
  failed scans the SX1261 path is disabled automatically.
- TX power up to 26 dBm is supported (higher than SX1262's 22 dBm).

Example::

    from sx1302_meshcore_kiss.sx1302.radio import SX1302Radio

    radio = SX1302Radio(
        frequency=869_618_000,
        bandwidth=125_000,
        spreading_factor=8,
        com_path="/dev/spidev0.0",
        sx1261_spi_path="/dev/spidev0.1",
    )
    radio.begin()
    await radio.send(b"hello mesh")
"""

import asyncio
import logging
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .base import LoRaRadio
from .hal import (
    BW_125KHZ,
    BW_250KHZ,
    BW_500KHZ,
    CR_LORA_4_5,
    CR_LORA_4_6,
    CR_LORA_4_7,
    CR_LORA_4_8,
    DR_LORA_SF7,
    DR_LORA_SF8,
    DR_LORA_SF9,
    DR_LORA_SF10,
    DR_LORA_SF11,
    DR_LORA_SF12,
    LGW_HAL_SUCCESS,
    MOD_LORA,
    RADIO_TYPE_SX1250,
    RX_OFF,
    RX_ON,
    RX_STATUS,
    RX_STATUS_UNKNOWN,
    RX_SUSPENDED,
    TX_EMITTING,
    TX_FREE,
    TX_IMMEDIATE,
    TX_OFF,
    TX_SCHEDULED,
    TX_STATUS,
    TX_STATUS_UNKNOWN,
    Sx1261Config,
    Sx1261LbtConfig,
    lgw_board_setconf,
    lgw_receive,
    lgw_rxif_setconf,
    lgw_rxrf_setconf,
    lgw_send,
    lgw_sx1261_setconf,
    lgw_start,
    lgw_status,
    lgw_stop,
    lgw_time_on_air,
)

logger = logging.getLogger(__name__)


class DutyCycleExceededError(RuntimeError):
    """Raised when duty-cycle enforcement blocks a transmission."""

    def __init__(self, message: str, duty_cycle_status: Dict[str, Any]) -> None:
        super().__init__(message)
        self.duty_cycle_status = duty_cycle_status


# Keep old alias for any callers that used IMMEDIATE
IMMEDIATE = TX_IMMEDIATE
LGW_RADIO_TYPE_SX1250 = RADIO_TYPE_SX1250

_CR_MAP: Dict[int, int] = {
    5: CR_LORA_4_5,
    6: CR_LORA_4_6,
    7: CR_LORA_4_7,
    8: CR_LORA_4_8,
}

_SF_MAP: Dict[int, int] = {
    7: DR_LORA_SF7,
    8: DR_LORA_SF8,
    9: DR_LORA_SF9,
    10: DR_LORA_SF10,
    11: DR_LORA_SF11,
    12: DR_LORA_SF12,
}


class SX1302Radio(LoRaRadio):
    _active_instance: Optional["SX1302Radio"] = None

    """SX1302/WM1302 concentrator implementing :class:`LoRaRadio`.

    Args:
        frequency: Centre frequency in Hz (default: 869 618 000).
        spreading_factor: LoRa SF 7–12 (default: 8).
        bandwidth: Bandwidth in Hz — 62 500 experimental trick mode, 125 000,
            250 000, or 500 000.
        coding_rate: Denominator of 4/N, 5–8 (default: 8 → 4/8).
        preamble_length: Preamble symbols (default: 17).
        tx_power: TX power in dBm, max 26 (default: 14).
        sync_word: LoRa sync word (default: 0x34E4).
        com_path: SPI device path for SX1302 (default: ``"/dev/spidev0.0"``).
        sx1261_spi_path: SPI device path for SX1261 noise-floor companion.
            Pass ``None`` to disable noise-floor measurement.

    Example::

        radio = SX1302Radio(
            frequency=915_800_000,
            bandwidth=250_000,
            spreading_factor=11,
            com_path="/dev/spidev0.0",
        )
        radio.begin()
    """

    def __init__(
        self,
        frequency: int = 869_618_000,
        spreading_factor: int = 8,
        bandwidth: int = 125_000,
        coding_rate: int = 8,
        preamble_length: int = 17,
        tx_power: int = 14,
        sync_word: int = 0x34E4,
        com_path: str = "/dev/spidev0.0",
        sx1261_spi_path: Optional[str] = None,
        duty_cycle_limit: float = 0.01,
        duty_cycle_window_s: float = 3600.0,
        duty_cycle_enforcement: str = "raise",
        reset_enabled: bool = True,
        reset_required: bool = False,
        gpio_chip: str = "gpiochip0",
        reset_script_path: Optional[str] = None,
        power_enable_pin: Optional[int] = 18,
        sx1302_reset_pin: Optional[int] = 17,
        sx1261_reset_pin: Optional[int] = 5,
        adc_reset_pin: Optional[int] = 13,
    ) -> None:
        self.frequency = self._validate_frequency(frequency)
        self.spreading_factor = self._validate_spreading_factor(spreading_factor)
        self.bandwidth = self._validate_bandwidth(bandwidth)
        self.coding_rate = self._validate_coding_rate(coding_rate)
        self.preamble_length = preamble_length
        self.tx_power = self._validate_tx_power(tx_power)
        self.sync_word = sync_word
        self.com_path = com_path
        self.sx1261_spi_path = sx1261_spi_path
        self.reset_enabled = bool(reset_enabled)
        self.reset_required = bool(reset_required)
        self.gpio_chip = gpio_chip
        self.reset_script_path = reset_script_path
        self.power_enable_pin = power_enable_pin
        self.sx1302_reset_pin = sx1302_reset_pin
        self.sx1261_reset_pin = sx1261_reset_pin
        self.adc_reset_pin = adc_reset_pin
        self.duty_cycle_limit = self._validate_duty_cycle_limit(duty_cycle_limit)
        self.duty_cycle_window_s = self._validate_duty_cycle_window(duty_cycle_window_s)
        self.duty_cycle_enforcement = self._validate_duty_cycle_enforcement(duty_cycle_enforcement)
        self._tx_airtime_log: List[tuple[float, int]] = []

        self._is_started: bool = False
        self._rx_callback: Optional[Callable] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._rx_running: bool = False
        self._rx_queue: Optional[asyncio.Queue] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self._last_rssi: int = -120
        self._last_snr: float = 0.0
        self._last_cad: Dict[str, Any] = {
            "busy": False,
            "source": "never_run",
            "packets_seen": 0,
            "noise_floor": self._last_rssi,
        }
        self._last_lbt: Dict[str, Any] = {
            "attempts": 0,
            "channel_busy": False,
            "backoff_delays_ms": [],
        }
        self._lbt_enabled: bool = False

        self._sx1261_radio: Optional[Any] = None
        self._sx1261_config: Optional[Sx1261Config] = None
        self._sx1261_active: bool = False
        self._sx1261_abort_count: int = 0
        self._last_noise_scan: float = 0.0

        logger.info(
            "SX1302Radio init: freq=%dHz SF=%d BW=%dHz CR=4/%d preamble=%d",
            frequency,
            spreading_factor,
            bandwidth,
            coding_rate,
            preamble_length,
        )

    # ------------------------------------------------------------------
    # LoRaRadio abstract method implementations
    # ------------------------------------------------------------------

    def begin(self) -> None:
        """Initialise and start the SX1302 concentrator.

        Raises:
            RuntimeError: If any HAL call fails.

        Example::

            radio = SX1302Radio()
            radio.begin()  # raises RuntimeError on failure
        """
        if self._is_started:
            logger.warning("SX1302Radio.begin() called on already-started radio")
            return

        try:
            self._loop = asyncio.get_running_loop()
            self._rx_queue = asyncio.Queue()
        except RuntimeError:
            self._loop = None
            self._rx_queue = None

        try:
            lgw_stop()
            time.sleep(0.1)
        except Exception:
            pass

        self._reset_concentrator()
        self._configure_board()
        self._configure_rf_chain()
        self._configure_if_chain()
        self._configure_sx1261()

        ret = lgw_start()
        if ret != LGW_HAL_SUCCESS:
            raise RuntimeError(f"lgw_start() failed (code={ret})")

        self._is_started = True
        logger.info("SX1302 concentrator started")

        self._rx_running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True, name="sx1302-rx")
        self._rx_thread.start()

    async def send(
        self,
        data: bytes,
        *,
        lbt_max_attempts: int = 3,
        lbt_backoff_base: float = 0.05,
        lbt_cad_timeout: float = 0.5,
    ) -> Dict[str, Any]:
        """Transmit a LoRa packet.

        Args:
            data: Raw payload bytes (max 255).

        Returns:
            Metadata dict with keys ``freq_hz``, ``sf``, ``bw``, ``size``.

        Raises:
            RuntimeError: If the concentrator is not started or lgw_send fails.

        Example::

            meta = await radio.send(b"hello")
            print(meta["size"])
        """
        if not self._is_started:
            raise RuntimeError("SX1302Radio.send() called before begin()")

        duty_cycle_delay_s = await self._enforce_duty_cycle_before_send(data)

        lbt_attempts = 0
        lbt_channel_busy = False
        lbt_backoff_delays_ms: List[int] = []
        if self._lbt_enabled:
            max_attempts = max(1, int(lbt_max_attempts))
            for attempt in range(1, max_attempts + 1):
                lbt_attempts = attempt
                lbt_channel_busy = await self.perform_cad(timeout=lbt_cad_timeout)
                if not lbt_channel_busy:
                    break
                if attempt >= max_attempts:
                    self._last_lbt = {
                        "attempts": lbt_attempts,
                        "channel_busy": True,
                        "backoff_delays_ms": lbt_backoff_delays_ms,
                    }
                    raise RuntimeError(
                        f"SX1302Radio.send() blocked by LBT: channel busy after {lbt_attempts} attempts"
                    )
                delay_s = max(0.0, float(lbt_backoff_base)) * (2 ** (attempt - 1))
                lbt_backoff_delays_ms.append(int(round(delay_s * 1000)))
                await asyncio.sleep(delay_s)

        self._last_lbt = {
            "attempts": lbt_attempts,
            "channel_busy": lbt_channel_busy,
            "backoff_delays_ms": lbt_backoff_delays_ms,
        }

        pkt = {
            "freq_hz": self.frequency,
            "tx_mode": TX_IMMEDIATE,
            "rf_chain": 0,
            "rf_power": self.tx_power,
            "modulation": MOD_LORA,
            "bandwidth": self._map_bandwidth(self.bandwidth),
            "datarate": self._map_spreading_factor(self.spreading_factor),
            "coderate": self._map_coding_rate(self.coding_rate),
            "preamble": self.preamble_length,
            "no_crc": False,
            "no_header": False,
            "payload": data,
        }

        logger.info(
            "SX1302 lgw_send: payload_len=%d freq=%d bw=%d sf=%d cr=4/%d power=%d",
            len(data), self.frequency, self.bandwidth, self.spreading_factor, self.coding_rate, self.tx_power,
        )
        ret = lgw_send(pkt)
        if ret != LGW_HAL_SUCCESS:
            logger.error("SX1302 lgw_send failed: code=%s payload_len=%d", ret, len(data))
            raise RuntimeError(f"lgw_send() failed (code={ret})")

        status_code, tx_state = lgw_status(0, TX_STATUS)
        if status_code != LGW_HAL_SUCCESS:
            tx_state = TX_STATUS_UNKNOWN

        airtime_ms = self.get_airtime(len(data))
        self.record_tx_airtime(airtime_ms)
        duty_cycle = self.get_duty_cycle_status()
        duty_cycle["enforcement"] = self.duty_cycle_enforcement
        duty_cycle["delayed_s"] = duty_cycle_delay_s

        return {
            "success": True,
            "freq_hz": self.frequency,
            "sf": self.spreading_factor,
            "bw": self.bandwidth,
            "size": len(data),
            "airtime_ms": airtime_ms,
            "tx_status": self._tx_status_name(tx_state),
            "tx_status_code": tx_state,
            "lbt_enabled": self._lbt_enabled,
            "lbt_attempts": lbt_attempts,
            "lbt_channel_busy": lbt_channel_busy,
            "lbt_backoff_delays_ms": lbt_backoff_delays_ms,
            "duty_cycle": duty_cycle,
        }

    async def wait_for_rx(self) -> bytes:
        """Wait for the next valid received packet.

        Returns:
            Raw payload bytes.

        Raises:
            RuntimeError: If called before ``begin()`` or outside an async context.

        Example::

            payload = await radio.wait_for_rx()
        """
        if self._rx_queue is None:
            raise RuntimeError("wait_for_rx() requires an active asyncio event loop at begin()")
        return await self._rx_queue.get()

    def sleep(self) -> None:
        """No-op: SX1302 has no low-power sleep mode.

        Call :meth:`cleanup` to stop it.
        """
        logger.warning("SX1302Radio.sleep() called — SX1302 has no sleep mode; ignoring")

    def get_last_rssi(self) -> int:
        """Return the last measured noise floor from the SX1261.

        Returns:
            Noise floor in dBm.  Returns −120 if no reading available.

        Example::

            rssi = radio.get_last_rssi()
        """
        return self._last_rssi

    def get_last_signal_rssi(self) -> Optional[int]:
        """Return RSSI for the last observed signal/packet if available.

        SX1302 packet RSSI and SX1261 noise-floor RSSI are not yet tracked as
        separate values in this wrapper, so this currently mirrors
        :meth:`get_last_rssi` for API parity with other daemon radios.
        """
        return self._last_rssi

    def get_last_snr(self) -> float:
        """Return SNR from the last received packet.

        Returns:
            SNR in dB.  Returns 0.0 before any packet is received.

        Example::

            snr = radio.get_last_snr()
        """
        return self._last_snr

    # ------------------------------------------------------------------
    # Additional public API
    # ------------------------------------------------------------------

    def set_rx_callback(self, callback: Callable) -> None:
        """Register a callback invoked on each valid received packet.

        Args:
            callback: Callable accepting ``bytes``.

        Example::

            async def on_rx(data: bytes) -> None:
                print("received:", data)

            radio.set_rx_callback(on_rx)
        """
        self._rx_callback = callback

    def get_noise_floor(self) -> Optional[float]:
        """Return noise floor in dBm, or ``None`` if SX1261 is not configured.

        Returns:
            Noise floor in dBm, or ``None``.
        """
        if not self.sx1261_spi_path:
            return None
        return float(self._last_rssi)

    def get_airtime(self, payload: Any) -> int:
        """Return LoRa time-on-air for current radio settings in milliseconds."""
        if isinstance(payload, (bytes, bytearray, memoryview)):
            payload_len = len(payload)
        else:
            payload_len = int(payload)
        if not (0 <= payload_len <= 255):
            raise ValueError("payload length must be between 0 and 255 bytes")

        pkt = {
            "modulation": MOD_LORA,
            "bandwidth": self._map_bandwidth(self.bandwidth),
            "bandwidth_hz": self.bandwidth,
            "datarate": self._map_spreading_factor(self.spreading_factor),
            "coderate": self._map_coding_rate(self.coding_rate),
            "preamble": self.preamble_length,
            "no_crc": False,
            "no_header": False,
            "size": payload_len,
            "payload": bytes(payload_len),
        }
        return lgw_time_on_air(pkt)

    def record_tx_airtime(self, airtime_ms: int) -> None:
        """Record successful TX airtime for duty-cycle accounting."""
        airtime = max(0, int(airtime_ms))
        self._tx_airtime_log.append((time.monotonic(), airtime))
        self._prune_tx_airtime_log()

    def get_duty_cycle_status(self, payload: Any = None) -> Dict[str, Any]:
        """Return duty-cycle usage and next legal TX timing.

        This method only reports accounting state. Enforcement/delay policy is
        intentionally left to the later duty-cycle enforcement step.
        """
        self._prune_tx_airtime_log()
        now = time.monotonic()
        allowed_ms = int(self.duty_cycle_limit * self.duty_cycle_window_s * 1000)
        used_ms = sum(airtime for _, airtime in self._tx_airtime_log)
        remaining_ms = max(0, allowed_ms - used_ms)

        candidate_airtime_ms = None
        if payload is not None:
            candidate_airtime_ms = self.get_airtime(payload)
            tx_legal_now = candidate_airtime_ms <= remaining_ms
        else:
            tx_legal_now = remaining_ms > 0

        next_legal_at = now
        next_tx_delay_s = 0.0
        if not tx_legal_now:
            projected_used = used_ms
            for timestamp, airtime in sorted(self._tx_airtime_log, key=lambda item: item[0]):
                projected_used -= airtime
                if payload is None:
                    projected_remaining = max(0, allowed_ms - projected_used)
                    legal_after_expiry = projected_remaining > 0
                else:
                    legal_after_expiry = candidate_airtime_ms <= max(0, allowed_ms - projected_used)
                if legal_after_expiry:
                    next_legal_at = timestamp + self.duty_cycle_window_s
                    next_tx_delay_s = max(0.0, next_legal_at - now)
                    break
            else:
                next_legal_at = now + self.duty_cycle_window_s
                next_tx_delay_s = self.duty_cycle_window_s

        status = {
            "limit": self.duty_cycle_limit,
            "window_s": self.duty_cycle_window_s,
            "allowed_airtime_ms": allowed_ms,
            "used_airtime_ms": used_ms,
            "remaining_airtime_ms": remaining_ms,
            "usage_fraction": 0.0 if allowed_ms <= 0 else used_ms / allowed_ms,
            "tx_count": len(self._tx_airtime_log),
            "tx_legal_now": tx_legal_now,
            "next_tx_legal_at": next_legal_at,
            "next_tx_delay_s": next_tx_delay_s,
        }
        if candidate_airtime_ms is not None:
            status["candidate_airtime_ms"] = candidate_airtime_ms
        return status

    def _prune_tx_airtime_log(self) -> None:
        cutoff = time.monotonic() - self.duty_cycle_window_s
        self._tx_airtime_log = [entry for entry in self._tx_airtime_log if entry[0] > cutoff]

    async def _enforce_duty_cycle_before_send(self, payload: Any) -> float:
        """Apply configured duty-cycle policy before touching the HAL TX path."""
        status = self.get_duty_cycle_status(payload=payload)
        if status["tx_legal_now"]:
            return 0.0

        if self.duty_cycle_enforcement in {"disabled", "off", "none"}:
            return 0.0

        if self.duty_cycle_enforcement == "delay":
            delay_s = float(status["next_tx_delay_s"])
            if delay_s > 0.0:
                await asyncio.sleep(delay_s)
            return delay_s

        raise DutyCycleExceededError(
            "SX1302Radio.send() blocked by duty cycle: "
            f"next legal TX in {status['next_tx_delay_s']:.3f}s",
            status,
        )

    def cleanup(self) -> None:
        """Stop the RX thread and shut down the concentrator.

        Safe to call multiple times.

        Example::

            radio.cleanup()
        """
        self._rx_running = False
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=2.0)
        if self._sx1261_radio is not None:
            try:
                self._sx1261_radio.cleanup()
            except Exception:
                pass
            self._sx1261_radio = None
        self._sx1261_active = False
        if self._is_started:
            lgw_stop()
            self._is_started = False
            logger.info("SX1302 concentrator stopped")
        if SX1302Radio._active_instance is self:
            SX1302Radio._active_instance = None

    def get_status(self) -> Dict[str, Any]:
        """Return radio status information without touching hardware unless started."""
        tx_status_code = TX_STATUS_UNKNOWN
        rx_status_code = RX_STATUS_UNKNOWN
        tx_status_available = False
        rx_status_available = False

        if self._is_started:
            tx_ret, tx_state = lgw_status(0, TX_STATUS)
            if tx_ret == LGW_HAL_SUCCESS:
                tx_status_code = tx_state
                tx_status_available = True
            rx_ret, rx_state = lgw_status(0, RX_STATUS)
            if rx_ret == LGW_HAL_SUCCESS:
                rx_status_code = rx_state
                rx_status_available = True

        return {
            "initialized": self._is_started,
            "started": self._is_started,
            "frequency": self.frequency,
            "tx_power": self.tx_power,
            "spreading_factor": self.spreading_factor,
            "bandwidth": self.bandwidth,
            "coding_rate": self.coding_rate,
            "preamble_length": self.preamble_length,
            "sync_word": self.sync_word,
            "com_path": self.com_path,
            "sx1261_spi_path": self.sx1261_spi_path,
            "last_rssi": self._last_rssi,
            "last_snr": self._last_snr,
            "lbt_enabled": self._lbt_enabled,
            "last_cad": dict(self._last_cad),
            "last_lbt": dict(self._last_lbt),
            "duty_cycle": self.get_duty_cycle_status(),
            "duty_cycle_enforcement": self.duty_cycle_enforcement,
            "rx_running": self._rx_running,
            "rx_thread_alive": bool(self._rx_thread and self._rx_thread.is_alive()),
            "rx_queue_available": self._rx_queue is not None,
            "sx1261_configured": self.sx1261_spi_path is not None,
            "sx1261_active": self._sx1261_active or self._sx1261_radio is not None,
            "sx1261_config": self._sx1261_status_config(),
            "last_noise_scan": self._last_noise_scan,
            "hardware_ready": self._is_started,
            "tx_status": self._tx_status_name(tx_status_code),
            "tx_status_code": tx_status_code,
            "tx_status_available": tx_status_available,
            "rx_status": self._rx_status_name(rx_status_code),
            "rx_status_code": rx_status_code,
            "rx_status_available": rx_status_available,
        }

    def get_health_diagnostics(self) -> Dict[str, Any]:
        """Return structured health diagnostics for the radio wrapper."""
        status = self.get_status()
        issues: List[str] = []

        if not status["started"]:
            issues.append("not_started")
        if status["started"] and not status["rx_running"]:
            issues.append("rx_loop_not_running")
        if status["started"] and not status["rx_thread_alive"]:
            issues.append("rx_thread_not_alive")
        if status["started"] and not status["rx_queue_available"]:
            issues.append("rx_queue_missing")
        if status["started"] and not status["tx_status_available"]:
            issues.append("tx_status_unavailable")
        if status["started"] and not status["rx_status_available"]:
            issues.append("rx_status_unavailable")
        if status["sx1261_configured"] and not status["sx1261_active"]:
            issues.append("sx1261_inactive")

        return {
            "healthy": not issues,
            "issues": issues,
            "status": status,
        }

    def check_radio_health(self) -> bool:
        """Return True when diagnostics report no health issues."""
        return bool(self.get_health_diagnostics()["healthy"])

    def set_frequency(self, frequency: int) -> bool:
        """Set operating frequency for subsequent configuration/TX."""
        self.frequency = self._validate_frequency(frequency)
        return True

    def set_freq(self, frequency: int) -> bool:
        """Alias for :meth:`set_frequency`."""
        return self.set_frequency(frequency)

    def set_tx_power(self, power: int) -> bool:
        """Set TX power in dBm for subsequent TX packets."""
        self.tx_power = self._validate_tx_power(power)
        return True

    def set_power(self, power: int) -> bool:
        """Alias for :meth:`set_tx_power`."""
        return self.set_tx_power(power)

    def set_spreading_factor(self, sf: int) -> bool:
        """Set LoRa spreading factor for subsequent configuration/TX."""
        self.spreading_factor = self._validate_spreading_factor(sf)
        return True

    def set_sf(self, sf: int) -> bool:
        """Alias for :meth:`set_spreading_factor`."""
        return self.set_spreading_factor(sf)

    def set_bandwidth(self, bw: int) -> bool:
        """Set LoRa bandwidth in Hz for subsequent configuration/TX."""
        self.bandwidth = self._validate_bandwidth(bw)
        return True

    def set_bw(self, bw: int) -> bool:
        """Alias for :meth:`set_bandwidth`."""
        return self.set_bandwidth(bw)

    def set_lbt_enabled(self, enabled: bool) -> None:
        """Enable or disable software LBT policy flag.

        Actual software LBT/backoff enforcement is implemented in a later step;
        this method makes the capability visible to higher-level code now.
        """
        self._lbt_enabled = bool(enabled)

    def get_lbt_enabled(self) -> bool:
        """Return whether software LBT policy is enabled."""
        return self._lbt_enabled

    def is_channel_busy(self) -> bool:
        """Return the last best-effort channel busy state.

        Call :meth:`perform_cad` to refresh this cached value. The decision is
        a software approximation, not SX1262-style native CAD.
        """
        return bool(self._last_cad.get("busy", False))

    async def perform_cad(
        self,
        timeout: float = 0.5,
        poll_interval: float = 0.01,
        rssi_threshold: int = -90,
        max_pkt: int = 8,
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        """Perform best-effort software CAD.

        SX1302 does not expose the same native LoRa CAD primitive as SX1262.
        This approximation checks for packet activity in a short RX window and,
        when the SX1261 companion scaffold is active, falls back to the current
        companion noise-floor/RSSI estimate.
        """
        if not self._is_started:
            raise RuntimeError("SX1302Radio.perform_cad() called while not started")

        deadline = time.monotonic() + max(0.0, float(timeout))
        packets_seen = 0

        while True:
            packets = lgw_receive(max_pkt=max_pkt)
            active_packets = [pkt for pkt in packets if int(pkt.get("size", 0) or 0) > 0]
            packets_seen += len(active_packets)
            if active_packets:
                first = active_packets[0]
                if "rssi" in first:
                    self._last_rssi = int(first.get("rssi", self._last_rssi))
                if "snr" in first:
                    self._last_snr = float(first.get("snr", self._last_snr))
                self._last_cad = {
                    "busy": True,
                    "source": "rx_packet",
                    "packets_seen": packets_seen,
                    "noise_floor": self._last_rssi,
                }
                return True

            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(max(0.0, float(poll_interval)))

        sx1261_active = self._sx1261_active or self._sx1261_radio is not None
        if sx1261_active and self._last_rssi >= int(rssi_threshold):
            self._last_cad = {
                "busy": True,
                "source": "sx1261_rssi",
                "packets_seen": packets_seen,
                "noise_floor": self._last_rssi,
            }
            return True

        self._last_cad = {
            "busy": False,
            "source": "idle",
            "packets_seen": packets_seen,
            "noise_floor": self._last_rssi,
        }
        return False

    @classmethod
    def get_instance(cls, **kwargs: Any) -> "SX1302Radio":
        """Get the active instance or create one."""
        if cls._active_instance is None:
            cls._active_instance = cls(**kwargs)
        return cls._active_instance

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _reset_concentrator(self) -> None:
        """Pulse configured SX1302 GPIO reset lines.

        Defaults match common WM1302 on Raspberry Pi / CM4 wiring:
            18 — power enable
            17 — SX1302 reset
             5 — SX1261 reset (spectral scan companion)
            13 — ADC reset

        Set ``reset_enabled=False`` to skip GPIO reset completely. Set any pin
        to ``None`` to skip that line. Missing/failed ``gpioset`` is non-fatal
        by default for import/dev safety; set ``reset_required=True`` to make a
        reset failure abort ``begin()``.
        """
        if not self.reset_enabled:
            logger.info("SX1302 GPIO reset disabled by configuration")
            return

        if self.reset_script_path:
            try:
                script_path = Path(self.reset_script_path)
                logger.info("SX1302 GPIO reset via script: %s", script_path)
                result = subprocess.run([str(script_path)], check=False, capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    time.sleep(0.5)
                    logger.debug("GPIO reset script complete")
                    return
                message = f"GPIO reset script failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
                if self.reset_required:
                    raise RuntimeError(message)
                logger.warning("%s; falling back to gpioset sequence", message)
            except Exception as exc:
                message = f"GPIO reset script failed: {exc}"
                if self.reset_required:
                    raise RuntimeError(message) from exc
                logger.warning("%s; falling back to gpioset sequence", message)

        logger.info("SX1302 GPIO reset sequence")
        gpioset = ["gpioset", "-m", "time", "-u", "100000", self.gpio_chip]
        seq = []
        if self.power_enable_pin is not None:
            seq.append(f"{self.power_enable_pin}=1")
        if self.sx1302_reset_pin is not None:
            seq.extend([f"{self.sx1302_reset_pin}=1", f"{self.sx1302_reset_pin}=0"])
        if self.sx1261_reset_pin is not None:
            seq.extend([f"{self.sx1261_reset_pin}=0", f"{self.sx1261_reset_pin}=1"])
        if self.adc_reset_pin is not None:
            seq.extend([f"{self.adc_reset_pin}=0", f"{self.adc_reset_pin}=1"])

        try:
            for arg in seq:
                subprocess.run(gpioset + [arg], check=False, capture_output=True)
                time.sleep(0.01)
            time.sleep(0.5)
            logger.debug("GPIO reset complete")
        except FileNotFoundError as exc:
            message = "gpioset not found — skipping GPIO reset (install gpiod)"
            if self.reset_required:
                raise RuntimeError(message) from exc
            logger.warning(message)
        except Exception as exc:
            message = f"GPIO reset failed: {exc}"
            if self.reset_required:
                raise RuntimeError(message) from exc
            logger.warning("%s (non-fatal)", message)

    def _configure_board(self) -> None:
        ret = lgw_board_setconf(
            {
                "lorawan_public": False,
                "clksrc": 0,
                "spi_path": self.com_path,
            }
        )
        if ret != LGW_HAL_SUCCESS:
            raise RuntimeError(
                f"lgw_board_setconf() failed (code={ret}). Check SPI device: {self.com_path}"
            )
        logger.debug("Board configuration OK")

    def _configure_rf_chain(self) -> None:
        ret = lgw_rxrf_setconf(
            0,
            {
                "enable": True,
                "freq_hz": self.frequency,
                "rssi_offset": -166.0,
                "type": RADIO_TYPE_SX1250,
                "tx_enable": True,
                "single_input_mode": False,
            },
        )
        if ret != LGW_HAL_SUCCESS:
            raise RuntimeError(f"lgw_rxrf_setconf() failed (code={ret})")
        logger.debug("RF chain 0 configuration OK")

    def _configure_if_chain(self) -> None:
        # IF chains 0-7: multi-SF (125 kHz only).
        # IF chain 8: LoRa standard modem (supports 125/250/500 kHz).
        if_chain_num = 8 if self.bandwidth != 125_000 else 0

        ret = lgw_rxif_setconf(
            if_chain_num,
            {
                "enable": True,
                "rf_chain": 0,
                "freq_hz": 0,
                "bandwidth": self._map_bandwidth(self.bandwidth),
                "datarate": self._map_spreading_factor(self.spreading_factor),
            },
        )
        if ret != LGW_HAL_SUCCESS:
            raise RuntimeError(f"lgw_rxif_setconf() failed (code={ret})")
        logger.debug("IF chain %d configuration OK", if_chain_num)

    def _configure_sx1261(self) -> None:
        if not self.sx1261_spi_path:
            self._sx1261_config = None
            self._sx1261_active = False
            return

        self._sx1261_config = Sx1261Config(
            enable=True,
            spi_path=self.sx1261_spi_path,
            rssi_offset=0.0,
            spectral_scan_enable=True,
            lbt=Sx1261LbtConfig(enable=False, rssi_target=-80, channels=[]),
        )
        ret = lgw_sx1261_setconf(self._sx1261_config)
        if ret != LGW_HAL_SUCCESS:
            logger.warning("SX1261 companion configuration failed (code=%s)", ret)
            self._sx1261_active = False
            return

        self._sx1261_active = True
        logger.info("SX1261 companion configured for future noise/CAD/LBT support")

    def _sx1261_status_config(self) -> Optional[Dict[str, Any]]:
        """Return serializable SX1261 config for status output."""
        if self._sx1261_config is None:
            return None
        return {
            "enable": self._sx1261_config.enable,
            "spi_path": self._sx1261_config.spi_path,
            "rssi_offset": self._sx1261_config.rssi_offset,
            "spectral_scan_enable": self._sx1261_config.spectral_scan_enable,
            "lbt": {
                "enable": self._sx1261_config.lbt.enable,
                "rssi_target": self._sx1261_config.lbt.rssi_target,
                "channels": list(self._sx1261_config.lbt.channels),
            },
        }

    @staticmethod
    def _tx_status_name(state: int) -> str:
        return {
            TX_STATUS_UNKNOWN: "unknown",
            TX_OFF: "off",
            TX_FREE: "free",
            TX_SCHEDULED: "scheduled",
            TX_EMITTING: "emitting",
        }.get(state, "unknown")

    @staticmethod
    def _rx_status_name(state: int) -> str:
        return {
            RX_STATUS_UNKNOWN: "unknown",
            RX_OFF: "off",
            RX_ON: "on",
            RX_SUSPENDED: "suspended",
        }.get(state, "unknown")

    @staticmethod
    def _validate_frequency(frequency: int) -> int:
        if not (100_000_000 <= int(frequency) <= 1_000_000_000):
            raise ValueError("frequency must be between 100 MHz and 1 GHz")
        return int(frequency)

    @staticmethod
    def _validate_bandwidth(bw_hz: int) -> int:
        allowed = {62_500, 125_000, 250_000, 500_000}
        if int(bw_hz) not in allowed:
            raise ValueError("bandwidth must be one of 62500, 125000, 250000, or 500000 Hz")
        return int(bw_hz)

    @staticmethod
    def _validate_spreading_factor(sf: int) -> int:
        if int(sf) not in _SF_MAP:
            raise ValueError("spreading factor must be between 7 and 12")
        return int(sf)

    @staticmethod
    def _validate_coding_rate(cr: int) -> int:
        if int(cr) not in _CR_MAP:
            raise ValueError("coding rate must be one of 5, 6, 7, or 8")
        return int(cr)

    @staticmethod
    def _validate_tx_power(power: int) -> int:
        if not (0 <= int(power) <= 26):
            raise ValueError("TX power must be between 0 and 26 dBm")
        return int(power)

    @staticmethod
    def _validate_duty_cycle_limit(limit: float) -> float:
        value = float(limit)
        if not (0.0 < value <= 1.0):
            raise ValueError("duty_cycle_limit must be greater than 0 and no more than 1.0")
        return value

    @staticmethod
    def _validate_duty_cycle_window(window_s: float) -> float:
        value = float(window_s)
        if value <= 0.0:
            raise ValueError("duty_cycle_window_s must be greater than 0")
        return value

    @staticmethod
    def _validate_duty_cycle_enforcement(mode: str) -> str:
        value = str(mode).lower()
        allowed = {"raise", "delay", "disabled", "off", "none"}
        if value not in allowed:
            raise ValueError("duty_cycle_enforcement must be 'raise', 'delay', or 'disabled'")
        return value

    @staticmethod
    def _map_bandwidth(bw_hz: int) -> int:
        """Map bandwidth in Hz to SX1302 BW constant.

        Note:
            ``62_500`` Hz is allowed as an experimental trick mode but still
            maps to the 125 kHz HAL bandwidth code until lower-level register
            handling models the trick explicitly.

        Args:
            bw_hz: Bandwidth in Hz.

        Returns:
            One of BW_125KHZ, BW_250KHZ, BW_500KHZ.
        """
        if bw_hz <= 125_000:
            return BW_125KHZ
        if bw_hz <= 250_000:
            return BW_250KHZ
        return BW_500KHZ

    @staticmethod
    def _map_coding_rate(cr: int) -> int:
        return _CR_MAP.get(cr, CR_LORA_4_8)

    @staticmethod
    def _map_spreading_factor(sf: int) -> int:
        return _SF_MAP.get(sf, DR_LORA_SF8)

    # ------------------------------------------------------------------
    # Background RX thread
    # ------------------------------------------------------------------

    def _rx_loop(self) -> None:
        """Poll lgw_receive() in a background daemon thread."""
        while self._rx_running:
            try:
                packets: List[dict] = lgw_receive(max_pkt=8)
                for pkt in packets:
                    size = pkt.get("size", 0)
                    if size > 0:
                        self._last_snr = float(pkt.get("snr", 0.0))

                    if size > 0 and pkt.get("crc_error", True):
                        logger.debug("Dropped packet: bad CRC (size=%d)", size)
                        continue

                    if size > 0 and not pkt.get("crc_error", True):
                        payload = pkt.get("payload", b"")
                        self._dispatch_rx(payload)

                # Noise floor: sample SX1261 RSSI every 30 s
                if self._sx1261_radio is not None and (
                    time.time() - self._last_noise_scan >= 30.0
                ):
                    self._measure_noise_floor()
                    self._last_noise_scan = time.time()

            except Exception as exc:
                logger.error("RX loop error: %s", exc)

            time.sleep(0.01)

    def _dispatch_rx(self, payload: bytes) -> None:
        """Push ``payload`` to the queue and invoke any registered callback."""
        if self._rx_queue is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._rx_queue.put_nowait, payload)

        if self._rx_callback is not None and self._loop is not None:
            if asyncio.iscoroutinefunction(self._rx_callback):
                asyncio.run_coroutine_threadsafe(self._rx_callback(payload), self._loop)
            else:
                self._loop.call_soon_threadsafe(self._rx_callback, payload)
        elif self._rx_callback is not None:
            logger.warning("No event loop; calling RX callback synchronously")
            self._rx_callback(payload)

    def _measure_noise_floor(self) -> None:
        """Sample noise floor from the SX1261 companion."""
        if self._sx1261_radio is None:
            return
        try:
            rssi = self._sx1261_radio.get_last_rssi()
            if rssi != 0:
                self._last_rssi = rssi
                logger.debug("Noise floor: %d dBm", rssi)
        except Exception as exc:
            self._sx1261_abort_count += 1
            logger.debug("SX1261 RSSI read failed: %s", exc)
            if self._sx1261_abort_count >= 3:
                logger.warning("SX1261 failing repeatedly — disabling noise floor")
                try:
                    self._sx1261_radio.cleanup()
                except Exception:
                    pass
                self._sx1261_radio = None
