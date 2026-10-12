import json
import uuid

import pytest
from devices.models import Robot, RobotState
from mqtt_bridge import topics

from .helpers import wait_until


def _robot():
    # Ingestion is pairing-gated (Phase A / F-02): LWT/status flow requires
    # a paired robot.
    return Robot.objects.create(
        device_id=str(uuid.uuid4()), pairing_status=Robot.PAIRING_PAIRED
    )


@pytest.mark.django_db(transaction=True)
def test_abrupt_disconnect_publishes_lwt_offline_status(bridge, oracle, make_device):
    robot = _robot()
    status = topics.status_topic(robot.device_id)
    will = (status, json.dumps({"online": False}), 1, True)
    device = make_device(
        device_id=robot.device_id, subscribe_to_own=False, will=will
    )

    # Device is alive first so the LWT transition is observable:
    device.publish(status, {"online": True, "battery_percent": 80}, qos=1)
    assert wait_until(
        lambda: RobotState.objects.filter(robot=robot, online=True).exists(),
        timeout=6,
    ), "initial status not ingested"

    device.simulate_network_drop()

    def _lwt_messages():
        return [
            m
            for m in oracle.received(status)
            if json.loads(m["payload"]) == {"online": False}
        ]

    assert wait_until(lambda: bool(_lwt_messages()), timeout=8), (
        f"LWT was not published by the broker; oracle saw: "
        f"{[m['payload'] for m in oracle.received(status)]}"
    )

    assert wait_until(
        lambda: not RobotState.objects.get(robot=robot).online,
        timeout=6,
    ), "LWT offline status not ingested"


@pytest.mark.django_db(transaction=True)
def test_lwt_flows_through_update_status_service(bridge, make_device, monkeypatch):
    """Same-path proof: the LWT-triggered offline update goes through
    devices.services.update_status, not a special bridge-only writer."""
    from devices import services

    calls = []
    monkeypatch.setattr(
        services, "update_status", lambda **kwargs: calls.append(kwargs)
    )
    robot = _robot()
    status = topics.status_topic(robot.device_id)
    will = (status, json.dumps({"online": False}), 1, True)
    device = make_device(
        device_id=robot.device_id, subscribe_to_own=False, will=will
    )
    device.publish(status, {"online": True}, qos=1)
    assert wait_until(lambda: bool(calls), timeout=6)
    calls.clear()

    device.simulate_network_drop()
    assert wait_until(lambda: any(c.get("online") is False for c in calls), timeout=6), (
        "LWT did not reach update_status with online=False"
    )
    offline = [c for c in calls if c.get("online") is False]
    assert offline[0]["robot"].pk == robot.pk
