from pathlib import Path

from android_runner.cli import load_provider_config


def test_load_provider_config_expands_commands(tmp_path: Path):
    config = tmp_path / "provider.yaml"
    config.write_text(
        "serial: demo\n"
        "working_directory: C:/tools\n"
        "commands:\n"
        "  prepare: [node, gps-lab.mjs, prepare, --serial, '{serial}']\n"
        "  status: [node, gps-lab.mjs, status, --serial, '{serial}']\n"
        "  route: [node, gps-lab.mjs, route, --file, '{route}', --serial, '{serial}']\n"
        "  stop: [node, gps-lab.mjs, stop, --serial, '{serial}']\n",
        encoding="utf-8",
    )
    loaded = load_provider_config(config)
    assert loaded["serial"] == "demo"
    assert loaded["commands"]["route"][-1] == "{serial}"
