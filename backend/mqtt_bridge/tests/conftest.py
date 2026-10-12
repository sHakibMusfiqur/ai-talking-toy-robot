import uuid

import pytest
from django.conf import settings

from mqtt_bridge.provisioning import ensure_device_client
from mqtt_bridge import topics

from .helpers import MqttTestClient

# ``broker``/``bridge`` live in the ROOT conftest (backend/conftest.py):
# session fixtures must be defined exactly once per pytest session or pytest
# would instantiate a second live bridge with the same client_id, and the
# two connections would take over each other's broker session forever.


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
