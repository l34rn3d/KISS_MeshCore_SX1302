import pytest

from sx1302_meshcore_kiss.sx1302.adapter import SX1302Adapter
from sx1302_meshcore_kiss.sx1302.metadata import RadioConfig, TxPacket


class FakeRadio:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.begin_count = 0
        FakeRadio.instances.append(self)

    def begin(self):
        self.begin_count += 1
        self.started = True

    def cleanup(self):
        self.started = False

    async def send(self, payload: bytes):
        if not self.started:
            raise RuntimeError("FakeRadio.send() called before begin()")
        return {"success": True, "airtime_ms": 12.0}


@pytest.mark.asyncio
async def test_reconfigure_after_start_restarts_replacement_radio_before_tx():
    FakeRadio.instances = []
    adapter = SX1302Adapter(radio_factory=FakeRadio)

    await adapter.configure(RadioConfig(frequency_hz=916_575_000, bandwidth_hz=62_500, spreading_factor=7, coding_rate=8))
    await adapter.start()

    await adapter.configure(RadioConfig(frequency_hz=916_575_000, bandwidth_hz=62_500, spreading_factor=7, coding_rate=8, tx_power_dbm=26))
    result = await adapter.transmit(
        TxPacket(
            payload=b"hello",
            frequency_hz=916_575_000,
            bandwidth_hz=62_500,
            spreading_factor=7,
            coding_rate=8,
            tx_power_dbm=26,
        )
    )

    assert len(FakeRadio.instances) == 1
    assert FakeRadio.instances[-1].started is True
    assert result.ok is True
    assert result.error is None


@pytest.mark.asyncio
async def test_reconfigure_with_same_config_does_not_recreate_or_restart_radio():
    FakeRadio.instances = []
    adapter = SX1302Adapter(radio_factory=FakeRadio)
    config = RadioConfig(frequency_hz=916_575_000, bandwidth_hz=62_500, spreading_factor=7, coding_rate=8, tx_power_dbm=26)

    await adapter.configure(config)
    await adapter.start()
    await adapter.configure(config)
    await adapter.start()

    assert len(FakeRadio.instances) == 1
    assert FakeRadio.instances[0].begin_count == 1


@pytest.mark.asyncio
async def test_reconfigure_signature_handles_dict_and_list_values():
    FakeRadio.instances = []
    adapter = SX1302Adapter(radio_factory=FakeRadio)
    config = RadioConfig(
        frequency_hz=916_575_000,
        bandwidth_hz=62_500,
        spreading_factor=7,
        coding_rate=8,
        tx_power_dbm=26,
        reset_script_args=["start"],
        reset_script_env={"CONCENTRATOR_RESET_PIN": "17"},
    )

    await adapter.configure(config)
    await adapter.configure(config)

    assert len(FakeRadio.instances) == 1
