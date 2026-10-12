import json
import uuid

import pytest
from devices.models import Robot, RobotEvent, RobotState
from mqtt_bridge import topics

from .helpers import wait_until


def _robot(device_id=None):
    device_id = device_id or str(uuid.uuid4())
    # Ingestion is pairing-gated (Phase A / F-02): these tests exercise the
    # post-pairing path, so the robot must already be paired.
    return Robot.objects.create(
        device_id=device_id, pairing_status=Robot.PAIRING_PAIRED
    )


def _status_topic(robot):
    return topics.status_topic(robot.device_id)


def _wait_state(robot, timeout=6):
    return wait_until(
        lambda: RobotState.objects.filter(robot=robot).exists(), timeout=timeout
    )


@pytest.mark.django_db(transaction=True)
def test_status_publish_is_persisted(bridge, make_device):
    robot = _robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    device.publish(
        _status_topic(robot),
        {"online": True, "state": "chatting", "battery_percent": 77, "rssi": -55},
        qos=1,
    )
    assert _wait_state(robot), "status was not ingested"
    state = RobotState.objects.get(robot=robot)
    assert state.online is True
    assert state.state == "chatting"
    assert state.battery_percent == 77
    assert state.rssi == -55
    robot.refresh_from_db()
    assert robot.last_seen is not None


@pytest.mark.django_db(transaction=True)
def test_status_partial_update_keeps_previous_values(bridge, make_device):
    robot = _robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    device.publish(
        _status_topic(robot),
        {"online": True, "state": "chatting", "battery_percent": 77, "rssi": -55},
        qos=1,
    )
    assert _wait_state(robot)
    device.publish(_status_topic(robot), {"battery_percent": 40}, qos=1)
    assert wait_until(
        lambda: RobotState.objects.get(robot=robot).battery_percent == 40,
        timeout=6,
    ), "partial status update not applied"
    state = RobotState.objects.get(robot=robot)
    assert state.online is True
    assert state.state == "chatting"
    assert state.rssi == -55


@pytest.mark.django_db(transaction=True)
def test_malformed_status_payloads_are_dropped_and_bridge_survives(bridge, make_device):
    robot = _robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    topic = _status_topic(robot)
    invalid_payloads = [
        b"{not json",
        json.dumps(["not", "an", "object"]).encode(),
        json.dumps({"online": "yes"}).encode(),              # wrong type
        json.dumps({"robot_id": "other-device"}).encode(),   # unknown field (identity is topic-only)
        json.dumps({"battery_percent": 500}).encode(),       # out of range
        json.dumps({}).encode(),                             # no status fields
        b"\xff\xfe\x00garbage",                              # invalid UTF-8
    ]
    for payload in invalid_payloads:
        device.publish(topic, payload, qos=1)
    assert not wait_until(
        lambda: RobotState.objects.filter(robot=robot).exists(), timeout=1.5
    ), "a malformed status payload was persisted"
    # The bridge must still be alive and processing:
    device.publish(topic, {"online": True}, qos=1)
    assert _wait_state(robot), "bridge stopped processing after malformed payloads"
    state = RobotState.objects.get(robot=robot)
    assert state.online is True


@pytest.mark.django_db(transaction=True)
def test_status_for_unknown_device_is_dropped(bridge, make_device):
    known_robot = _robot()
    known_device = make_device(device_id=known_robot.device_id, subscribe_to_own=False)
    # A correctly ACL-scoped device whose device_id has no Robot row yet:
    stranger_device = make_device(subscribe_to_own=False)
    stranger_id = stranger_device.device_id
    stranger_device.publish(f"robots/{stranger_id}/status", {"online": True}, qos=1)
    assert not wait_until(
        lambda: RobotState.objects.filter(robot=known_robot).exists(), timeout=1.2
    )
    # Bridge is still alive and correctly routes the known device:
    known_device.publish(_status_topic(known_robot), {"online": True}, qos=1)
    assert _wait_state(known_robot)


@pytest.mark.django_db(transaction=True)
def test_heartbeat_marks_online_and_refreshes_last_seen(bridge, make_device):
    robot = _robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    device.publish(topics.heartbeat_topic(robot.device_id), b"", qos=0)
    assert wait_until(
        lambda: RobotState.objects.filter(robot=robot, online=True).exists(),
        timeout=6,
    ), "heartbeat not ingested"
    robot.refresh_from_db()
    assert robot.last_seen is not None


@pytest.mark.django_db(transaction=True)
def test_heartbeat_with_fields_is_rejected(bridge, make_device):
    robot = _robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    topic = topics.heartbeat_topic(robot.device_id)
    device.publish(topic, json.dumps({"battery_percent": 10}), qos=0)
    assert not wait_until(
        lambda: RobotState.objects.filter(robot=robot).exists(), timeout=1.2
    )
    device.publish(topic, b"{}", qos=0)
    assert wait_until(
        lambda: RobotState.objects.filter(robot=robot, online=True).exists(),
        timeout=6,
    ), "valid heartbeat not ingested after rejected one"


@pytest.mark.django_db(transaction=True)
def test_event_is_recorded_with_payload(bridge, make_device):
    robot = _robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    device.publish(
        topics.event_topic(robot.device_id),
        {"event_type": "action_completed", "payload": {"duration_ms": 500}},
        qos=1,
    )
    assert wait_until(
        lambda: RobotEvent.objects.filter(robot=robot).exists(), timeout=6
    ), "event not ingested"
    event = RobotEvent.objects.get(robot=robot)
    assert event.event_type == "action_completed"
    assert event.payload == {"duration_ms": 500}
    robot.refresh_from_db()
    assert robot.last_seen is not None


@pytest.mark.django_db(transaction=True)
def test_malformed_events_are_dropped_and_bridge_survives(bridge, make_device):
    robot = _robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    topic = topics.event_topic(robot.device_id)
    invalid_payloads = [
        b"not json",
        json.dumps({"event_type": "bad type with spaces"}).encode(),
        json.dumps({"event_type": ""}).encode(),
        json.dumps({"event_type": "ok", "robot_id": "spoof"}).encode(),  # unknown field
        json.dumps({"event_type": "ok", "payload": [1, 2]}).encode(),    # non-object payload
    ]
    for payload in invalid_payloads:
        device.publish(topic, payload, qos=1)
    assert not wait_until(
        lambda: RobotEvent.objects.filter(robot=robot).exists(), timeout=1.5
    )
    device.publish(topic, {"event_type": "valid_event"}, qos=1)
    assert wait_until(
        lambda: RobotEvent.objects.filter(robot=robot).exists(), timeout=6
    ), "bridge stopped processing after malformed events"
    assert RobotEvent.objects.get(robot=robot).event_type == "valid_event"


@pytest.mark.django_db(transaction=True)
def test_bridge_routes_status_through_service_layer(bridge, make_device, monkeypatch):
    """Section 12.2: MQTT ingestion calls devices.services.update_status --
    the same function REST uses -- never direct model writes."""
    from devices import services

    calls = []
    monkeypatch.setattr(
        services, "update_status", lambda **kwargs: calls.append(kwargs)
    )
    robot = _robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    device.publish(_status_topic(robot), {"online": True, "battery_percent": 9}, qos=1)
    assert wait_until(lambda: bool(calls), timeout=6)
    assert calls[0]["robot"].pk == robot.pk
    assert calls[0]["online"] is True
    assert calls[0]["battery_percent"] == 9


@pytest.mark.django_db(transaction=True)
def test_bridge_routes_heartbeat_through_service_layer(bridge, make_device, monkeypatch):
    from devices import services

    calls = []
    monkeypatch.setattr(
        services, "record_heartbeat", lambda **kwargs: calls.append(kwargs)
    )
    robot = _robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    device.publish(topics.heartbeat_topic(robot.device_id), b"", qos=0)
    assert wait_until(lambda: bool(calls), timeout=6)
    assert calls[0]["robot"].pk == robot.pk


@pytest.mark.django_db(transaction=True)
def test_bridge_routes_event_through_service_layer(bridge, make_device, monkeypatch):
    from devices import services

    calls = []
    monkeypatch.setattr(
        services, "record_robot_event", lambda **kwargs: calls.append(kwargs)
    )
    robot = _robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    device.publish(
        topics.event_topic(robot.device_id),
        {"event_type": "safety_stop", "payload": {"reason": "test"}},
        qos=1,
    )
    assert wait_until(lambda: bool(calls), timeout=6)
    assert calls[0]["robot"].pk == robot.pk
    assert calls[0]["event_type"] == "safety_stop"
    assert calls[0]["payload"] == {"reason": "test"}


# --- Pairing gate (Phase A, F-02): unpaired devices never ingested --------


def _unpaired_robot(device_id=None):
    device_id = device_id or str(uuid.uuid4())
    return Robot.objects.create(
        device_id=device_id, pairing_status=Robot.PAIRING_UNPAIRED
    )


@pytest.mark.django_db(transaction=True)
def test_unpaired_device_status_is_dropped(bridge, make_device):
    robot = _unpaired_robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    device.publish(_status_topic(robot), {"online": True}, qos=1)
    assert not wait_until(
        lambda: RobotState.objects.filter(robot=robot).exists(), timeout=1.5
    ), "unpaired device status was ingested"
    robot.refresh_from_db()
    assert robot.last_seen is None, "unpaired publish must not touch last_seen"


@pytest.mark.django_db(transaction=True)
def test_unpaired_device_heartbeat_is_dropped(bridge, make_device):
    robot = _unpaired_robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    device.publish(topics.heartbeat_topic(robot.device_id), b"", qos=0)
    assert not wait_until(
        lambda: RobotState.objects.filter(robot=robot).exists(), timeout=1.5
    ), "unpaired device heartbeat was ingested"


@pytest.mark.django_db(transaction=True)
def test_unpaired_device_event_is_dropped(bridge, make_device):
    robot = _unpaired_robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)
    device.publish(
        topics.event_topic(robot.device_id),
        {"event_type": "firmware.started"},
        qos=1,
    )
    assert not wait_until(
        lambda: RobotEvent.objects.filter(robot=robot).exists(), timeout=1.5
    ), "unpaired device event was ingested"


@pytest.mark.django_db(transaction=True)
def test_pairing_gates_all_three_categories_then_resumes_after_pairing(
    bridge, make_device
):
    """Registered-but-unpaired device publishes all three categories: all
    dropped. After the robot is marked paired, the same device's messages
    are ingested (the gate is state-based, not connection-based)."""
    robot = _unpaired_robot()
    device = make_device(device_id=robot.device_id, subscribe_to_own=False)

    device.publish(_status_topic(robot), {"online": True}, qos=1)
    device.publish(topics.heartbeat_topic(robot.device_id), b"", qos=0)
    device.publish(
        topics.event_topic(robot.device_id), {"event_type": "boot"}, qos=1
    )
    assert not wait_until(
        lambda: RobotState.objects.filter(robot=robot).exists(), timeout=1.5
    )
    assert not wait_until(
        lambda: RobotEvent.objects.filter(robot=robot).exists(), timeout=1.2
    )

    robot.pairing_status = Robot.PAIRING_PAIRED
    robot.save(update_fields=["pairing_status"])

    device.publish(_status_topic(robot), {"online": True, "battery_percent": 50}, qos=1)
    assert _wait_state(robot), "paired device status not ingested after pairing"
    device.publish(
        topics.event_topic(robot.device_id), {"event_type": "post_pair"}, qos=1
    )
    assert wait_until(
        lambda: RobotEvent.objects.filter(robot=robot).exists(), timeout=6
    ), "paired device event not ingested after pairing"
