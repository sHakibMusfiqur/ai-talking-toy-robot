"""Live-broker integration test: the full Web -> Django -> MQTT ->
simulator -> events loop plus the failure paths, using the same live
Mosquitto broker and production bridge subscriber as the mqtt_bridge
suite (Mongo + Mosquitto must be up - see AGENTS.md local commands).

Phasing matters: registration/pairing happen while the bridge is silent
for this device (no MQTT traffic yet), so the test thread never opens a
Mongo transaction over the Robot document while the bridge is writing
the same document (TransientTransactionError). After the simulator
starts, the test only reads.
"""

import logging
import uuid

import pytest
from devices.models import Robot, RobotEvent, RobotState
from devices.tests.helpers import PAIR_URL, setup_parent
from django.conf import settings
from mqtt_bridge import topics
from mqtt_bridge.provisioning import _single
from mqtt_bridge.tests.helpers import MqttTestClient, wait_until
from rest_framework.test import APIClient

from robot_sim import protocol
from robot_sim.backend_api import (
    BackendApi,
    BackendError,
    DjangoTestTransport,
)
from robot_sim.identity import load_or_create_identity
from robot_sim.simulator import (
    RobotSimulator,
    SimulatorConfig,
    backoff_delay,
)

pytestmark = pytest.mark.django_db(transaction=True)

COMMAND_URL = "/api/v1/devices/{pk}/command/"
STATE_URL = "/api/v1/devices/{pk}/state/"
EVENTS_URL = "/api/v1/devices/{pk}/events/"


def _deprovision(username):
    try:
        _single({"command": "deleteClient", "username": username})
    except Exception:  # noqa: BLE001 - cleanup must never fail a test
        pass


class RecordingTransport(DjangoTestTransport):
    """Django test transport that remembers response bodies so the test can
    prove the real pairing code/JWT never reach the logs."""

    def __init__(self, client):
        super().__init__(client)
        self.bodies = []

    def post(self, path, data):
        status, body = super().post(path, data)
        self.bodies.append((path, body))
        return status, body

    def pairing_code(self):
        return next(
            body["pairing_code"]
            for path, body in self.bodies
            if path.endswith("/register/") and "pairing_code" in body
        )

    def access_token(self):
        return next(
            body["access_token"]
            for path, body in self.bodies
            if path.endswith("/auth/token/")
        )


class DeadTransport:
    def post(self, path, data):
        raise BackendError("backend unreachable (ConnectError)")


def test_simulator_full_loop_web_mqtt_device(bridge, api_client, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.DEBUG, logger="robot_sim")
    # paho debug-logs raw packet bytes (CONNECT includes credentials);
    # third-party packet tracing is out of scope for the leak check.
    logging.getLogger("paho").setLevel(logging.WARNING)

    owner = APIClient()
    setup_parent(owner, f"sim-{uuid.uuid4().hex[:8]}@example.com")

    identity = load_or_create_identity(tmp_path)
    transport = RecordingTransport(api_client)
    api = BackendApi(transport, identity)

    # --- Phase A: register + pair while the bridge is silent for this
    # device (no MQTT traffic exists yet -> no concurrent transactions).
    pairing_code = api.register()
    assert isinstance(pairing_code, str) and pairing_code
    robot = Robot.objects.get(device_id=identity.device_id)
    assert robot.pairing_status == "unpaired"
    assert robot.firmware_version == "sim-0.1.0"

    paired = owner.post(
        PAIR_URL, {"pairing_code": pairing_code}, format="json"
    )
    assert paired.status_code == 200, paired.content
    robot_id = paired.json()["id"]

    # --- Phase B: start the simulator: 409 re-register (silent), then
    # challenge/token auth, then MQTT connect + telemetry.
    pairing_codes = []
    config = SimulatorConfig(
        mqtt_host=settings.MQTT_HOST,
        mqtt_port=settings.MQTT_PORT,
        heartbeat_seconds=0.1,
        status_seconds=0.15,
        exec_delay_seconds=0.0,
    )
    simulator = RobotSimulator(
        identity, api, config, on_pairing_code=pairing_codes.append
    )
    try:
        simulator.start()

        # An already-registered identity must not print a second code.
        assert pairing_codes == []
        assert transport.pairing_code() == pairing_code

        # Broker saw exactly the firmware's four subscriptions, all granted.
        expected_subs = [
            topic
            for topic, _qos in protocol.own_subscriptions(identity.device_id)
        ]
        assert simulator.subscribed_topics == expected_subs
        assert simulator.subscription_ack_codes == [1, 1, 1, 1]

        # Initial status (BOOT, online) ingested by the production bridge.
        assert wait_until(
            lambda: RobotState.objects.filter(
                robot=robot, online=True, state="BOOT"
            ).exists(),
            timeout=8,
        ), "initial status was not ingested"

        # Heartbeat refreshes last_seen (proof of life, bare {} payload).
        first_seen = Robot.objects.get(pk=robot.pk).last_seen
        assert first_seen is not None
        assert wait_until(
            lambda: Robot.objects.get(pk=robot.pk).last_seen > first_seen,
            timeout=8,
        ), "heartbeat did not refresh last_seen"

        # Web -> MQTT -> simulator: owner command (case-insensitive input).
        response = owner.post(
            COMMAND_URL.format(pk=robot_id), {"action": "wave"}, format="json"
        )
        assert response.status_code == 200, response.content

        def events():
            return list(
                RobotEvent.objects.filter(robot=robot).values_list(
                    "event_type", "payload"
                )
            )

        assert wait_until(
            lambda: {"command.received", "command.executed"}
            <= {event_type for event_type, _ in events()},
            timeout=8,
        ), "command ack events did not reach the database"

        received = [
            payload for event_type, payload in events()
            if event_type == "command.received"
        ][-1]
        executed = [
            payload for event_type, payload in events()
            if event_type == "command.executed"
        ][-1]
        assert received == {"action": "WAVE", "simulated": True}
        assert executed == {"action": "WAVE", "simulated": True}

        # State machine mirrors firmware: BOOT -> WAVE -> IDLE.
        assert wait_until(
            lambda: RobotState.objects.get(robot=robot).state == "IDLE",
            timeout=8,
        ), "state did not settle to IDLE after execution"

        # Startup lifecycle event visible to the owner on the events API.
        assert wait_until(
            lambda: "simulator.started"
            in {
                event["event_type"]
                for event in owner.get(EVENTS_URL.format(pk=robot_id)).json()
            },
            timeout=8,
        ), "simulator.started event not visible on the events API"

        # Owner state read: never fabricated telemetry.
        state_body = owner.get(STATE_URL.format(pk=robot_id)).json()
        assert state_body["online"] is True
        assert state_body["rssi"] is None
        assert state_body["battery_percent"] is None

        # Malformed/hostile commands published straight onto the command
        # topic (django-service role can do this; a rogue device cannot)
        # must come back as rejects, never as executions.
        publisher = MqttTestClient(
            username=settings.MQTT_BRIDGE_USERNAME,
            password=settings.MQTT_BRIDGE_PASSWORD,
        )
        publisher.connect()
        try:
            command_topic = topics.command_topic(identity.device_id)
            publisher.publish(command_topic, {"action": "servo"}, qos=1)
            publisher.publish(command_topic, "not-json", qos=1)
        finally:
            publisher.disconnect()

        assert wait_until(
            lambda: sum(
                1 for event_type, _ in events()
                if event_type == "command.rejected"
            )
            >= 2,
            timeout=8,
        ), "rejections did not reach the database"
        rejects = [
            payload for event_type, payload in events()
            if event_type == "command.rejected"
        ]
        assert {
            "action": "servo",
            "reason": "unknown_action",
            "simulated": True,
        } in rejects
        assert {
            "action": "",
            "reason": "invalid_payload",
            "simulated": True,
        } in rejects
        executed_count = sum(
            1 for event_type, _ in events()
            if event_type == "command.executed"
        )
        assert executed_count == 1, "rejected commands must never execute"
    finally:
        simulator.stop()
        _deprovision(identity.device_id)

    # Graceful stop publishes the retained offline marker -> dashboard
    # shows the robot offline (same payload as the LWT).
    assert wait_until(
        lambda: not RobotState.objects.get(robot=robot).online,
        timeout=8,
    ), "graceful stop did not publish offline status"

    # Secrets: pairing code + JWT + private key never reached any log.
    token = transport.access_token()
    assert pairing_code not in caplog.text
    assert token not in caplog.text
    assert "PRIVATE KEY" not in caplog.text


def test_start_fails_cleanly_when_backend_unreachable(tmp_path):
    identity = load_or_create_identity(tmp_path)
    api = BackendApi(DeadTransport(), identity)
    simulator = RobotSimulator(identity, api, SimulatorConfig())
    with pytest.raises(BackendError, match="unreachable"):
        simulator.start()
    simulator.stop()  # idempotent teardown must not raise


def test_backoff_is_bounded_and_deterministic():
    assert backoff_delay(0) == 1.0
    assert backoff_delay(1) == 2.0
    assert backoff_delay(4) == 16.0
    assert backoff_delay(5) == 30.0
    assert backoff_delay(99) == 30.0
