from __future__ import annotations
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Optional
import yaml
from sx1302_meshcore_kiss.sx1302.metadata import RadioConfig

@dataclass
class KissConfig:
    mode: str = "pty"
    symlink: str = "/tmp/sx1302-kiss"
    serial_port: str = "/dev/ttyUSB0"
    baud_rate: int = 115200
    tcp_bind: str = "127.0.0.1"
    tcp_port: int = 8001

@dataclass
class CrcConfig:
    forward_unknown_crc: bool = False
    publish_bad_crc_payload: bool = True
    dashboard_show_bad_crc_payload: bool = True

@dataclass
class MqttConfig:
    enabled: bool = True
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
    bind_host: str = "127.0.0.1"
    port: int = 8080
    max_packet_events: int = 50

@dataclass
class LoggingConfig:
    level: str = "INFO"
    log_raw_payloads: bool = False

@dataclass
class StatusConfig:
    publish_interval_seconds: int = 30
    counters_interval_seconds: int = 30

@dataclass
class AppConfig:
    node_id: str = "repeater-01"
    kiss: KissConfig = field(default_factory=KissConfig)
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
    cfg.kiss = _merge_dataclass(KissConfig, raw.get('kiss'))
    radio_raw = raw.get('radio', {}) or {}
    sx_raw = raw.get('sx1302', {}) or {}
    combined = {**radio_raw}
    if 'spi_device' not in combined and 'spi_device' in sx_raw: combined['spi_device'] = sx_raw['spi_device']
    cfg.radio = _merge_dataclass(RadioConfig, combined)
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
