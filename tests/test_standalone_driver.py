from pathlib import Path


def test_adapter_uses_daemon_local_sx1302_radio_by_default():
    from sx1302_meshcore_kiss.sx1302.adapter import SX1302Adapter
    from sx1302_meshcore_kiss.sx1302.radio import SX1302Radio

    adapter = SX1302Adapter()

    assert adapter.radio_factory is SX1302Radio


def test_daemon_sources_do_not_import_pymc_core():
    src = Path("src/sx1302_meshcore_kiss")
    offenders = []
    for path in src.rglob("*.py"):
        text = path.read_text()
        if "pymc_core" in text:
            offenders.append(str(path))

    assert offenders == []


def test_pyproject_has_no_pymc_core_runtime_extra():
    text = Path("pyproject.toml").read_text()

    assert "pymc_core" not in text
    assert "pymc-core" not in text
