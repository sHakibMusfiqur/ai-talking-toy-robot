"""Phase A (F-08) live acceptance: a command publish that fails while the
broker is down must NEVER be delivered after the broker comes back, and the
next publish must work normally. Exercises the real paho client against the
real Mosquitto container (docker stop/start)."""

import json
import socket
import subprocess
import time
import uuid

import pytest
from django.conf import settings
from devices.models import Robot, RobotState
from mqtt_bridge import topics
from mqtt_bridge.client import MqttError
from mqtt_bridge.publisher import get_publisher, reset_publisher

from .helpers import MqttTestClient, wait_until

BROKER_CONTAINER = "infra-mosquitto-1"

pytestmark = pytest.mark.django_db(transaction=True)


def _paired_robot():
    return Robot.objects.create(
        device_id=str(uuid.uuid4()), pairing_status=Robot.PAIRING_PAIRED
    )


def _tcp_up(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


@pytest.fixture
def live_publisher(broker):
    reset_publisher()
    yield get_publisher()
    reset_publisher()


def _fresh_subscriber(device_id, password="device-jwt-test"):
    """A brand-new broker client subscribed to the command topic — used to
    observe what arrives AFTER the broker restarts (the pre-restart client
    dies with the broker and cannot witness a zombie delivery)."""
    from mqtt_bridge.provisioning import ensure_device_client

    def _provision_ok():
        try:
            ensure_device_client(device_id, password)
            return True
        except Exception:
            return False

    assert wait_until(_provision_ok, timeout=30), (
        "device client could not be re-provisioned after broker restart"
    )
    client = MqttTestClient(username=device_id, password=password)
    client.connect(timeout=10)
    client.subscribe(topics.command_topic(device_id), qos=1)
    return client


def test_failed_publish_is_never_delivered_after_broker_restart(
    live_publisher, make_device, bridge
):
    robot = _paired_robot()
    device = make_device(device_id=robot.device_id)
    command_topic = topics.command_topic(robot.device_id)
    device.subscribe(command_topic, qos=1)

    # Sanity: the path works while the broker is up.
    live_publisher.publish_command(robot, {"action": "WAVE"})
    received = device.wait_received(command_topic, timeout=6)
    assert received, "sanity command was not delivered"
    assert json.loads(received[0]["payload"]) == {"action": "WAVE"}

    # Kill the broker. Publish immediately: paho may not have observed the
    # drop yet (stale is_connected), which is exactly the enqueue-then-fail
    # path the poison logic exists for; either path must raise MqttError
    # and must never deliver later.
    subprocess.run(
        ["docker", "stop", BROKER_CONTAINER],
        check=True,
        capture_output=True,
        timeout=90,
    )
    with pytest.raises(MqttError):
        live_publisher.publish_command(robot, {"action": "NEVER"})

    # Bring the broker back.
    subprocess.run(
        ["docker", "start", BROKER_CONTAINER],
        check=True,
        capture_output=True,
        timeout=90,
    )
    assert wait_until(
        lambda: _tcp_up(settings.MQTT_HOST, settings.MQTT_PORT), timeout=45
    ), "broker did not come back"

    # A live subscriber AFTER the restart is the only honest witness for a
    # zombie QoS 1 re-delivery (the old device died with the broker).
    observer = _fresh_subscriber(robot.device_id)
    try:
        # Generous window: if the poisoned connection auto-reconnected and
        # re-sent the queued message, it would arrive here.
        time.sleep(3)
        payloads = [
            json.loads(m["payload"]) for m in observer.received(command_topic)
        ]
        assert "NEVER" not in payloads, (
            f"a command published while the broker was down was delivered "
            f"after restart: {payloads}"
        )

        # The publisher recovers on next use: get_publisher discards the
        # dead/poisoned singleton and builds a fresh connection.
        def _publish_fresh():
            try:
                get_publisher().publish_command(robot, {"action": "WAVE"})
                return True
            except MqttError:
                return False

        assert wait_until(_publish_fresh, timeout=30, interval=1.0), (
            "post-recovery publish never succeeded"
        )
        received = wait_until(
            lambda: [
                m
                for m in observer.received(command_topic)
                if json.loads(m["payload"]) == {"action": "WAVE"}
            ],
            timeout=8,
        )
        assert received, "post-recovery command was not delivered"
    finally:
        observer.disconnect()

    # Leave the broker/session in a state the rest of the suite can rely
    # on: the session-scoped bridge subscriber must be fully back — not
    # merely TCP-connected, but proven to ingest again — before this test
    # finishes. Later suites (e.g. the simulator integration test) ingest
    # through that same bridge; messages published while it is still in
    # reconnect backoff are lost (clean_session=True).
    status_device = make_device(device_id=robot.device_id, subscribe_to_own=False)

    def _bridge_ingested_a_fresh_status():
        status_device.publish(
            topics.status_topic(robot.device_id),
            {"online": True, "battery_percent": 99},
            qos=1,
        )
        return wait_until(
            lambda: RobotState.objects.filter(
                robot=robot, battery_percent=99
            ).exists(),
            timeout=2.0,
        )

    assert wait_until(_bridge_ingested_a_fresh_status, timeout=45, interval=2.0), (
        "bridge subscriber did not resume ingestion after the broker restart"
    )
