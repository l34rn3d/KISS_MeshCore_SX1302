from __future__ import annotations
import argparse, asyncio, logging, signal, time
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

async def run(config_path: str) -> None:
    cfg=load_config(config_path); logging.basicConfig(level=getattr(logging, cfg.logging.level.upper(), logging.INFO))
    counters=Counters(); ring=PacketRingBuffer(cfg.dashboard.max_packet_events); adapter=SX1302Adapter(); mqtt=MqttPublisher(**cfg.mqtt.__dict__)
    kiss = PtyEndpoint(symlink=cfg.kiss.symlink) if cfg.kiss.mode == 'pty' else SerialEndpoint(port=cfg.kiss.serial_port, baud_rate=cfg.kiss.baud_rate)
    service=ModemService(node_id=cfg.node_id, radio_config=cfg.radio, sx1302=adapter, kiss=kiss, mqtt=mqtt, ring=ring, counters=counters, forward_unknown_crc=cfg.crc.forward_unknown_crc)
    dashboard = DashboardServer(config=cfg, counters=counters, ring=ring, status_provider=lambda: {'mqtt_connected': mqtt.connected}) if cfg.dashboard.enabled else None
    await adapter.configure(cfg.radio); await kiss.start(); await mqtt.start();
    if dashboard: dashboard.start()
    await adapter.start()
    stop=asyncio.Event(); loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(sig, stop.set)
    async def kiss_loop():
        while not stop.is_set():
            try:
                for frame in kiss.codec.feed(await kiss.read_bytes()): await service.handle_kiss_frame(frame)
            except KissDecodeError as exc:
                counters.kiss_decode_error_count += 1; await mqtt.publish_event('kiss/error', {'status':'decode_error','error':str(exc)}); ring.add({'direction':'kiss','status':'decode_error','error':str(exc)})
    async def rx_loop():
        while not stop.is_set(): await service.handle_rx_packet(await adapter.receive())
    tasks=[asyncio.create_task(kiss_loop()), asyncio.create_task(rx_loop())]
    try: await stop.wait()
    finally:
        for t in tasks: t.cancel()
        await adapter.stop(); await mqtt.stop(); await kiss.stop();
        if dashboard: dashboard.stop()

def main(argv=None) -> None:
    p=argparse.ArgumentParser(); p.add_argument('--config', required=True); args=p.parse_args(argv); asyncio.run(run(args.config))

if __name__ == '__main__': main()
