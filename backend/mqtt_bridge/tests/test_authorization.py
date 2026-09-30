import json
import uuid

import pytest
from devices.models import Robot
from devices.tests.helpers import setup_admin, setup_parent
from devices.tests.test_device_auth import (
    complete_auth,
    make_ec_keypair,
    register,
)
from mqtt_bridge.client import MqttError
from mqtt_bridge import topics

from .helpers import wait_until


def command_path(robot):
    return f"/api/v1/devices/{robot.pk}/command/"


def _robot(parent=None):
    return Robot.objects.create(device_id=str(uuid.uuid4()), parent=parent)


@pytest.fixture
def parent_a(api_client):
    return setup_parent(api_client, "parent-a-phase8@example.com")


@pytest.fixture
def parent_b(api_client):
    return setup_parent(api_client, "parent-b-phase8@example.com")


@pytest.fixture
def admin_client(api_client):
    setup_admin(api_client, "admin-phase8@example.com")
    return api_client


@pytest.mark.django_db
def test_owner_command_published_and_delivered(api_client, parent_a, broker, make_device):
    robot = _robot(parent=parent_a)
    device = make_device(device_id=robot.device_id)
    response = api_client.post(
        command_path(robot), {"action": "wave"}, format="json"
    )
    assert response.status_code == 200, response.content
    assert response.json() == {"detail": "Command published."}

    topic = topics.command_topic(robot.device_id)
    received = device.wait_received(topic, timeout=5)
    assert received, "command was not delivered over MQTT"
    assert json.loads(received[0]["payload"]) == {"action": "wave"}


@pytest.mark.django_db
def test_non_owner_parent_gets_404(api_client, parent_a, parent_b):
    # Fixture order logs in parent_a then parent_b: the active session is B.
    robot = _robot(parent=parent_a)
    response = api_client.post(
        command_path(robot), {"action": "wave"}, format="json"
    )
    assert response.status_code == 404
    assert set(response.json().keys()) == {"detail"}


@pytest.mark.django_db
def test_admin_may_command_any_robot(admin_client, broker, make_device):
    robot = _robot()  # unowned robot
    device = make_device(device_id=robot.device_id)
    response = admin_client.post(
        command_path(robot), {"action": "dance"}, format="json"
    )
    assert response.status_code == 200, response.content
    received = device.wait_received(topics.command_topic(robot.device_id), timeout=5)
    assert received, "admin command not delivered"


@pytest.mark.django_db
def test_unauthenticated_command_rejected(api_client):
    robot = _robot()
    response = api_client.post(command_path(robot), {"action": "wave"}, format="json")
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication credentials were not provided."}


@pytest.mark.django_db
def test_device_bearer_token_is_not_accepted(api_client, broker):
    """Commands originate from humans on the dashboard (session + CSRF),
    never from device tokens: a stolen device token cannot drive another
    robot's command channel."""
    from .conftest import _deprovision

    private_key, pem = make_ec_keypair()
    device_id = str(uuid.uuid4())
    try:
        register(api_client, device_id, pem)
        token = complete_auth(api_client, device_id, private_key)["access_token"]
        robot = _robot()
        response = api_client.post(
            command_path(robot),
            {"action": "wave"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert response.status_code == 401
        assert response.json() == {
            "detail": "Authentication credentials were not provided."
        }
    finally:
        _deprovision(device_id)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"action": "bad action!"},
        {"action": "x" * 65},
        {"action": "wave", "extra": 1},
        {"action": ""},
        {"action": 123},
    ],
)
def test_malformed_command_bodies_rejected(api_client, parent_a, body):
    robot = _robot(parent=parent_a)
    response = api_client.post(command_path(robot), body, format="json")
    assert response.status_code == 400
    errors = response.json()
    assert "action" in errors or "non_field_errors" in errors


@pytest.mark.django_db
@pytest.mark.parametrize("bad_pk", ["not-an-objectid", "0" * 24])
def test_malformed_robot_id_gets_404(api_client, parent_a, bad_pk):
    response = api_client.post(
        f"/api/v1/devices/{bad_pk}/command/", {"action": "wave"}, format="json"
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_unknown_robot_gets_404(api_client, parent_a):
    robot = _robot(parent=parent_a)
    other = str(robot.pk)[::-1]  # valid ObjectId shape, no such robot
    response = api_client.post(
        f"/api/v1/devices/{other}/command/", {"action": "wave"}, format="json"
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_get_on_command_endpoint_is_405(api_client, parent_a):
    robot = _robot(parent=parent_a)
    response = api_client.get(command_path(robot))
    assert response.status_code == 405


@pytest.mark.django_db
def test_mqtt_failure_returns_503(api_client, parent_a, monkeypatch):
    from devices import views

    def _explode(robot, payload):
        raise MqttError("broker unreachable")

    monkeypatch.setattr(views, "publish_command", _explode)
    robot = _robot(parent=parent_a)
    response = api_client.post(command_path(robot), {"action": "wave"}, format="json")
    assert response.status_code == 503
    assert response.json() == {"detail": "Robot command service unavailable."}
