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
from sx1302_meshcore_kiss.pymc_tcp.server import PyMCTcpServer
from sx1302_meshcore_kiss.radio.modem_service import ModemService
from sx1302_meshcore_kiss.sx1302.adapter import SX1302Adapter
from sx1302_meshcore_kiss.telemetry.counters import Counters
from sx1302_meshcore_kiss.telemetry.ring_buffer import PacketRingBuffer

logger = logging.getLogger(__name__)
NOISE_SCAN_INTERVAL_S = 60.0


class NullKissEndpoint:
    async def write_frame(self, command: int, payload: bytes) -> None:
        return


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
    pymc_tcp: PyMCTcpServer | None = None,
):
    last_radio_activity = {
        "preamble": 0,
        "syncword": 0,
        "header_valid": 0,
        "meshcore_candidate": 0,
    }

    def status_provider() -> dict[str, Any]:
        radio_status: dict[str, Any] = {}
        if adapter.radio is not None and hasattr(adapter.radio, "get_status"):
            try:
                radio_status = dict(adapter.radio.get_status() or {})
            except Exception as exc:  # pragma: no cover - defensive dashboard path
                radio_status = {"status_error": str(exc)}
        temperature_c = radio_status.get("temperature_c")
        if temperature_c is None and hasattr(adapter, "get_temperature"):
            try:
                temperature_c = adapter.get_temperature()
            except Exception:  # pragma: no cover - defensive dashboard path
                temperature_c = None

        sx1261_debug = radio_status.get("sx1261_rx_debug") if isinstance(radio_status, dict) else None
        if isinstance(sx1261_debug, dict):
            preamble = int(sx1261_debug.get("preamble_count") or 0)
            syncword = int(sx1261_debug.get("syncword_count") or 0)
            header_valid = int(sx1261_debug.get("header_valid_count") or 0)
            rx_done = int(sx1261_debug.get("rx_done_count") or 0)
            meshcore_candidate = max(syncword, header_valid, rx_done)
            counters.radio_preamble_count = max(counters.radio_preamble_count, preamble)
            counters.radio_syncword_count = max(counters.radio_syncword_count, syncword)
            counters.radio_header_valid_count = max(counters.radio_header_valid_count, header_valid)
            counters.radio_meshcore_candidate_count = max(counters.radio_meshcore_candidate_count, meshcore_candidate)
            if preamble > last_radio_activity["preamble"]:
                logger.info(
                    "LoRa preamble activity observed: preambles=%d (+%d), syncword=%d, header_valid=%d, rx_done=%d",
                    preamble,
                    preamble - last_radio_activity["preamble"],
                    syncword,
                    header_valid,
                    rx_done,
                )
            if meshcore_candidate > last_radio_activity["meshcore_candidate"]:
                logger.info(
                    "MeshCore-like LoRa activity observed: candidates=%d (+%d), syncword=%d, header_valid=%d, rx_done=%d",
                    meshcore_candidate,
                    meshcore_candidate - last_radio_activity["meshcore_candidate"],
                    syncword,
                    header_valid,
                    rx_done,
                )
            last_radio_activity.update({
                "preamble": preamble,
                "syncword": syncword,
                "header_valid": header_valid,
                "meshcore_candidate": meshcore_candidate,
            })
        completed_lora_packets = int(counters.rx_good_count + counters.rx_bad_crc_count + counters.rx_unknown_crc_count)
        counters.radio_meshcore_candidate_count = max(counters.radio_meshcore_candidate_count, completed_lora_packets)

        return {
            "mqtt_connected": mqtt.connected,
            "transport": cfg.transport,
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
                "temperature_c": temperature_c,
                "last_cad": adapter.get_cad_status() if hasattr(adapter, "get_cad_status") else {},
                "state": _radio_state(adapter, cfg, radio_status),
                "sx1302": radio_status,
            },
            "kiss": {
                "enabled": cfg.transport == "kiss",
                "mode": cfg.kiss.mode,
                "symlink": cfg.kiss.symlink,
                "serial_port": cfg.kiss.serial_port,
                "baud_rate": cfg.kiss.baud_rate,
            },
            "pymc_tcp": pymc_tcp.status() if pymc_tcp is not None else {"enabled": False},
            "startup": {
                "profile": cfg.startup.profile,
                "hotspot": cfg.startup.hotspot,
                "options": cfg.startup.options,
                "hotspot_options": cfg.startup.hotspot_options,
                "reset_script_path": cfg.radio.reset_script_path,
            },
            "manual_radio": {
                "enabled": cfg.manual_radio.enabled,
                "profile": cfg.manual_radio.profile,
                "auto_start": cfg.manual_radio.auto_start,
                "profile_options": cfg.manual_radio.profile_options,
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


async def _pymc_tcp_rx_loop(*, adapter: SX1302Adapter, service: ModemService, pymc_tcp: PyMCTcpServer, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            packet = await adapter.receive()
            await service.handle_rx_packet(packet)
            await pymc_tcp.publish_rx_packet(packet)
        except RuntimeError as exc:
            if "not configured" not in str(exc):
                logger.exception("pyMC TCP RX loop runtime error")
            await asyncio.sleep(0.25)
        except Exception:
            logger.exception("pyMC TCP RX loop error")
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
    kiss = _make_kiss_endpoint(cfg) if cfg.transport == "kiss" else NullKissEndpoint()
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
    pymc_tcp = PyMCTcpServer(config=cfg, adapter=adapter, counters=counters, ring=ring, mqtt=mqtt) if cfg.transport == "pymc_tcp" else None
    dashboard = (
        DashboardServer(
            config=cfg,
            counters=counters,
            ring=ring,
            status_provider=_build_status_provider(cfg=cfg, adapter=adapter, mqtt=mqtt, counters=counters, pymc_tcp=pymc_tcp),
            config_path=config_path,
        )
        if cfg.dashboard.enabled
        else None
    )

    await adapter.configure(cfg.radio)
    if cfg.transport == "kiss":
        await kiss.start()
    await mqtt.start()
    if pymc_tcp:
        await pymc_tcp.start()
    if dashboard:
        dashboard.start()
    manual_profile_waiting = cfg.manual_radio.enabled and cfg.manual_radio.profile and not cfg.manual_radio.auto_start
    if not manual_profile_waiting:
        await adapter.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    tasks = [asyncio.create_task(_noise_scan_loop(adapter=adapter, stop=stop))]
    if cfg.transport == "kiss":
        tasks.append(asyncio.create_task(_kiss_loop(kiss=kiss, service=service, mqtt=mqtt, ring=ring, counters=counters, stop=stop)))
        tasks.append(asyncio.create_task(_rx_loop(adapter=adapter, service=service, stop=stop)))
    elif pymc_tcp:
        tasks.append(asyncio.create_task(_pymc_tcp_rx_loop(adapter=adapter, service=service, pymc_tcp=pymc_tcp, stop=stop)))
    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await adapter.stop()
        await mqtt.stop()
        if cfg.transport == "kiss":
            await kiss.stop()
        if pymc_tcp:
            await pymc_tcp.stop()
        if dashboard:
            dashboard.stop()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
