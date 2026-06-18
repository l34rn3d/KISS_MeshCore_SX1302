from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RadioConfig:
    # RF settings are intentionally unset until the host sends MeshCore KISS
    # SetRadio/SetTxPower. The daemon config owns hardware/endpoint settings,
    # not channel parameters.
    frequency_hz: Optional[int] = None
    bandwidth_hz: Optional[int] = None
    spreading_factor: Optional[int] = None
    coding_rate: Optional[int] = None
    tx_power_dbm: Optional[int] = None
    preamble_len: Optional[int] = 17
    sync_word: Optional[int] = None
    implicit_header: bool = False
    invert_iq: bool = False
    spi_device: str = "/dev/spidev0.0"
    backend: str = "python"
    c_hal_lib: Optional[str] = None
    lorawan_public: bool = False
    sx1261_spi_path: Optional[str] = None
    reset_enabled: bool = True
    reset_required: bool = False
    gpio_chip: str = "gpiochip0"
    reset_script_path: Optional[str] = None
    reset_script_args: list[str] = field(default_factory=list)
    reset_script_env: dict[str, str] = field(default_factory=dict)
    power_enable_pin: Optional[int] = 18
    sx1302_reset_pin: Optional[int] = 17
    sx1261_reset_pin: Optional[int] = 5
    adc_reset_pin: Optional[int] = 13
    duty_cycle_enforcement: str = "raise"
    lbt_enabled: bool = False
    lbt_rssi_threshold_dbm: Optional[int] = None
    lbt_max_attempts: int = 3
    lbt_backoff_ms: int = 50
    lbt_cad_timeout_ms: int = 500


@dataclass
class TxPacket:
    payload: bytes
    frequency_hz: int
    bandwidth_hz: int
    spreading_factor: int
    coding_rate: int
    tx_power_dbm: int
    preamble_len: Optional[int] = None
    sync_word: Optional[int] = None
    invert_iq: bool = False
    timestamp_us: Optional[int] = None


@dataclass
class RxPacket:
    payload: bytes
    crc_ok: Optional[bool]
    frequency_hz: Optional[int]
    bandwidth_hz: Optional[int]
    spreading_factor: Optional[int]
    coding_rate: Optional[int]
    rssi_dbm: Optional[float]
    snr_db: Optional[float]
    channel: Optional[int]
    concentrator_timestamp_us: Optional[int]
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TxResult:
    ok: bool
    error: Optional[str]
    airtime_ms: Optional[float]
    started_at: str
    completed_at: Optional[str]
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SX1302Stats:
    started: bool = False
    status: dict[str, Any] = field(default_factory=dict)
