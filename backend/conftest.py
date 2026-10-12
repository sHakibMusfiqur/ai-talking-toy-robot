import socket

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from mqtt_bridge.provisioning import ensure_bridge_client
from mqtt_bridge.subscriber import start_bridge

BROKER_START_CMD = (
    "docker compose --env-file .env -f infra/docker-compose.yml up -d mosquitto"
)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture(scope="session")
def broker():
    if not settings.MQTT_ADMIN_PASSWORD or not settings.MQTT_BRIDGE_PASSWORD:
        pytest.fail(
            "MQTT_ADMIN_PASSWORD/MQTT_BRIDGE_PASSWORD are not configured — "
            "the repo-root .env must define them for Phase 8 tests."
        )
    try:
        with socket.create_connection(
            (settings.MQTT_HOST, settings.MQTT_PORT), timeout=3
        ):
            pass
    except OSError as exc:
        pytest.fail(
            f"Mosquitto is not reachable at "
            f"{settings.MQTT_HOST}:{settings.MQTT_PORT} ({exc}) — "
            f"start it with: {BROKER_START_CMD}"
        )
    try:
        ensure_bridge_client()
    except Exception as exc:  # pragma: no cover - failure diagnostics
        pytest.fail(f"could not provision the django-service client: {exc}")
    return settings


@pytest.fixture(scope="session")
def bridge(broker):
    """The production bridge subscriber, live for the whole session —
    ingestion tests publish real messages and observe real DB writes.

    Defined once in the ROOT conftest so mqtt_bridge and robot_sim tests
    resolve the same session-scoped instance. A duplicate definition (or a
    re-exported copy) in a child conftest would make pytest instantiate a
    SECOND bridge for the same process: both connections share the
    client_id ``django-bridge-{pid}`` and then endlessly take over each
    other's broker session, which loses device messages intermittently."""
    subscriber = start_bridge()
    yield subscriber
    subscriber.connection.disconnect()
