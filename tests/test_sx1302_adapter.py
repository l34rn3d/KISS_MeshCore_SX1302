import asyncio

import pytest

from sx1302_meshcore_kiss.sx1302.adapter import SX1302Adapter
from sx1302_meshcore_kiss.sx1302.metadata import RadioConfig, RxPacket, TxPacket


class FakeRadio:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.began = False
        self.cleaned = False
        self.sent = []
        self.rx_queue = asyncio.Queue()
        self.status = {"started": True, "last_rssi": -120, "last_snr": 0.0}

    def begin(self):
        self.began = True

    def cleanup(self):
        self.cleaned = True

    async def send(self, payload):
        self.sent.append(payload)
        return {"success": True, "airtime_ms": 42, "tx_status": "free", "freq_hz": self.kwargs.get("frequency")}

    async def wait_for_rx(self):
        return await self.rx_queue.get()

    def get_last_signal_rssi(self):
        return -98

    def get_last_snr(self):
        return 6.25

    def get_status(self):
        return self.status


def test_configure_instantiates_sx1302radio_with_config_values():
    created = []
    adapter = SX1302Adapter(radio_factory=lambda **kwargs: created.append(kwargs) or FakeRadio(**kwargs))
    config = RadioConfig(frequency_hz=915000000, bandwidth_hz=62500, spreading_factor=8, coding_rate=5, tx_power_dbm=14, spi_device="/dev/spidev9.0")

    asyncio.run(adapter.configure(config))

    assert created[0]["frequency"] == 915000000
    assert created[0]["bandwidth"] == 62500
    assert created[0]["spreading_factor"] == 8
    assert created[0]["coding_rate"] == 5
    assert created[0]["tx_power"] == 14
    assert created[0]["com_path"] == "/dev/spidev9.0"


@pytest.mark.asyncio
async def test_start_stop_and_transmit_delegate_to_radio():
    radio = FakeRadio(frequency=915000000)
    adapter = SX1302Adapter(radio_factory=lambda **kwargs: radio)
    await adapter.configure(RadioConfig(frequency_hz=915000000, bandwidth_hz=125000, spreading_factor=8, coding_rate=5, tx_power_dbm=14))

    await adapter.start()
    result = await adapter.transmit(TxPacket(payload=b"abc", frequency_hz=915000000, bandwidth_hz=125000, spreading_factor=8, coding_rate=5, tx_power_dbm=14))
    await adapter.stop()

    assert radio.began is True
    assert radio.sent == [b"abc"]
    assert result.ok is True
    assert result.airtime_ms == 42
    assert radio.cleaned is True


@pytest.mark.asyncio
async def test_receive_normalizes_payload_and_last_signal_metadata():
    radio = FakeRadio()
    adapter = SX1302Adapter(radio_factory=lambda **kwargs: radio)
    await adapter.configure(RadioConfig(frequency_hz=915000000, bandwidth_hz=125000, spreading_factor=8, coding_rate=5, tx_power_dbm=14))
    radio.rx_queue.put_nowait(b"abc")

    pkt = await adapter.receive()

    assert isinstance(pkt, RxPacket)
    assert pkt.payload == b"abc"
    assert pkt.crc_ok is True
    assert pkt.frequency_hz == 915000000
    assert pkt.rssi_dbm == -98
    assert pkt.snr_db == 6.25
