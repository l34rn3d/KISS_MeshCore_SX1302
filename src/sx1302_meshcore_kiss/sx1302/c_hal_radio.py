from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from .radio import SX1302Radio
from .hal import (
    BW_125KHZ,
    BW_250KHZ,
    BW_500KHZ,
    CR_LORA_4_5,
    CR_LORA_4_6,
    CR_LORA_4_7,
    CR_LORA_4_8,
    MOD_LORA,
    lgw_time_on_air,
)

logger = logging.getLogger(__name__)

LGW_HAL_SUCCESS = 0
STAT_CRC_OK = 0x10
STAT_CRC_BAD = 0x11
STAT_NO_CRC = 0x01
TX_STATUS = 1
TX_FREE = 2
TX_EMITTING = 4
SX1261_62K5_NOISE_SCAN_INTERVAL_S = 60.0


class _McRxPacket(ctypes.Structure):
    _fields_ = [
        ("payload", ctypes.c_uint8 * 256),
        ("size", ctypes.c_uint16),
        ("status", ctypes.c_uint8),
        ("freq_hz", ctypes.c_uint32),
        ("if_chain", ctypes.c_uint8),
        ("rf_chain", ctypes.c_uint8),
        ("modulation", ctypes.c_uint8),
        ("bandwidth", ctypes.c_uint8),
        ("datarate", ctypes.c_uint32),
        ("coderate", ctypes.c_uint8),
        ("rssic", ctypes.c_float),
        ("rssis", ctypes.c_float),
        ("snr", ctypes.c_float),
        ("count_us", ctypes.c_uint32),
    ]


class _McSx1261Debug(ctypes.Structure):
    _fields_ = [
        ("receive_entry_count", ctypes.c_uint32),
        ("sx1261_branch_count", ctypes.c_uint32),
        ("sx1302_fallback_count", ctypes.c_uint32),
        ("poll_count", ctypes.c_uint32),
        ("rx_done_count", ctypes.c_uint32),
        ("crc_err_count", ctypes.c_uint32),
        ("timeout_count", ctypes.c_uint32),
        ("header_err_count", ctypes.c_uint32),
        ("preamble_count", ctypes.c_uint32),
        ("syncword_count", ctypes.c_uint32),
        ("header_valid_count", ctypes.c_uint32),
        ("last_irq_flags", ctypes.c_uint16),
        ("last_status", ctypes.c_uint8),
        ("last_rx_size", ctypes.c_uint8),
        ("last_rx_start", ctypes.c_uint8),
        ("lora_rx_enabled", ctypes.c_uint8),
        ("last_pkt_status", ctypes.c_uint8 * 4),
    ]


class SemtechCHalRadio(SX1302Radio):
    """SX1302 radio implementation backed by Semtech's upstream C HAL.

    This is intentionally a thin ctypes shim around c_hal/meshcore_lgw_bridge.c.
    GPIO reset and duty-cycle policy stay in the existing Python radio class;
    register init/TX/RX is delegated to libloragw so we have a proven baseline.
    """

    def __init__(
        self,
        *args: Any,
        c_hal_lib: str | None = None,
        lorawan_public: bool = False,
        implicit_header: bool = False,
        invert_iq: bool = False,
        lbt_enabled: bool = False,
        lbt_rssi_threshold_dbm: int | None = None,
        lbt_max_attempts: int = 3,
        lbt_backoff_ms: int = 50,
        lbt_cad_timeout_ms: int = 500,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.c_hal_lib = c_hal_lib or os.environ.get("MESHCORE_LGW_LIB") or str(
            Path(__file__).resolve().parents[3] / "build" / "c_hal" / "libmeshcore_lgw.so"
        )
        self.lorawan_public = lorawan_public
        self.implicit_header = implicit_header
        self.invert_iq = invert_iq
        self.lbt_enabled = bool(lbt_enabled)
        self.lbt_rssi_threshold_dbm = lbt_rssi_threshold_dbm
        self.lbt_max_attempts = max(1, int(lbt_max_attempts))
        self.lbt_backoff_ms = max(0, int(lbt_backoff_ms))
        self.lbt_cad_timeout_ms = max(0, int(lbt_cad_timeout_ms))
        self._lib = self._load_library(self.c_hal_lib)
        self._last_rx: Optional[_McRxPacket] = None
        self._pending_rx: list[_McRxPacket] = []
        self._last_status_code: Optional[int] = None
        self._last_lbt: dict[str, Any] = {"attempts": 0, "channel_busy": False, "backoff_delays_ms": []}
        self._last_noise_floor: Optional[float] = None
        self._last_noise_scan_at: float = 0.0
        self._radio_use_depth = 0

    @staticmethod
    def _load_library(path: str):
        lib_path = Path(path)
        if not lib_path.exists():
            raise RuntimeError(
                f"Semtech C HAL bridge not found at {lib_path}. Run tools/build_semtech_bridge.sh first "
                "or set radio.c_hal_lib / MESHCORE_LGW_LIB."
            )
        lib = ctypes.CDLL(str(lib_path))
        lib.mc_lgw_start.argtypes = [
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint8,
            ctypes.c_uint8,
            ctypes.c_int8,
            ctypes.c_uint16,
            ctypes.c_uint16,
            ctypes.c_bool,
            ctypes.c_bool,
            ctypes.c_bool,
            ctypes.c_char_p,
        ]
        lib.mc_lgw_start.restype = ctypes.c_int
        lib.mc_lgw_stop.argtypes = []
        lib.mc_lgw_stop.restype = ctypes.c_int
        lib.mc_lgw_send.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint16]
        lib.mc_lgw_send.restype = ctypes.c_int
        lib.mc_lgw_receive.argtypes = [ctypes.POINTER(_McRxPacket)]
        lib.mc_lgw_receive.restype = ctypes.c_int
        lib.mc_lgw_status.argtypes = [ctypes.c_uint8, ctypes.POINTER(ctypes.c_uint8)]
        lib.mc_lgw_status.restype = ctypes.c_int
        lib.mc_lgw_spectral_scan_noise.argtypes = [ctypes.c_uint32, ctypes.c_uint16, ctypes.POINTER(ctypes.c_int16), ctypes.c_uint16]
        lib.mc_lgw_spectral_scan_noise.restype = ctypes.c_int
        lib.mc_lgw_experimental_62k5_if_chain.argtypes = []
        lib.mc_lgw_experimental_62k5_if_chain.restype = ctypes.c_int
        if hasattr(lib, "mc_lgw_bandwidth_code"):
            lib.mc_lgw_bandwidth_code.argtypes = []
            lib.mc_lgw_bandwidth_code.restype = ctypes.c_int
        if hasattr(lib, "mc_lgw_bandwidth_hz"):
            lib.mc_lgw_bandwidth_hz.argtypes = []
            lib.mc_lgw_bandwidth_hz.restype = ctypes.c_int
        if hasattr(lib, "mc_lgw_sx1261_debug"):
            lib.mc_lgw_sx1261_debug.argtypes = [ctypes.POINTER(_McSx1261Debug)]
            lib.mc_lgw_sx1261_debug.restype = ctypes.c_int
        lib.mc_lgw_version.argtypes = []
        lib.mc_lgw_version.restype = ctypes.c_char_p
        return lib

    def begin(self) -> None:
        if self._is_started:
            return
        if self.reset_enabled:
            self._reset_concentrator()
        logger.info(
            "Starting SX1302 through Semtech C HAL bridge: spi=%s freq=%s bw=%s sf=%s cr=4/%s power=%s lib=%s",
            self.com_path,
            self.frequency,
            self.bandwidth,
            self.spreading_factor,
            self.coding_rate,
            self.tx_power,
            self.c_hal_lib,
        )
        ret = self._lib.mc_lgw_start(
            self.com_path.encode(),
            int(self.frequency),
            int(self.bandwidth),
            int(self.spreading_factor),
            int(self.coding_rate),
            int(self.tx_power),
            int(self.preamble_length),
            int(self.sync_word or 0x3444),
            bool(self.implicit_header),
            bool(self.invert_iq),
            bool(self.lorawan_public),
            self.sx1261_spi_path.encode() if self.sx1261_spi_path else None,
        )
        if ret != LGW_HAL_SUCCESS:
            raise RuntimeError(f"mc_lgw_start() failed with code {ret}")
        self._is_started = True
        logger.info("Semtech C HAL started: %s", self._version())

    def cleanup(self) -> None:
        try:
            self._lib.mc_lgw_stop()
        finally:
            self._is_started = False

    async def send(self, payload: bytes) -> dict[str, Any]:
        if not self._is_started:
            self.begin()
        if len(payload) > 255:
            raise ValueError("LoRa payload too large for SX1302 HAL bridge (>255 bytes)")
        started = time.monotonic()
        code = ctypes.c_uint8(0)
        lbt_meta = await self._wait_for_lbt_clear()
        self._radio_use_depth += 1
        try:
            arr = (ctypes.c_uint8 * len(payload)).from_buffer_copy(payload)
            ret = await asyncio.to_thread(self._lib.mc_lgw_send, arr, len(payload))
            if ret != LGW_HAL_SUCCESS:
                raise RuntimeError(f"mc_lgw_send() failed with code {ret}")
            # Wait briefly for immediate TX to clear so callers get a useful status.
            for _ in range(100):
                if self._lib.mc_lgw_status(TX_STATUS, ctypes.byref(code)) == LGW_HAL_SUCCESS:
                    self._last_status_code = int(code.value)
                    if code.value not in (TX_EMITTING,):
                        break
                await asyncio.sleep(0.01)
        finally:
            self._radio_use_depth = max(0, self._radio_use_depth - 1)
        return {
            "success": True,
            "backend": "semtech_c_hal",
            "tx_status": int(code.value),
            "airtime_ms": self.get_airtime(len(payload)),
            "lbt_enabled": self.lbt_enabled,
            **lbt_meta,
            "elapsed_ms": (time.monotonic() - started) * 1000.0,
        }

    async def _wait_for_lbt_clear(self) -> dict[str, Any]:
        if not self.lbt_enabled:
            self._last_lbt = {"attempts": 0, "channel_busy": False, "backoff_delays_ms": []}
            return {"lbt_attempts": 0, "lbt_channel_busy": False, "lbt_backoff_delays_ms": []}

        backoffs: list[int] = []
        for attempt in range(1, self.lbt_max_attempts + 1):
            busy = await self._lbt_channel_busy()
            if not busy:
                self._last_lbt = {"attempts": attempt, "channel_busy": False, "backoff_delays_ms": backoffs}
                return {"lbt_attempts": attempt, "lbt_channel_busy": False, "lbt_backoff_delays_ms": backoffs}
            if attempt >= self.lbt_max_attempts:
                self._last_lbt = {"attempts": attempt, "channel_busy": True, "backoff_delays_ms": backoffs}
                raise RuntimeError(f"C HAL LBT blocked TX: channel busy after {attempt} attempts")
            delay_ms = self.lbt_backoff_ms * (2 ** (attempt - 1))
            backoffs.append(delay_ms)
            await asyncio.sleep(delay_ms / 1000.0)

        return {"lbt_attempts": self.lbt_max_attempts, "lbt_channel_busy": True, "lbt_backoff_delays_ms": backoffs}

    async def _lbt_channel_busy(self) -> bool:
        if self.is_channel_busy():
            return True
        if int(self.bandwidth) == 62_500 and self.sx1261_spi_path:
            if self.lbt_rssi_threshold_dbm is None:
                return False
            noise = self._scan_noise_floor(force=True)
            if noise is None:
                return False
            return noise >= float(self.lbt_rssi_threshold_dbm)
        deadline = time.monotonic() + (self.lbt_cad_timeout_ms / 1000.0)
        while True:
            pkt = _McRxPacket()
            self._radio_use_depth += 1
            try:
                ret = await asyncio.to_thread(self._lib.mc_lgw_receive, ctypes.byref(pkt))
            finally:
                self._radio_use_depth = max(0, self._radio_use_depth - 1)
            if ret < 0:
                raise RuntimeError(f"mc_lgw_receive() failed during LBT with code {ret}")
            if ret > 0:
                self._last_rx = pkt
                self._pending_rx.append(pkt)
                if self.lbt_rssi_threshold_dbm is None or float(pkt.rssic) >= float(self.lbt_rssi_threshold_dbm):
                    return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.01)

    def get_airtime(self, payload: Any) -> int:
        if isinstance(payload, (bytes, bytearray, memoryview)):
            payload_len = len(payload)
        else:
            payload_len = int(payload)
        if not (0 <= payload_len <= 255):
            raise ValueError("payload length must be between 0 and 255 bytes")
        return lgw_time_on_air({
            "modulation": MOD_LORA,
            "bandwidth": {125_000: BW_125KHZ, 250_000: BW_250KHZ, 500_000: BW_500KHZ}.get(int(self.bandwidth), BW_125KHZ),
            "bandwidth_hz": int(self.bandwidth),
            "datarate": int(self.spreading_factor),
            "coderate": {5: CR_LORA_4_5, 6: CR_LORA_4_6, 7: CR_LORA_4_7, 8: CR_LORA_4_8}.get(int(self.coding_rate), CR_LORA_4_5),
            "preamble": int(self.preamble_length),
            "no_crc": False,
            "no_header": bool(self.implicit_header),
            "size": payload_len,
            "payload": bytes(payload_len),
        })

    def get_noise_floor(self) -> Optional[float]:
        return self.scan_noise_floor_if_idle()

    def scan_noise_floor_if_idle(self) -> Optional[float]:
        """Refresh the SX1261 spectral-scan noise floor only when it is safe.

        In 125 kHz mode SX1302 remains the packet RX backend; the companion
        SX1261 is only borrowed briefly for noise measurements. In 62.5 kHz mode
        SX1261 also owns packet RX, so scans are rate-limited and restored in C.
        """
        if not self.sx1261_spi_path or not self._is_started:
            return self._last_noise_floor
        if int(self.bandwidth) == 62_500:
            if self._last_noise_scan_at and (time.monotonic() - self._last_noise_scan_at) < SX1261_62K5_NOISE_SCAN_INTERVAL_S:
                return self._last_noise_floor
            if self._radio_use_depth > 0 or self._pending_rx or self.is_channel_busy() or self._last_lbt.get("channel_busy"):
                return self._last_noise_floor
            return self._scan_noise_floor(force=True)
        if self._radio_use_depth > 0 or self._pending_rx or self.is_channel_busy():
            return self._last_noise_floor
        return self._scan_noise_floor(force=False)

    def _scan_noise_floor(self, *, force: bool) -> Optional[float]:
        if not force and (self._radio_use_depth > 0 or self._pending_rx or self.is_channel_busy()):
            return self._last_noise_floor
        out = ctypes.c_int16()
        self._radio_use_depth += 1
        try:
            ret = self._lib.mc_lgw_spectral_scan_noise(int(self.frequency), 200, ctypes.byref(out), 2000)
        finally:
            self._radio_use_depth = max(0, self._radio_use_depth - 1)
        if ret == LGW_HAL_SUCCESS:
            self._last_noise_floor = float(out.value)
            self._last_noise_scan_at = time.monotonic()
        return self._last_noise_floor

    def is_channel_busy(self) -> bool:
        code = ctypes.c_uint8(0)
        if self._is_started and self._lib.mc_lgw_status(TX_STATUS, ctypes.byref(code)) == LGW_HAL_SUCCESS:
            self._last_status_code = int(code.value)
        return self._last_status_code == TX_EMITTING

    async def wait_for_rx(self) -> bytes:
        if not self._is_started:
            self.begin()
        while True:
            if self._pending_rx:
                pkt = self._pending_rx.pop(0)
                self._last_rx = pkt
                return bytes(pkt.payload[: pkt.size])
            pkt = _McRxPacket()
            self._radio_use_depth += 1
            try:
                ret = await asyncio.to_thread(self._lib.mc_lgw_receive, ctypes.byref(pkt))
            finally:
                self._radio_use_depth = max(0, self._radio_use_depth - 1)
            if ret < 0:
                raise RuntimeError(f"mc_lgw_receive() failed with code {ret}")
            if ret > 0:
                self._last_rx = pkt
                return bytes(pkt.payload[: pkt.size])
            await asyncio.sleep(0.05)

    def get_last_signal_rssi(self) -> Optional[float]:
        if self._last_rx is not None:
            return float(self._last_rx.rssic)
        return None

    def get_last_snr(self) -> Optional[float]:
        if self._last_rx is not None:
            return float(self._last_rx.snr)
        return None

    def get_status(self) -> dict[str, Any]:
        code = ctypes.c_uint8(0)
        status_ret = self._lib.mc_lgw_status(TX_STATUS, ctypes.byref(code)) if self._is_started else -1
        if status_ret == LGW_HAL_SUCCESS:
            self._last_status_code = int(code.value)
        rx_meta: dict[str, Any] = {}
        if self._last_rx is not None:
            rx_meta = {
                "last_rx_size": int(self._last_rx.size),
                "last_rx_status": int(self._last_rx.status),
                "last_rx_freq_hz": int(self._last_rx.freq_hz),
                "last_rx_if_chain": int(self._last_rx.if_chain),
                "last_rx_rssi": float(self._last_rx.rssic),
                "last_rx_snr": float(self._last_rx.snr),
                "last_rx_count_us": int(self._last_rx.count_us),
            }
        sx1261_debug: dict[str, Any] = {}
        if hasattr(self._lib, "mc_lgw_sx1261_debug"):
            dbg = _McSx1261Debug()
            if self._lib.mc_lgw_sx1261_debug(ctypes.byref(dbg)) == LGW_HAL_SUCCESS:
                sx1261_debug = {
                    "rx_poll_debug": {
                        "receive_entry_count": int(dbg.receive_entry_count),
                        "sx1261_branch_count": int(dbg.sx1261_branch_count),
                        "sx1302_fallback_count": int(dbg.sx1302_fallback_count),
                    },
                    "sx1261_rx_debug": {
                        "poll_count": int(dbg.poll_count),
                        "receive_entry_count": int(dbg.receive_entry_count),
                        "sx1261_branch_count": int(dbg.sx1261_branch_count),
                        "sx1302_fallback_count": int(dbg.sx1302_fallback_count),
                        "lora_rx_enabled": bool(dbg.lora_rx_enabled),
                        "rx_done_count": int(dbg.rx_done_count),
                        "crc_err_count": int(dbg.crc_err_count),
                        "timeout_count": int(dbg.timeout_count),
                        "header_err_count": int(dbg.header_err_count),
                        "preamble_count": int(dbg.preamble_count),
                        "syncword_count": int(dbg.syncword_count),
                        "header_valid_count": int(dbg.header_valid_count),
                        "last_irq_flags": f"0x{int(dbg.last_irq_flags):04x}",
                        "last_status": f"0x{int(dbg.last_status):02x}",
                        "last_rx_size": int(dbg.last_rx_size),
                        "last_rx_start": int(dbg.last_rx_start),
                        "last_pkt_status": [int(v) for v in dbg.last_pkt_status],
                    }
                }
        return {
            "started": self._is_started,
            "backend": "semtech_c_hal",
            "lib": self.c_hal_lib,
            "version": self._version(),
            "tx_status": int(code.value) if status_ret == LGW_HAL_SUCCESS else self._last_status_code,
            "lbt_enabled": self.lbt_enabled,
            "last_lbt": dict(self._last_lbt),
            "sx1261_configured": self.sx1261_spi_path is not None,
            "last_noise_floor": self._last_noise_floor,
            "last_noise_scan_at": self._last_noise_scan_at,
            "experimental_62k5_if_chain": self._lib.mc_lgw_experimental_62k5_if_chain() if hasattr(self._lib, "mc_lgw_experimental_62k5_if_chain") else 2,
            "hal_bandwidth_code": self._lib.mc_lgw_bandwidth_code() if hasattr(self._lib, "mc_lgw_bandwidth_code") else None,
            "hal_bandwidth_hz": self._lib.mc_lgw_bandwidth_hz() if hasattr(self._lib, "mc_lgw_bandwidth_hz") else None,
            "rx_backend": "sx1261" if int(self.bandwidth) == 62_500 else "sx1302",
            **rx_meta,
            **sx1261_debug,
        }

    def _version(self) -> str:
        raw = self._lib.mc_lgw_version()
        return raw.decode(errors="replace") if raw else "unknown"
