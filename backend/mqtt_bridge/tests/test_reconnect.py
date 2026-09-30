import socket
import subprocess
import uuid

import pytest
from django.conf import settings
from devices.models import Robot, RobotState
from mqtt_bridge.client import backoff_delay
from mqtt_bridge import topics

from .helpers import wait_until

BROKER_CONTAINER = "infra-mosquitto-1"


def test_backoff_is_exponential_and_bounded():
    assert backoff_delay(0, base=1, cap=60) == 1
    assert backoff_delay(1, base=1, cap=60) == 2
    assert backoff_delay(2, base=1, cap=60) == 4
    assert backoff_delay(3, base=1, cap=60) == 8
    # Bounded: never exceeds the configured cap, never drops below base.
    assert backoff_delay(30, base=1, cap=60) == 60
    assert backoff_delay(0, base=5, cap=60) == 5
    assert backoff_delay(99, base=5, cap=60) == 60
    # Negative/garbage attempts behave like the first attempt.
    assert backoff_delay(-3, base=1, cap=60) == 1


def test_backoff_uses_configuration_bounds():
    assert backoff_delay(0) == settings.MQTT_RECONNECT_MIN_DELAY_SECONDS
    assert backoff_delay(50) == settings.MQTT_RECONNECT_MAX_DELAY_SECONDS


def _robot():
    return Robot.objects.create(device_id=str(uuid.uuid4()))


def _publish_and_wait_state(device, robot, battery, timeout=8):
    device.publish(
        topics.status_topic(robot.device_id),
        {"online": True, "battery_percent": battery},
        qos=1,
    )
    return wait_until(
        lambda: RobotState.objects.filter(
            robot=robot, battery_percent=battery
        ).exists(),
        timeout=timeout,
    )


def _publish_with_retry_until_state(device, robot, battery, attempts=20):
    """Publish repeatedly until ingested — covers the brief window between
    broker-side disconnect and subscription re-registration after reconnect
    (at-least-once semantics; still fails if resubscribe never happens)."""
    for _ in range(attempts):
        device.publish(
            topics.status_topic(robot.device_id),
            {"online": True, "battery_percent": battery},
            qos=1,
        )
        if wait_until(
            lambda: RobotState.objects.filter(
                robot=robot, battery_percent=battery
            ).exists(),
            timeout=1.0,
        ):
            return True
    return False


@pytest.mark.django_db(transaction=True)
def test_bridge_recovers_after_network_drop_and_resubscribes(bridge, make_device):
    robot = _robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    assert _publish_and_wait_state(device, robot, 10), "initial status not ingested"

    bridge.connection.simulate_network_drop()
    # The bridge must first NOTICE the drop (is_connected can be stale-True
    # until the paho loop observes the closed socket; reconnect itself is
    # delayed at least MQTT_RECONNECT_MIN_DELAY_SECONDS):
    assert wait_until(
        lambda: not bridge.connection.is_connected(), timeout=10
    ), "bridge did not observe the network drop"
    assert wait_until(
        bridge.connection.is_connected, timeout=20
    ), "bridge did not auto-reconnect"

    # Arrival after reconnect proves on_connect re-applied subscriptions.
    assert _publish_with_retry_until_state(device, robot, 66), (
        "status published after reconnect was not ingested (resubscribe failed)"
    )


@pytest.mark.django_db(transaction=True)
def test_bridge_survives_full_broker_restart(bridge, make_device):
    robot = _robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    assert _publish_and_wait_state(device, robot, 20), "initial status not ingested"

    subprocess.run(
        ["docker", "restart", BROKER_CONTAINER],
        check=True,
        capture_output=True,
        timeout=90,
    )

    # Broker TCP back up:
    assert wait_until(
        lambda: _tcp_up(settings.MQTT_HOST, settings.MQTT_PORT), timeout=45
    ), "broker did not come back after restart"
    # The old device client died with the broker; reconnect it.
    from .helpers import MqttTestClient
    from mqtt_bridge.provisioning import ensure_device_client

    ensure_device_client(device.device_id, "device-jwt-test")
    device2 = MqttTestClient(username=device.device_id, password="device-jwt-test")
    device2.connect()
    # Bridge re-authenticates and re-subscribes within bounded backoff —
    # proven by ingestion succeeding (retry covers the re-registration gap):
    assert _publish_with_retry_until_state(device2, robot, 68, attempts=30), (
        "status published after broker restart was not ingested"
    )
    device2.disconnect()


def _tcp_up(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False
