import json

import pytest
from devices.models import Robot
from mqtt_bridge import topics
from mqtt_bridge.publisher import (
    MqttPublisher,
    publish_config_notify,
    publish_content_notify,
    publish_ota_notify,
    reset_publisher,
)

from .helpers import wait_until


class RecordingConnection:
    def __init__(self):
        self.calls = []

    def publish(self, topic, payload, *, qos, retain=False, timeout=None):
        self.calls.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain}
        )


@pytest.fixture
def recording_publisher():
    return MqttPublisher(RecordingConnection())


def _robot(device_id=None):
    import uuid

    device_id = device_id or str(uuid.uuid4())
    return Robot.objects.create(device_id=device_id)


def test_publish_command_uses_exact_topic_qos1_not_retained(recording_publisher):
    robot = _robot()
    recording_publisher.publish_command(robot, {"action": "wave"})
    call = recording_publisher.connection.calls[0]
    assert call["topic"] == f"robots/{robot.device_id}/command"
    assert call["qos"] == 1
    assert call["retain"] is False
    assert json.loads(call["payload"]) == {"action": "wave"}


@pytest.mark.parametrize("kind,version", [("config", 7), ("ota", 3), ("content", 5)])
def test_publish_notify_uses_exact_topic_retained(recording_publisher, kind, version):
    robot = _robot()
    recording_publisher.publish_notify(robot, kind, version)
    call = recording_publisher.connection.calls[0]
    assert call["topic"] == f"robots/{robot.device_id}/{kind}/notify"
    assert call["qos"] == 1
    assert call["retain"] is True
    assert json.loads(call["payload"]) == {"version": version}


def test_publish_never_emits_wildcards(recording_publisher):
    robot = _robot()
    recording_publisher.publish_command(robot, {"action": "dance"})
    for kind in ("config", "ota", "content"):
        recording_publisher.publish_notify(robot, kind, 1)
    for call in recording_publisher.connection.calls:
        assert "#" not in call["topic"]
        assert "+" not in call["topic"]


def test_invalid_command_envelopes_rejected(recording_publisher):
    robot = _robot()
    from rest_framework.exceptions import ValidationError

    for bad in ({}, {"action": "bad action!"}, {"action": "x" * 65}, {"action": "wave", "extra": 1}):
        with pytest.raises(ValidationError):
            recording_publisher.publish_command(robot, bad)
    assert recording_publisher.connection.calls == []


def test_unknown_notify_kind_rejected(recording_publisher):
    robot = _robot()
    with pytest.raises(ValueError):
        recording_publisher.publish_notify(robot, "firmware", 1)


@pytest.fixture
def publisher(broker):
    from mqtt_bridge.publisher import get_publisher

    reset_publisher()
    pub = get_publisher()
    yield pub
    reset_publisher()


@pytest.mark.django_db
def test_live_command_reaches_only_target_device(publisher, make_device):
    robot_a = _robot()
    robot_b = _robot()
    device_a = make_device(device_id=robot_a.device_id)
    device_b = make_device(device_id=robot_b.device_id)

    publisher.publish_command(robot_a, {"action": "wave"})

    topic_a = topics.command_topic(robot_a.device_id)
    received = device_a.wait_received(topic_a, timeout=5)
    assert received, "target device did not receive the command"
    assert json.loads(received[0]["payload"]) == {"action": "wave"}
    assert received[0]["qos"] == 1
    assert received[0]["retain"] is False
    assert not wait_until(
        lambda: bool(device_b.received(topics.command_topic(robot_b.device_id))),
        timeout=1.0,
    ), "another device received a command not addressed to it"


@pytest.mark.django_db
def test_live_command_is_not_retained_for_late_subscribers(publisher, make_device):
    robot = _robot()
    publisher.publish_command(robot, {"action": "wave"})
    device = make_device(device_id=robot.device_id)  # subscribes AFTER publish
    assert not wait_until(
        lambda: bool(device.received(topics.command_topic(robot.device_id))),
        timeout=1.5,
    ), "command was retained (must be momentary)"


@pytest.mark.django_db
def test_live_notify_markers_are_delivered_to_late_subscriber(publisher, make_device):
    robot = _robot()
    publish_config_notify(robot, 7)
    publish_ota_notify(robot, 3)
    publish_content_notify(robot, 5)
    device = make_device(device_id=robot.device_id)  # subscribes AFTER publish
    for kind, version in (("config", 7), ("ota", 3), ("content", 5)):
        topic = topics.notify_topic(robot.device_id, kind)
        received = device.wait_received(topic, timeout=5)
        assert received, f"retained {kind} marker not delivered to late subscriber"
        assert json.loads(received[0]["payload"]) == {"version": version}


@pytest.mark.django_db
def test_commands_for_multiple_robots_stay_separate(publisher, make_device):
    robots = [_robot() for _ in range(3)]
    devices = [
        make_device(device_id=robot.device_id) for robot in robots
    ]
    for index, robot in enumerate(robots):
        publisher.publish_command(robot, {"action": f"step{index}"})
    for index, robot in enumerate(robots):
        topic = topics.command_topic(robot.device_id)
        received = devices[index].wait_received(topic, timeout=5)
        assert received, f"robot {index} missed its command"
        assert json.loads(received[0]["payload"]) == {"action": f"step{index}"}
