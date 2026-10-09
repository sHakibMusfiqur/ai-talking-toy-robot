"""CLI wiring tests: environment/flag resolution and the pairing banner
(the start-failure path is covered by the simulator integration tests)."""

import pytest

from robot_sim import cli


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "BACKEND_URL",
        "MQTT_HOST",
        "MQTT_PORT",
        "STATE_DIR",
        "HEARTBEAT_SECONDS",
        "STATUS_SECONDS",
        "EXEC_MS",
        "FIRMWARE_VERSION",
    ):
        monkeypatch.delenv(f"ROBOT_SIM_{name}", raising=False)


def test_parser_defaults():
    args = cli.build_parser().parse_args([])
    assert args.backend_url == "http://127.0.0.1:8000"
    assert args.mqtt_host == "127.0.0.1"
    assert args.mqtt_port == 1883
    assert args.state_dir == "sim_state"
    assert args.heartbeat_seconds == 30.0
    assert args.status_seconds == 60.0
    assert args.exec_ms == 300.0
    assert args.firmware_version == "sim-0.1.0"


def test_parser_env_overrides(monkeypatch):
    monkeypatch.setenv("ROBOT_SIM_MQTT_PORT", "1884")
    monkeypatch.setenv("ROBOT_SIM_BACKEND_URL", "http://example:8080")
    args = cli.build_parser().parse_args([])
    assert args.mqtt_port == 1884
    assert args.backend_url == "http://example:8080"


def test_parser_flags_beat_env(monkeypatch):
    monkeypatch.setenv("ROBOT_SIM_MQTT_PORT", "1884")
    args = cli.build_parser().parse_args(["--mqtt-port", "1900"])
    assert args.mqtt_port == 1900


def test_bad_env_value_exits_with_clear_message(monkeypatch):
    monkeypatch.setenv("ROBOT_SIM_MQTT_PORT", "not-a-port")
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser()
    assert "ROBOT_SIM_MQTT_PORT" in str(excinfo.value.code)


def test_pairing_banner_prints_code_exactly_once(capsys):
    cli._print_pairing_banner("CODE1234", "device-1")
    out = capsys.readouterr().out
    assert out.count("CODE1234") == 1
    assert "device-1" in out
    assert "ONCE" in out
