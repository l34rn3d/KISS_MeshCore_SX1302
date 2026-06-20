from __future__ import annotations
from dataclasses import asdict, dataclass, field, is_dataclass
from importlib import resources
import os
from pathlib import Path
from typing import Any, Optional
import yaml
from sx1302_meshcore_kiss.sx1302.metadata import RadioConfig

HOTSPOT_PROFILE_ENV = "SX1302_HOTSPOT_PROFILE_DIR"
HOTSPOT_PROFILE_DIR = Path("/etc/sx1302-meshcore-kiss/hotspots")
_PACKAGE_HOTSPOT_PROFILE_DIR = "hotspot_profiles"

STARTUP_PROFILES: dict[str, dict[str, Any]] = {
    "sensecap_wm1302_pinctrl": {
        "description": "SenseCAP/WM1302 Raspberry Pi pinctrl reset sequence",
        "reset_script_path": "/opt/sx1302-meshcore-kiss/tools/reset_wm1302_pinctrl.sh",
        "spi_device": "/dev/spidev0.0",
        "sx1261_spi_path": "/dev/spidev0.1",
        "power_enable_pin": 18,
        "sx1302_reset_pin": 17,
        "sx1261_reset_pin": 5,
        "adc_reset_pin": 13,
    },
    "semtech_reset_lgw_sysfs": {
        "description": "Semtech CoreCell reset_lgw.sh sysfs GPIO sequence",
        "reset_script_path": "/opt/sx1302-meshcore-kiss/tools/reset_lgw.sh",
        "spi_device": "/dev/spidev0.0",
        "sx1261_spi_path": "/dev/spidev0.1",
        "power_enable_pin": 18,
        "sx1302_reset_pin": 23,
        "sx1261_reset_pin": 22,
        "adc_reset_pin": 13,
    },
    "nebra_helium_docker": {
        "description": "Nebra/Helium hotspot-style startup using an external reset script to avoid GPIO ownership issues",
        "reset_script_path": "/opt/sx1302-meshcore-kiss/tools/reset_lgw.sh",
        "spi_device": "/dev/spidev0.0",
        "sx1261_spi_path": "/dev/spidev0.1",
        "power_enable_pin": 18,
        "sx1302_reset_pin": 23,
        "sx1261_reset_pin": 22,
        "adc_reset_pin": 13,
    },
    "gpioset_fallback": {
        "description": "Internal gpioset reset sequence configured by GPIO pin values",
        "reset_script_path": "",
        "spi_device": "/dev/spidev0.0",
        "sx1261_spi_path": "/dev/spidev0.1",
        "power_enable_pin": 18,
        "sx1302_reset_pin": 23,
        "sx1261_reset_pin": 22,
        "adc_reset_pin": 13,
    },
    "no_reset": {
        "description": "Do not reset GPIOs before starting SX1302",
        "reset_enabled": False,
    },
}

RADIO_PROFILES: dict[str, dict[str, Any]] = {
    "meshcore_eu_default": {
        "description": "MeshCore PlatformIO default: 869.618 MHz, 62.5 kHz, SF8",
        "frequency_hz": 869_618_000,
        "bandwidth_hz": 62_500,
        "spreading_factor": 8,
        "coding_rate": 5,
        "tx_power_dbm": 22,
        "sync_word": 0x12,
        "preamble_len": 16,
    },
    "meshcore_eu_docs_legacy": {
        "description": "MeshCore documented CLI default: 869.525 MHz, 250 kHz, SF11",
        "frequency_hz": 869_525_000,
        "bandwidth_hz": 250_000,
        "spreading_factor": 11,
        "coding_rate": 5,
        "tx_power_dbm": 22,
        "sync_word": 0x12,
        "preamble_len": 16,
    },
    "meshcore_us_default": {
        "description": "Common US MeshCore test profile: 915 MHz, 125 kHz, SF8",
        "frequency_hz": 915_000_000,
        "bandwidth_hz": 125_000,
        "spreading_factor": 8,
        "coding_rate": 5,
        "tx_power_dbm": 22,
        "sync_word": 0x12,
        "preamble_len": 16,
    },
    "meshcore_us_repeat": {
        "description": "Companion-radio repeat test profile: 918 MHz, 125 kHz, SF8",
        "frequency_hz": 918_000_000,
        "bandwidth_hz": 125_000,
        "spreading_factor": 8,
        "coding_rate": 5,
        "tx_power_dbm": 22,
        "sync_word": 0x12,
        "preamble_len": 16,
    },
    "meshcore_433": {
        "description": "433 MHz MeshCore test profile",
        "frequency_hz": 433_000_000,
        "bandwidth_hz": 125_000,
        "spreading_factor": 8,
        "coding_rate": 5,
        "tx_power_dbm": 14,
        "sync_word": 0x12,
        "preamble_len": 16,
    },
}


def hotspot_profile_dir() -> Path:
    return Path(os.environ.get(HOTSPOT_PROFILE_ENV, HOTSPOT_PROFILE_DIR))


def _coerce_hotspot_profile(name: str, data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict) or data.get("enabled") is False:
        return None
    profile = {k: v for k, v in data.items() if k != "enabled"}
    profile.setdefault("friendly", name)
    if "spi_device" not in profile or "reset_pin" not in profile:
        return None
    profile["spi_device"] = str(profile["spi_device"])
    profile["reset_pin"] = int(profile["reset_pin"])
    if profile.get("sx125x_reset_pin") is not None:
        profile["sx125x_reset_pin"] = int(profile["sx125x_reset_pin"])
    if profile.get("startup_profile") is not None:
        profile["startup_profile"] = str(profile["startup_profile"])
    return profile


def _load_hotspot_profile_file(name: str, text: str) -> dict[str, Any] | None:
    return _coerce_hotspot_profile(name, yaml.safe_load(text) or {})


def _load_packaged_hotspot_profiles() -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    base = resources.files(__package__).joinpath(_PACKAGE_HOTSPOT_PROFILE_DIR)
    for entry in sorted(base.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith((".yaml", ".yml")) or entry.name.startswith("_"):
            continue
        profile = _load_hotspot_profile_file(Path(entry.name).stem, entry.read_text())
        if profile:
            profiles[Path(entry.name).stem] = profile
    return profiles


def _load_external_hotspot_profiles() -> dict[str, dict[str, Any] | None]:
    profiles: dict[str, dict[str, Any] | None] = {}
    directory = hotspot_profile_dir()
    if not directory.is_dir():
        return profiles
    for path in sorted(directory.glob("*.y*ml")):
        if path.name.startswith("_"):
            continue
        raw = yaml.safe_load(path.read_text()) or {}
        if isinstance(raw, dict) and raw.get("enabled") is False:
            profiles[path.stem] = None
            continue
        profile = _coerce_hotspot_profile(path.stem, raw)
        if profile:
            profiles[path.stem] = profile
    return profiles


def hotspot_profiles() -> dict[str, dict[str, Any]]:
    profiles = _load_packaged_hotspot_profiles()
    for name, profile in _load_external_hotspot_profiles().items():
        if profile is None:
            profiles.pop(name, None)
        else:
            profiles[name] = profile
    return profiles


def hotspot_profile_names() -> list[str]:
    return list(hotspot_profiles().keys())

@dataclass
class KissConfig:
    mode: str = "pty"
    symlink: str = "/run/sx1302-meshcore-kiss/sx1302-kiss"
    serial_port: str = "/dev/ttyUSB0"
    baud_rate: int = 115200
    tcp_bind: str = "127.0.0.1"
    tcp_port: int = 8001

@dataclass
class PyMCTcpConfig:
    enabled: bool = True
    bind_host: str = "0.0.0.0"
    port: int = 5055
    token: str = ""
    max_clients: int = 1

@dataclass
class StartupConfig:
    profile: str = "nebra_helium_docker"
    hotspot: str = "sensecap-fl1"
    options: list[str] = field(default_factory=lambda: list(STARTUP_PROFILES.keys()))
    hotspot_options: list[str] = field(default_factory=hotspot_profile_names)

@dataclass
class ManualRadioConfig:
    enabled: bool = False
    profile: str = ""
    auto_start: bool = False
    profile_options: list[str] = field(default_factory=lambda: list(RADIO_PROFILES.keys()))

@dataclass
class CrcConfig:
    forward_unknown_crc: bool = True
    publish_bad_crc_payload: bool = False
    dashboard_show_bad_crc_payload: bool = False

@dataclass
class MqttConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 1883
    username: str = ""
    password: str = ""
    client_id: str = "sx1302-meshcore-kiss-repeater-01"
    base_topic: str = "meshcore/sx1302/repeater-01"
    qos_events: int = 0
    qos_status: int = 1
    retain_status: bool = True
    retain_packet_events: bool = False

@dataclass
class DashboardConfig:
    enabled: bool = True
    bind_host: str = "0.0.0.0"
    port: int = 8080
    max_packet_events: int = 50

@dataclass
class LoggingConfig:
    level: str = "INFO"
    log_raw_payloads: bool = False
    live_log_path: str = "/run/sx1302-meshcore-kiss/live.log"

@dataclass
class StatusConfig:
    publish_interval_seconds: int = 30
    counters_interval_seconds: int = 30

@dataclass
class AppConfig:
    node_id: str = "repeater-01"
    transport: str = "pymc_tcp"
    kiss: KissConfig = field(default_factory=KissConfig)
    pymc_tcp: PyMCTcpConfig = field(default_factory=PyMCTcpConfig)
    startup: StartupConfig = field(default_factory=StartupConfig)
    manual_radio: ManualRadioConfig = field(default_factory=ManualRadioConfig)
    radio: RadioConfig = field(default_factory=RadioConfig)
    crc: CrcConfig = field(default_factory=CrcConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    status: StatusConfig = field(default_factory=StatusConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _merge_dataclass(cls, data: Any):
    obj = cls()
    if not isinstance(data, dict): return obj
    for k, v in data.items():
        if hasattr(obj, k): setattr(obj, k, v)
    return obj

def load_config(path: str | Path) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    cfg = AppConfig()
    cfg.node_id = raw.get('node_id', cfg.node_id)
    cfg.transport = str(raw.get('transport', cfg.transport)).lower()
    if cfg.transport not in {"pymc_tcp", "kiss"}:
        raise ValueError("transport must be 'pymc_tcp' or 'kiss'")
    cfg.kiss = _merge_dataclass(KissConfig, raw.get('kiss'))
    cfg.pymc_tcp = _merge_dataclass(PyMCTcpConfig, raw.get('pymc_tcp'))
    cfg.startup = _merge_dataclass(StartupConfig, raw.get('startup'))
    cfg.manual_radio = _merge_dataclass(ManualRadioConfig, raw.get('manual_radio'))
    radio_raw = raw.get('radio', {}) or {}
    sx_raw = raw.get('sx1302', {}) or {}
    combined = {**radio_raw}
    if 'spi_device' not in combined and 'spi_device' in sx_raw: combined['spi_device'] = sx_raw['spi_device']
    cfg.radio = _merge_dataclass(RadioConfig, combined)
    profile = STARTUP_PROFILES.get(str(cfg.startup.profile))
    if profile:
        for key, value in profile.items():
            if key == "description":
                continue
            if hasattr(cfg.radio, key) and key not in radio_raw:
                setattr(cfg.radio, key, value)
    apply_hotspot_profile(cfg, cfg.startup.hotspot, explicit_radio_keys=set(radio_raw.keys()))
    if cfg.manual_radio.enabled and cfg.manual_radio.profile:
        apply_radio_profile(cfg, cfg.manual_radio.profile, explicit_radio_keys=set(radio_raw.keys()))
    cfg.crc = _merge_dataclass(CrcConfig, raw.get('crc'))
    cfg.mqtt = _merge_dataclass(MqttConfig, raw.get('mqtt'))
    cfg.dashboard = _merge_dataclass(DashboardConfig, raw.get('dashboard'))
    cfg.status = _merge_dataclass(StatusConfig, raw.get('status'))
    cfg.logging = _merge_dataclass(LoggingConfig, raw.get('logging'))
    return cfg

def redact_config(config: AppConfig | dict[str, Any]) -> dict[str, Any]:
    data = asdict(config) if is_dataclass(config) else dict(config)
    def red(v, key=''):
        if isinstance(v, dict): return {k: red(val, k) for k, val in v.items()}
        if any(marker in key.lower() for marker in ('password','secret','token','key')) and key.lower() not in ('sync_word',): return '<redacted>' if v else ''
        return v
    return red(data)


def startup_profiles() -> dict[str, dict[str, Any]]:
    return {name: dict(values) for name, values in STARTUP_PROFILES.items()}


def radio_profiles() -> dict[str, dict[str, Any]]:
    return {name: dict(values) for name, values in RADIO_PROFILES.items()}


def apply_radio_profile(config: AppConfig, profile_name: str, *, explicit_radio_keys: set[str] | None = None) -> bool:
    profile = RADIO_PROFILES.get(str(profile_name))
    if not profile:
        return False
    explicit = explicit_radio_keys or set()
    config.manual_radio.profile = str(profile_name)
    for key, value in profile.items():
        if key != "description" and key not in explicit and hasattr(config.radio, key):
            setattr(config.radio, key, value)
    return True


def apply_hotspot_profile(config: AppConfig, hotspot: str, *, explicit_radio_keys: set[str] | None = None) -> bool:
    profile = hotspot_profiles().get(str(hotspot))
    if not profile:
        return False
    explicit = explicit_radio_keys or set()
    config.startup.hotspot = str(hotspot)
    startup_profile = str(profile.get("startup_profile") or "")
    values = {}
    if startup_profile and startup_profile in STARTUP_PROFILES:
        config.startup.profile = startup_profile
        values.update({k: v for k, v in STARTUP_PROFILES[startup_profile].items() if k != "description"})
        values.setdefault("reset_script_args", [])
        values.setdefault("reset_script_env", {})
    else:
        env = {"CONCENTRATOR_RESET_PIN": str(profile["reset_pin"])}
        if profile.get("sx125x_reset_pin") is not None:
            env["SX125x_RESET_PIN"] = str(profile["sx125x_reset_pin"])
        values.update({
            "reset_enabled": True,
            "reset_script_path": "/opt/sx1302-meshcore-kiss/tools/reset_lgw_nebra.sh",
            "reset_script_args": ["start"],
            "reset_script_env": env,
            "sx1261_reset_pin": int(profile["sx125x_reset_pin"]) if profile.get("sx125x_reset_pin") is not None else None,
            "power_enable_pin": None,
            "adc_reset_pin": None,
        })
    values.update({
        "spi_device": profile["spi_device"],
        "sx1302_reset_pin": int(profile["reset_pin"]),
    })
    for key, value in values.items():
        if key not in explicit and hasattr(config.radio, key):
            setattr(config.radio, key, value)
    return True
