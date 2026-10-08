import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from devices.models import RobotState
from devices.tests.helpers import PAIR_URL, setup_parent
from devices.tests.test_device_auth import (
    complete_auth,
    make_ec_keypair,
    register,
)
from mqtt_bridge import topics
from mqtt_bridge.provisioning import _single
from rest_framework.test import APIClient

from .helpers import MqttTestClient, wait_until

pytestmark = pytest.mark.django_db(transaction=True)

COMMAND_URL = "/api/v1/devices/{pk}/command/"
STATE_URL = "/api/v1/devices/{pk}/state/"
EVENTS_URL = "/api/v1/devices/{pk}/events/"


def _deprovision(username):
    try:
        _single({"command": "deleteClient", "username": username})
    except Exception:  # noqa: BLE001 - cleanup must never fail a test
        pass


@contextmanager
def enrolled_device(owner_client):
    """Register + pair + authenticate + connect one device, and always
    tear the broker client down afterwards."""
    email = f"acceptance-{uuid.uuid4().hex[:8]}@example.com"
    setup_parent(owner_client, email)
    private_key, pem = make_ec_keypair()
    device_id = str(uuid.uuid4())
    registration = register(owner_client, device_id, pem)
    paired = owner_client.post(
        PAIR_URL, {"pairing_code": registration["pairing_code"]}, format="json"
    )
    assert paired.status_code == 200, paired.content
    token = complete_auth(owner_client, device_id, private_key)["access_token"]
    device = MqttTestClient(username=device_id, password=token)
    device.connect()
    try:
        yield SimpleNamespace(
            robot_id=paired.json()["id"], device=device, device_id=device_id
        )
    finally:
        device.disconnect()
        _deprovision(device_id)


def test_full_acceptance_path_device_to_web_to_device(bridge, api_client):
    with enrolled_device(api_client) as robot:
        device = robot.device
        device_id = robot.device_id

        # Exactly the firmware's subscription set: own command + notifies.
        codes = device.subscribe(f"robots/{device_id}/command", qos=1)
        assert codes == [1]
        for kind in topics.NOTIFY_KINDS:
            codes = device.subscribe(f"robots/{device_id}/{kind}/notify", qos=1)
            assert codes == [1]

        # --- ESP32 -> MQTT: frozen heartbeat, QoS 0, no fields ---
        device.publish(topics.heartbeat_topic(device_id), "{}", qos=0)
        assert wait_until(
            lambda: RobotState.objects.filter(
                robot__device_id=device_id, online=True
            ).exists(),
            timeout=8,
        ), "heartbeat was not ingested"

        state_resp = api_client.get(STATE_URL.format(pk=robot.robot_id))
        assert state_resp.status_code == 200
        state = state_resp.json()
        assert state["online"] is True
        assert state["last_seen"] is not None

        # --- ESP32 -> MQTT: status (partial update keys only) ---
        device.publish(
            topics.status_topic(device_id),
            {"online": True, "state": "IDLE", "rssi": -61},
            qos=1,
        )
        assert wait_until(
            lambda: RobotState.objects.filter(
                robot__device_id=device_id, state="IDLE"
            ).exists(),
            timeout=8,
        ), "status was not ingested"
        state = api_client.get(STATE_URL.format(pk=robot.robot_id)).json()
        assert state["state"] == "IDLE"
        assert state["rssi"] == -61
        assert state["battery_percent"] is None, "must stay null, never fabricated"

        # --- ESP32 -> MQTT: lifecycle event ---
        device.publish(
            topics.event_topic(device_id),
            {"event_type": "firmware.started", "payload": {"firmware": "test"}},
            qos=1,
        )
        assert wait_until(
            lambda: any(
                event["event_type"] == "firmware.started"
                for event in api_client.get(
                    EVENTS_URL.format(pk=robot.robot_id)
                ).json()
            ),
            timeout=8,
        ), "event was not visible on the events API"

        # --- Web -> MQTT -> ESP32: owner command, exact envelope ---
        command = api_client.post(
            COMMAND_URL.format(pk=robot.robot_id), {"action": "WAVE"}, format="json"
        )
        assert command.status_code == 200, command.content
        received = device.wait_received(topics.command_topic(device_id), timeout=6)
        assert received, "command never reached the device"
        assert json.loads(received[0]["payload"]) == {"action": "WAVE"}
        assert received[0]["qos"] == 1, "commands must arrive at QoS 1"
        assert received[0]["retain"] is False

        # --- ESP32 -> MQTT -> Web: command ack events ---
        device.publish(
            topics.event_topic(device_id),
            {"event_type": "command.received", "payload": {"action": "WAVE"}},
            qos=1,
        )
        device.publish(
            topics.event_topic(device_id),
            {"event_type": "command.executed", "payload": {"action": "WAVE"}},
            qos=1,
        )
        assert wait_until(
            lambda: {
                event["event_type"]
                for event in api_client.get(
                    EVENTS_URL.format(pk=robot.robot_id)
                ).json()
            }
            >= {"command.received", "command.executed"},
            timeout=8,
        ), "command ack events did not reach the events API"


def test_non_semantic_and_malformed_commands_never_reach_device(
    bridge, api_client
):
    with enrolled_device(api_client) as robot:
        device = robot.device
        device_id = robot.device_id
        command_topic = topics.command_topic(device_id)
        device.subscribe(command_topic, qos=1)

        for payload in (
            {"action": "move_servo"},                       # raw hardware control
            {"action": "WAVE", "angle": 45},                # unknown extra field
            {"action": ""},                                 # empty action
            {"action": "WAVE;LEDs.on"},                     # out-of-contract syntax
        ):
            response = api_client.post(
                COMMAND_URL.format(pk=robot.robot_id), payload, format="json"
            )
            assert response.status_code == 400, (payload, response.content)

        assert not wait_until(
            lambda: bool(device.received(command_topic)), timeout=1.5
        ), "a rejected command reached the device"


def test_non_owner_command_is_404_and_not_delivered(bridge, api_client):
    with enrolled_device(api_client) as robot:
        device = robot.device
        command_topic = topics.command_topic(robot.device_id)
        device.subscribe(command_topic, qos=1)

        stranger = APIClient()
        setup_parent(stranger, f"stranger-{uuid.uuid4().hex[:8]}@example.com")
        response = stranger.post(
            COMMAND_URL.format(pk=robot.robot_id), {"action": "NOD"}, format="json"
        )
        assert response.status_code == 404, response.content

        assert not wait_until(
            lambda: bool(device.received(command_topic)), timeout=1.5
        ), "a non-owner command reached the device"


def test_anonymous_command_is_401_not_delivery(bridge, api_client):
    with enrolled_device(api_client) as robot:
        device = robot.device
        command_topic = topics.command_topic(robot.device_id)
        device.subscribe(command_topic, qos=1)

        anonymous = APIClient()
        response = anonymous.post(
            COMMAND_URL.format(pk=robot.robot_id), {"action": "IDLE"}, format="json"
        )
        assert response.status_code == 401, response.content
        assert not wait_until(
            lambda: bool(device.received(command_topic)), timeout=1.5
        ), "an anonymous command reached the device"


def test_device_session_uses_issued_jwt_as_broker_password(bridge, api_client):
    """Section 26: the password on the wire is exactly the JWT the
    exchange endpoint returned — no second credential system."""
    email = f"jwt-{uuid.uuid4().hex[:8]}@example.com"
    setup_parent(api_client, email)
    private_key, pem = make_ec_keypair()
    device_id = str(uuid.uuid4())
    register(api_client, device_id, pem)
    token = complete_auth(api_client, device_id, private_key)["access_token"]
    try:
        client = MqttTestClient(username=device_id, password=token)
        client.connect(timeout=5)
        client.disconnect()
    finally:
        _deprovision(device_id)
