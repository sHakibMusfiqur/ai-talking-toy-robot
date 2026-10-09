"""Command line entry point.

Run from ``backend/``::

    ..\\.venv\\Scripts\\python.exe -m robot_sim

Environment (all optional, ``ROBOT_SIM_`` prefix; CLI flags win):
    ROBOT_SIM_BACKEND_URL       default http://127.0.0.1:8000
    ROBOT_SIM_MQTT_HOST         default 127.0.0.1
    ROBOT_SIM_MQTT_PORT         default 1883
    ROBOT_SIM_STATE_DIR         default sim_state (gitignored)
    ROBOT_SIM_HEARTBEAT_SECONDS default 30
    ROBOT_SIM_STATUS_SECONDS    default 60
    ROBOT_SIM_EXEC_MS           default 300 (simulated motion duration)

Secrets policy: the pairing code is printed exactly once to the console
on fresh registration, the JWT stays in memory, and nothing secret is
ever written to the log.
"""

import argparse
import logging
import os
import signal
import sys
import threading

from .backend_api import (
    DEFAULT_FIRMWARE_VERSION,
    BackendApi,
    BackendError,
    HttpxTransport,
)
from .identity import IdentityError, load_or_create_identity
from .simulator import RobotSimulator, SimulatorConfig

DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_MQTT_HOST = "127.0.0.1"
DEFAULT_MQTT_PORT = 1883
DEFAULT_STATE_DIR = "sim_state"


def _env(name, default):
    return os.environ.get(f"ROBOT_SIM_{name}", default)


def _typed_env(name, default, cast):
    raw = _env(name, None)
    if raw is None:
        return default
    try:
        return cast(raw)
    except ValueError:
        raise SystemExit(
            f"invalid ROBOT_SIM_{name} value {raw!r}; expected {cast.__name__}"
        ) from None


def build_parser():
    parser = argparse.ArgumentParser(
        prog="robot_sim",
        description=(
            "Software-in-the-loop robot simulator: drives the real "
            "controller -> Django -> MQTT -> device path without hardware."
        ),
    )
    parser.add_argument("--backend-url", default=_env("BACKEND_URL", DEFAULT_BACKEND_URL))
    parser.add_argument("--mqtt-host", default=_env("MQTT_HOST", DEFAULT_MQTT_HOST))
    parser.add_argument(
        "--mqtt-port",
        type=int,
        default=_typed_env("MQTT_PORT", DEFAULT_MQTT_PORT, int),
    )
    parser.add_argument("--state-dir", default=_env("STATE_DIR", DEFAULT_STATE_DIR))
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=_typed_env("HEARTBEAT_SECONDS", 30.0, float),
    )
    parser.add_argument(
        "--status-seconds",
        type=float,
        default=_typed_env("STATUS_SECONDS", 60.0, float),
    )
    parser.add_argument(
        "--exec-ms",
        type=float,
        default=_typed_env("EXEC_MS", 300.0, float),
    )
    parser.add_argument(
        "--firmware-version",
        default=_env("FIRMWARE_VERSION", DEFAULT_FIRMWARE_VERSION),
    )
    return parser


def _print_pairing_banner(pairing_code, device_id):
    lines = (
        "",
        "=" * 58,
        " Robot simulator registered",
        f" Device ID:      {device_id}",
        f" Pairing code:   {pairing_code}",
        " The pairing code is shown ONCE - pair this robot now",
        " (POST /api/v1/devices/pair/ or see docs/testing/robot-simulator.md)",
        "=" * 58,
        "",
    )
    for line in lines:
        print(line, flush=True)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    stop_event = threading.Event()
    signal_count = {"count": 0}

    def _handle_signal(signum, frame):  # noqa: ARG001 - signal API
        signal_count["count"] += 1
        if signal_count["count"] >= 2:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            return
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, OSError):  # not on the main thread (tests)
        pass

    try:
        identity = load_or_create_identity(args.state_dir)
    except IdentityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if identity.created:
        print(
            f"generated new simulator identity: {identity.device_id}",
            flush=True,
        )

    api = BackendApi(
        HttpxTransport(args.backend_url, timeout=10.0),
        identity,
        firmware_version=args.firmware_version,
    )
    config = SimulatorConfig(
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        heartbeat_seconds=args.heartbeat_seconds,
        status_seconds=args.status_seconds,
        exec_delay_seconds=max(0.0, args.exec_ms / 1000.0),
    )
    simulator = RobotSimulator(
        identity,
        api,
        config,
        on_pairing_code=lambda code: _print_pairing_banner(
            code, identity.device_id
        ),
    )

    try:
        simulator.start()
    except (BackendError, OSError) as exc:
        simulator.stop()
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        "simulator running (heartbeat "
        f"{args.heartbeat_seconds:g}s, status {args.status_seconds:g}s) - "
        "press Ctrl+C to stop",
        flush=True,
    )
    stop_event.wait()
    print("shutting down simulator...", flush=True)
    simulator.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via -m
    sys.exit(main())
