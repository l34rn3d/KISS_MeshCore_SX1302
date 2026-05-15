from pathlib import Path
import tomllib


def test_pyproject_has_installable_metadata_and_runtime_dependencies():
    data = tomllib.loads(Path("pyproject.toml").read_text())
    project = data["project"]

    assert project["name"] == "sx1302-meshcore-kiss"
    assert "README.md" == project["readme"]
    assert project["license"]["text"] == "MIT"
    assert "sx1302-meshcore-kiss" in project["scripts"]
    assert any(dep.startswith("pymc-core") for dep in project["optional-dependencies"]["runtime"])


def test_install_docs_cover_uv_systemd_and_hardware_permissions():
    text = Path("docs/install.md").read_text().lower()

    assert "uv venv" in text
    assert "systemctl enable" in text
    assert "spi" in text
    assert "gpio" in text
    assert "/etc/sx1302-meshcore-kiss/config.yaml" in text
