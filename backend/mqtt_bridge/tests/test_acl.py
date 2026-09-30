import uuid

import pytest
from django.conf import settings
from mqtt_bridge import topics
from mqtt_bridge.provisioning import ensure_device_client

from .helpers import ConnectionRefused, MqttTestClient, wait_until

pytestmark = pytest.mark.django_db


def _refused(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ConnectionRefused as exc:
        return exc
    raise AssertionError("expected connection to be refused")


def test_anonymous_clients_are_rejected(broker):
    client = MqttTestClient(username=None, password=None)
    exc = _refused(client.connect)
    assert "not authorised" in exc.reason.lower() or "not authorized" in exc.reason.lower()


def test_wrong_password_is_rejected(broker):
    device_id = str(uuid.uuid4())
    ensure_device_client(device_id, "correct-password")
    client = MqttTestClient(username=device_id, password="wrong-password")
    exc = _refused(client.connect)
    assert "not authorised" in exc.reason.lower() or "not authorized" in exc.reason.lower()


def test_unknown_user_is_rejected(broker):
    client = MqttTestClient(username=str(uuid.uuid4()), password="whatever")
    _refused(client.connect)


def test_device_subscribes_own_topics(broker, make_device):
    device = make_device()
    codes = device.subscribe(f"robots/{device.device_id}/command", qos=1)
    assert codes == [1]
    for kind in topics.NOTIFY_KINDS:
        codes = device.subscribe(f"robots/{device.device_id}/{kind}/notify", qos=1)
        assert codes == [1]


def test_device_cannot_subscribe_to_another_device(broker, make_device):
    device_a = make_device()
    device_b_id = str(uuid.uuid4())
    codes = device_a.subscribe(f"robots/{device_b_id}/command", qos=1)
    assert codes == [128], f"cross-device subscribe unexpectedly granted: {codes}"


def test_device_cannot_subscribe_wildcards(broker, make_device):
    device = make_device()
    for pattern in ("robots/#", "robots/+/command", "robots/+/status", "#", "+/+/#"):
        codes = device.subscribe(pattern, qos=1)
        assert codes == [128], f"wildcard subscribe granted for {pattern}: {codes}"


def test_django_cannot_subscribe_command_or_notify(broker, oracle):
    for pattern in ("robots/+/command", "robots/+/config/notify"):
        codes = oracle.subscribe(pattern, qos=1)
        assert codes == [128], f"backend subscribe unexpectedly granted: {pattern}"


def test_device_publishes_own_status_delivered(broker, oracle, make_device):
    device = make_device(subscribe_to_own=False)
    topic = topics.status_topic(device.device_id)
    device.publish(topic, {"online": True, "battery_percent": 55}, qos=1)
    received = oracle.wait_received(topic, timeout=5)
    assert received, "own-device status was not delivered to the backend oracle"


def test_device_cannot_publish_to_another_device(broker, oracle, make_device):
    """The required cross-device negative test (Phase 8 DoD): device A
    publishing device B's status/heartbeat/event is never delivered —
    per-device ACL isolation is enforced by the broker, not convention."""
    device_a = make_device(subscribe_to_own=False)
    device_b_id = str(uuid.uuid4())
    for category, payload, qos in (
        ("status", {"online": False}, 1),
        ("heartbeat", b"", 0),
        ("event", {"event_type": "spoofed"}, 1),
    ):
        topic = f"robots/{device_b_id}/{category}"
        device_a.publish(topic, payload, qos=qos)
        assert not wait_until(
            lambda t=topic: bool(oracle.received(t)), timeout=1.5
        ), f"cross-device {category} was delivered: {topic}"
        assert not oracle.received(topic)


def test_device_cannot_publish_its_own_command(broker, make_device):
    device = make_device(subscribe_to_own=True)
    topic = topics.command_topic(device.device_id)
    device.publish(topic, {"action": "self-command"}, qos=1)
    assert not wait_until(lambda: bool(device.received(topic)), timeout=1.5)


def test_django_cannot_publish_fake_status(broker, oracle):
    """Section 11.2: Django never publishes status/event on a robot's
    behalf — ACL-enforced, not just convention."""
    client = MqttTestClient(
        username=settings.MQTT_BRIDGE_USERNAME,
        password=settings.MQTT_BRIDGE_PASSWORD,
    )
    client.connect()
    try:
        device_id = str(uuid.uuid4())
        topic = topics.status_topic(device_id)
        client.publish(topic, {"online": True}, qos=1)
        assert not wait_until(
            lambda: bool(oracle.received(topic)), timeout=1.5
        ), "fake backend status was delivered"
    finally:
        client.disconnect()
