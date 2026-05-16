from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path
from typing import Any

from sx1302_meshcore_kiss.config import load_config
from sx1302_meshcore_kiss.dashboard.app import DashboardServer
from sx1302_meshcore_kiss.kiss.codec import KissDecodeError
from sx1302_meshcore_kiss.kiss.pty_endpoint import PtyEndpoint
from sx1302_meshcore_kiss.kiss.serial_endpoint import SerialEndpoint
from sx1302_meshcore_kiss.mqtt.publisher import MqttPublisher
from sx1302_meshcore_kiss.radio.modem_service import ModemService
from sx1302_meshcore_kiss.sx1302.adapter import SX1302Adapter
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer

logger = logging.getLogger(__name__)
NOISE_SCAN_INTERVAL_S = 60.0


def _configure_logging(cfg: Any) -> None:
    level = getattr(logging, cfg.logging.level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    live_log_path = getattr(cfg.logging, "live_log_path", "")

    if live_log_path:
        path = Path(live_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, mode="w", encoding="utf-8"))

    logging.basicConfig(
        level=level,
        handlers=handlers,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
        force=True,
    )


def _make_kiss_endpoint(cfg: Any) -> PtyEndpoint | SerialEndpoint:
    if cfg.kiss.mode == "pty":
        return PtyEndpoint(symlink=cfg.kiss.symlink)
    return SerialEndpoint(port=cfg.kiss.serial_port, baud_rate=cfg.kiss.baud_rate)


def _radio_state(adapter: SX1302Adapter, cfg: Any, radio_status: dict[str, Any]) -> str:
    if radio_status.get("started"):
        return "started"
    if adapter.radio is not None:
        return "configured_waiting_start"
    return "waiting_for_SetRadio_SetTxPower"


def _build_status_provider(
    *,
    cfg: Any,
    adapter: SX1302Adapter,
    mqtt: MqttPublisher,
    counters: Counters,
):
    def status_provider() -> dict[str, Any]:
        radio_status: dict[str, Any] = {}
        if adapter.radio is not None and hasattr(adapter.radio, "get_status"):
            try:
                radio_status = dict(adapter.radio.get_status() or {})
            except Exception as exc:  # pragma: no cover - defensive dashboard path
                radio_status = {"status_error": str(exc)}

        return {
            "mqtt_connected": mqtt.connected,
            "radio": {
                "adapter_started": adapter._started,
                "receive_call_count": adapter.receive_call_count,
                "receive_waiting": adapter.receive_waiting,
                "last_receive_error": adapter.last_receive_error,
                "configured": adapter.has_rf_config(cfg.radio),
                "radio_created": adapter.radio is not None,
                "started": bool(radio_status.get("started", False)),
                "frequency_hz": cfg.radio.frequency_hz,
                "bandwidth_hz": cfg.radio.bandwidth_hz,
                "spreading_factor": cfg.radio.spreading_factor,
                "coding_rate": cfg.radio.coding_rate,
                "tx_power_dbm": cfg.radio.tx_power_dbm,
                "state": _radio_state(adapter, cfg, radio_status),
                "sx1302": radio_status,
            },
            "kiss": {
                "mode": cfg.kiss.mode,
                "symlink": cfg.kiss.symlink,
                "serial_port": cfg.kiss.serial_port,
                "baud_rate": cfg.kiss.baud_rate,
            },
            "counters": counters.__dict__,
        }

    return status_provider


async def _kiss_loop(
    *,
    kiss: PtyEndpoint | SerialEndpoint,
    service: ModemService,
    mqtt: MqttPublisher,
    ring: PacketRingBuffer,
    counters: Counters,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            data = await kiss.read_bytes()
            if not data:
                await asyncio.sleep(0.05)
                continue
            for frame in kiss.codec.feed(data):
                await service.handle_kiss_frame(frame)
        except KissDecodeError as exc:
            counters.kiss_decode_error_count += 1
            event = {"direction": "kiss", "status": "decode_error", "error": str(exc)}
            await mqtt.publish_event("kiss/error", event)
            ring.add(event)


async def _rx_loop(*, adapter: SX1302Adapter, service: ModemService, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await service.handle_rx_packet(await adapter.receive())
        except RuntimeError as exc:
            if "not configured" not in str(exc):
                logger.exception("RX loop runtime error")
            await asyncio.sleep(0.25)
        except Exception:
            logger.exception("RX loop error")
            await asyncio.sleep(0.25)


async def _noise_scan_loop(*, adapter: SX1302Adapter, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(NOISE_SCAN_INTERVAL_S)
        try:
            # The SX1261 companion provides opportunistic spectral-scan noise floor
            # for both normal SX1302 RX and the special 62.5 kHz SX1261 RX path.
            if adapter.radio is not None and not adapter.is_channel_busy():
                adapter.get_noise_floor()
        except Exception:
            logger.exception("Periodic noise scan error")


async def run(config_path: str) -> None:
    cfg = load_config(config_path)
    _configure_logging(cfg)

    counters = Counters()
    ring = PacketRingBuffer(cfg.dashboard.max_packet_events)
    adapter = SX1302Adapter()
    mqtt = MqttPublisher(**cfg.mqtt.__dict__)
    kiss = _make_kiss_endpoint(cfg)
    service = ModemService(
        node_id=cfg.node_id,
        radio_config=cfg.radio,
        sx1302=adapter,
        kiss=kiss,
        mqtt=mqtt,
        ring=ring,
        counters=counters,
        forward_unknown_crc=cfg.crc.forward_unknown_crc,
    )
    dashboard = (
        DashboardServer(
            config=cfg,
            counters=counters,
            ring=ring,
            status_provider=_build_status_provider(cfg=cfg, adapter=adapter, mqtt=mqtt, counters=counters),
        )
        if cfg.dashboard.enabled
        else None
    )

    await adapter.configure(cfg.radio)
    await kiss.start()
    await mqtt.start()
    if dashboard:
        dashboard.start()
    await adapter.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(_kiss_loop(kiss=kiss, service=service, mqtt=mqtt, ring=ring, counters=counters, stop=stop)),
        asyncio.create_task(_rx_loop(adapter=adapter, service=service, stop=stop)),
        asyncio.create_task(_noise_scan_loop(adapter=adapter, stop=stop)),
    ]
    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await adapter.stop()
        await mqtt.stop()
        await kiss.stop()
        if dashboard:
            dashboard.stop()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
