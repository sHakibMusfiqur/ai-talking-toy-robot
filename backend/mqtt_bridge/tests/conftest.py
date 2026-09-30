import socket
import uuid

import pytest
from django.conf import settings

from mqtt_bridge.provisioning import ensure_bridge_client, ensure_device_client
from mqtt_bridge.subscriber import start_bridge
from mqtt_bridge import topics

from .helpers import MqttTestClient

BROKER_START_CMD = (
    "docker compose --env-file .env -f infra/docker-compose.yml up -d mosquitto"
)


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
    ingestion tests publish real messages and observe real DB writes."""
    subscriber = start_bridge()
    yield subscriber
    subscriber.connection.disconnect()


@pytest.fixture
def oracle(broker):
    """A django-service subscriber over all three device→backend topics,
    used as the delivery oracle for ACL and publisher assertions."""
    client = MqttTestClient(
        username=settings.MQTT_BRIDGE_USERNAME,
        password=settings.MQTT_BRIDGE_PASSWORD,
    )
    client.connect()
    for topic, qos in topics.BACKEND_SUBSCRIPTIONS:
        codes = client.subscribe(topic, qos=qos)
        assert codes == [qos], f"backend subscription denied: {codes}"
    yield client
    client.disconnect()


@pytest.fixture
def make_device(broker):
    """Provision a unique broker client via the production provisioning code
    path and connect it (optionally subscribed to its own command/notify
    topics, exactly like firmware will)."""
    clients = []
    device_ids = []

    def _make(device_id=None, password="device-jwt-test", subscribe_to_own=True, will=None):
        device_id = device_id or str(uuid.uuid4())
        ensure_device_client(device_id, password)
        device_ids.append(device_id)
        client = MqttTestClient(username=device_id, password=password, will=will)
        client.connect()
        if subscribe_to_own:
            client.subscribe(f"robots/{device_id}/command", qos=1)
            for kind in topics.NOTIFY_KINDS:
                client.subscribe(f"robots/{device_id}/{kind}/notify", qos=1)
        client.device_id = device_id
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.disconnect()
    for device_id in device_ids:
        _deprovision(device_id)


def _deprovision(username):
    """Best-effort cleanup so repeated local runs do not accumulate users."""
    from mqtt_bridge.provisioning import _single

    try:
        _single({"command": "deleteClient", "username": username})
    except Exception:  # noqa: BLE001 - cleanup must never fail a test
        pass
