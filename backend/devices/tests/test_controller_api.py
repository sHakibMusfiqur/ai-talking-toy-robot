import uuid

import pytest
from analytics.models import AuditLog
from configuration.models import Configuration
from devices.models import Robot, RobotEvent, SensorData
from devices.services import update_status
from rest_framework.test import APIClient

from .helpers import setup_admin, setup_parent
from .test_device_auth_access import bearer, issue_token

pytestmark = pytest.mark.django_db


def _robot(parent, firmware_version="1.2.3"):
    return Robot.objects.create(
        device_id=str(uuid.uuid4()),
        parent=parent,
        firmware_version=firmware_version,
    )


def command_url(robot):
    return f"/api/v1/devices/{robot.pk}/command/"


def state_url(robot):
    return f"/api/v1/devices/{robot.pk}/state/"


def events_url(robot):
    return f"/api/v1/devices/{robot.pk}/events/"


def sensors_url(robot):
    return f"/api/v1/devices/{robot.pk}/sensors/"


def config_url(robot):
    return f"/api/v1/devices/{robot.pk}/configuration/"


def summary_url(robot):
    return f"/api/v1/devices/{robot.pk}/summary/"


@pytest.fixture
def parent(api_client):
    return setup_parent(api_client, "controller-parent@example.com")


@pytest.fixture
def admin(api_client):
    return setup_admin(api_client, "controller-admin@example.com")


@pytest.fixture
def stranger():
    client = APIClient()
    setup_parent(client, "controller-stranger@example.com")
    return client


@pytest.fixture
def recorder(monkeypatch):
    """Capture command publishes at the view's publish_command binding so
    these tests stay broker-independent (same seam as the 503 test)."""
    from devices import views

    calls = []

    def _record(robot, payload):
        calls.append((robot, payload))

    monkeypatch.setattr(views, "publish_command", _record)
    return calls


# --- Command: semantic action contract (docs/01 Section 19) ---------------

CANONICAL_ACTIONS = [
    "IDLE",
    "WAVE",
    "DANCE",
    "NOD",
    "TILT_HEAD",
    "POINT",
    "LISTENING_RETRY",
    "wave",
    "Wave",
    "dAnCe",
]


@pytest.mark.parametrize("action", CANONICAL_ACTIONS)
def test_semantic_actions_accepted_and_passed_through_verbatim(
    api_client, parent, recorder, action
):
    robot = _robot(parent)
    response = api_client.post(
        command_url(robot), {"action": action}, format="json"
    )
    assert response.status_code == 200, response.content
    assert response.json() == {"detail": "Command published."}
    assert len(recorder) == 1
    recorded_robot, payload = recorder[0]
    assert recorded_robot.pk == robot.pk
    assert payload == {"action": action}


NON_SEMANTIC_ACTIONS = [
    "fly",
    "servo",
    "pwm",
    "gpio",
    "set_servo_angle",
    "SHAKE_HEAD",
    "LOOK_LEFT",
]


@pytest.mark.parametrize("action", NON_SEMANTIC_ACTIONS)
def test_non_semantic_action_rejected_400(api_client, parent, recorder, action):
    robot = _robot(parent)
    response = api_client.post(
        command_url(robot), {"action": action}, format="json"
    )
    assert response.status_code == 400
    assert "action" in response.json()
    assert recorder == []


@pytest.mark.parametrize(
    "body",
    [
        {"action": "wave", "servo_angle": 90},
        {"action": "wave", "pwm": 512},
        {"action": "wave", "gpio": 17},
        {"action": "wave", "motor_voltage": 3.3},
    ],
)
def test_raw_hardware_control_body_rejected_400(
    api_client, parent, recorder, body
):
    robot = _robot(parent)
    response = api_client.post(command_url(robot), body, format="json")
    assert response.status_code == 400
    assert recorder == []


def test_invalid_action_on_foreign_robot_is_404_not_400(
    api_client, parent, stranger, recorder
):
    # Ownership resolves before body validation: a foreign caller can never
    # probe whether a given action would have been valid.
    robot = _robot(parent)
    response = stranger.post(command_url(robot), {"action": "fly"}, format="json")
    assert response.status_code == 404
    assert recorder == []


def test_admin_command_is_audit_logged(api_client, admin, recorder):
    robot = _robot(parent=None)  # admins may command any robot
    response = api_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert response.status_code == 200, response.content
    entry = AuditLog.objects.get(action="robot.commanded")
    assert entry.actor_id == admin.pk
    assert entry.target == str(robot.pk)


def test_parent_command_is_not_audit_logged(api_client, parent, recorder):
    robot = _robot(parent)
    response = api_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert response.status_code == 200, response.content
    assert not AuditLog.objects.filter(action="robot.commanded").exists()


# --- Command: duplicate-command suppression (Phase A, F-10) ----------------


@pytest.fixture(autouse=True)
def clear_command_cooldowns():
    from devices.views import _command_cooldowns

    _command_cooldowns.clear()
    yield
    _command_cooldowns.clear()


def test_repeat_command_within_cooldown_is_429(api_client, parent, recorder):
    robot = _robot(parent)
    first = api_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert first.status_code == 200, first.content
    second = api_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert second.status_code == 429
    assert second.json() == {"detail": "Command repeated too quickly."}
    # The throttled attempt never reached the publisher.
    assert len(recorder) == 1


def test_cooldown_is_keyed_per_robot(api_client, parent, recorder):
    robot_a = _robot(parent)
    robot_b = _robot(parent)
    first = api_client.post(command_url(robot_a), {"action": "wave"}, format="json")
    assert first.status_code == 200, first.content
    # Same user, different robot: not a duplicate.
    second = api_client.post(command_url(robot_b), {"action": "wave"}, format="json")
    assert second.status_code == 200, second.content
    assert len(recorder) == 2


def test_cooldown_is_keyed_per_user(api_client, parent, recorder):
    robot = _robot(parent)
    first = api_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert first.status_code == 200, first.content
    # Different user (admin may command any robot): not a duplicate.
    admin_client = APIClient()
    setup_admin(admin_client, "cooldown-admin@example.com")
    second = admin_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert second.status_code == 200, second.content
    assert len(recorder) == 2


def test_cooldown_expires_after_window(api_client, parent, recorder, settings):
    import time as _time

    settings.ROBOT_COMMAND_COOLDOWN_SECONDS = 0.15
    robot = _robot(parent)
    first = api_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert first.status_code == 200, first.content
    _time.sleep(0.2)
    second = api_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert second.status_code == 200, second.content
    assert len(recorder) == 2


def test_failed_publish_does_not_start_cooldown(api_client, parent, monkeypatch):
    # A 503 must not lock the user out of retrying: the cooldown is
    # recorded only after a successful publish.
    from mqtt_bridge.client import MqttError

    from devices import views

    def _explode(robot, payload):
        raise MqttError("broker unreachable")

    monkeypatch.setattr(views, "publish_command", _explode)
    robot = _robot(parent)
    first = api_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert first.status_code == 503

    calls = []

    def _record(robot, payload):
        calls.append((robot, payload))

    monkeypatch.setattr(views, "publish_command", _record)
    second = api_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert second.status_code == 200, second.content
    assert len(calls) == 1


def test_cooldown_disabled_when_zero(api_client, parent, recorder, settings):
    settings.ROBOT_COMMAND_COOLDOWN_SECONDS = 0
    robot = _robot(parent)
    first = api_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert first.status_code == 200, first.content
    second = api_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert second.status_code == 200, second.content
    assert len(recorder) == 2


def test_rejected_body_does_not_start_cooldown(api_client, parent, recorder):
    robot = _robot(parent)
    bad = api_client.post(command_url(robot), {"action": "servo"}, format="json")
    assert bad.status_code == 400
    # A valid command right after a rejected body is not throttled.
    good = api_client.post(command_url(robot), {"action": "wave"}, format="json")
    assert good.status_code == 200, good.content
    assert len(recorder) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_commands_publish_exactly_once(parent, monkeypatch):
    """Simultaneous command requests for the same robot must produce exactly
    one publish and one 200; the rest are 429. The patched publish blocks on
    a barrier so that on non-atomic check-then-act code every thread would
    pass the empty cooldown check, enter publish together, trip the barrier,
    and all return 200. With atomic reserve-before-publish only one thread
    reaches publish; the barrier times out, breaks, and the single publisher
    tolerates the broken barrier."""
    import threading

    from django.db import connections

    from devices import views

    parties = 4
    robot = _robot(parent)
    publish_calls = []
    publish_barrier = threading.Barrier(parties)
    start_barrier = threading.Barrier(parties)

    def _blocking_publish(robot, payload):
        publish_calls.append(payload)
        try:
            publish_barrier.wait(timeout=1.0)
        except threading.BrokenBarrierError:
            pass

    monkeypatch.setattr(views, "publish_command", _blocking_publish)

    responses = [None] * parties

    def _worker(index):
        try:
            client = APIClient()
            client.force_authenticate(user=parent.user)
            start_barrier.wait(timeout=10.0)
            responses[index] = client.post(
                command_url(robot), {"action": "wave"}, format="json"
            )
        finally:
            connections.close_all()

    threads = [
        threading.Thread(target=_worker, args=(i,)) for i in range(parties)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30.0)
        assert not thread.is_alive()

    assert all(response is not None for response in responses)
    codes = sorted(response.status_code for response in responses)
    assert codes == [200, 429, 429, 429]
    for response in responses:
        if response.status_code == 429:
            assert response.json() == {"detail": "Command repeated too quickly."}
    # The throttled requests never reached the publisher.
    assert len(publish_calls) == 1


@pytest.mark.django_db(transaction=True)
def test_slow_failing_publish_cannot_release_newer_reservation(
    parent, monkeypatch
):
    """Reservation A outlives its cooldown window while its publish is still
    in flight; B then reserves the expired slot. When A finally fails, A's
    release must remove only A's own reservation token - B's newer
    reservation stays, so a third request is still throttled. An
    unconditional pop would delete B's entry and let the third command
    through. Deterministic: A's publish blocks on events controlled by the
    test, not on timing."""
    import threading
    import time as _time

    from django.db import connections
    from django.test import override_settings

    from devices import views
    from mqtt_bridge.client import MqttError

    robot = _robot(parent)
    publish_calls = []
    a_entered = threading.Event()
    a_resume = threading.Event()

    def _gated_publish(robot, payload):
        publish_calls.append(payload)
        if len(publish_calls) == 1:  # request A: slow, then fails
            a_entered.set()
            assert a_resume.wait(timeout=10.0)
            raise MqttError("publish failed after its window expired")
        # request B succeeds immediately.

    monkeypatch.setattr(views, "publish_command", _gated_publish)

    def _post_as_parent():
        client = APIClient()
        client.force_authenticate(user=parent.user)
        return client.post(command_url(robot), {"action": "wave"}, format="json")

    a_response = [None]

    def _worker_a():
        try:
            client = APIClient()
            client.force_authenticate(user=parent.user)
            a_response[0] = client.post(
                command_url(robot), {"action": "wave"}, format="json"
            )
        finally:
            connections.close_all()

    with override_settings(ROBOT_COMMAND_COOLDOWN_SECONDS=0.15):
        thread = threading.Thread(target=_worker_a)
        thread.start()
        assert a_entered.wait(timeout=10.0)
        # A's reservation is now past its 0.15s window while A still holds
        # the publish slot open.
        _time.sleep(0.25)

        # B reserves the expired slot and publishes successfully.
        b = _post_as_parent()
        assert b.status_code == 200, b.content

        # Let A fail; its release must not touch B's newer reservation.
        a_resume.set()
        thread.join(timeout=10.0)
        assert not thread.is_alive()
        assert a_response[0] is not None
        assert a_response[0].status_code == 503

        # B's reservation is intact: a third request is still throttled and
        # never reaches the publisher.
        c = _post_as_parent()
        assert c.status_code == 429
        assert c.json() == {"detail": "Command repeated too quickly."}

    # A and B published; C never did.
    assert len(publish_calls) == 2


# --- Reads: state ----------------------------------------------------------


def test_owner_reads_state_shape_and_values(api_client, parent):
    robot = _robot(parent)
    update_status(
        robot=robot, online=True, state="sleeping", battery_percent=76, rssi=-63
    )
    response = api_client.get(state_url(robot))
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {
        "online",
        "state",
        "battery_percent",
        "rssi",
        "last_updated",
        "last_seen",
        "firmware_version",
    }
    assert data["online"] is True
    assert data["state"] == "sleeping"
    assert data["battery_percent"] == 76
    assert data["rssi"] == -63
    assert data["last_updated"] is not None
    assert data["last_seen"] is not None
    assert data["firmware_version"] == "1.2.3"


def test_state_nulls_when_robot_never_reported(api_client, parent):
    robot = _robot(parent)
    response = api_client.get(state_url(robot))
    assert response.status_code == 200
    data = response.json()
    assert data["online"] is None
    assert data["state"] is None
    assert data["battery_percent"] is None
    assert data["rssi"] is None
    assert data["last_updated"] is None
    assert data["last_seen"] is None
    assert data["firmware_version"] == "1.2.3"


def test_state_ownership_and_auth_boundaries(api_client, parent, stranger):
    robot = _robot(parent)
    assert api_client.get(state_url(robot)).status_code == 200
    assert stranger.get(state_url(robot)).status_code == 404
    assert (
        stranger.get("/api/v1/devices/000000000000000000000000/state/").status_code
        == 404
    )
    assert (
        stranger.get("/api/v1/devices/not-an-objectid/state/").status_code == 404
    )

    anon = APIClient()
    response = anon.get(state_url(robot))
    assert response.status_code == 401
    assert response.json() == {
        "detail": "Authentication credentials were not provided."
    }


def test_admin_read_of_unowned_robot_is_404(api_client, parent):
    """Per-robot reads follow the existing owner-scoped detail policy
    (docs/01 Phase 6 / endpoints.md); admins get fleet identity through
    the list endpoint, telemetry stays owner-scoped."""
    robot = _robot(parent)
    admin = APIClient()
    setup_admin(admin, "controller-admin-read@example.com")
    assert admin.get(state_url(robot)).status_code == 404


# --- Reads: events ---------------------------------------------------------


def _make_events(robot, count):
    for index in range(count):
        RobotEvent.objects.create(robot=robot, event_type=f"e{index}", payload={})


def test_events_newest_first_and_bounded(api_client, parent):
    robot = _robot(parent)
    _make_events(robot, 30)
    response = api_client.get(events_url(robot), {"limit": 5})
    assert response.status_code == 200
    data = response.json()
    assert [item["event_type"] for item in data] == [
        "e29",
        "e28",
        "e27",
        "e26",
        "e25",
    ]
    assert set(data[0].keys()) == {"event_type", "payload", "timestamp"}


def test_events_default_limit(api_client, parent):
    robot = _robot(parent)
    _make_events(robot, 25)
    response = api_client.get(events_url(robot))
    assert response.status_code == 200
    assert len(response.json()) == 20


def test_events_limit_boundaries(api_client, parent):
    robot = _robot(parent)
    _make_events(robot, 3)
    one = api_client.get(events_url(robot), {"limit": 1})
    assert one.status_code == 200
    assert len(one.json()) == 1
    maxed = api_client.get(events_url(robot), {"limit": 100})
    assert maxed.status_code == 200
    assert len(maxed.json()) == 3


@pytest.mark.parametrize("limit", ["0", "-1", "101", "abc", "5.5"])
def test_events_invalid_limit_rejected(api_client, parent, limit):
    robot = _robot(parent)
    _make_events(robot, 3)
    response = api_client.get(events_url(robot), {"limit": limit})
    assert response.status_code == 400
    assert "limit" in response.json()


def test_events_empty_is_empty_list(api_client, parent):
    robot = _robot(parent)
    response = api_client.get(events_url(robot))
    assert response.status_code == 200
    assert response.json() == []


def test_events_ownership_and_auth(api_client, parent, stranger):
    robot = _robot(parent)
    _make_events(robot, 2)
    assert stranger.get(events_url(robot)).status_code == 404
    anon = APIClient()
    assert anon.get(events_url(robot)).status_code == 401


# --- Reads: sensors --------------------------------------------------------


def test_sensors_newest_first_and_shaped(api_client, parent):
    robot = _robot(parent)
    SensorData.objects.create(robot=robot, sensor_type="temperature", value=21.5)
    SensorData.objects.create(robot=robot, sensor_type="humidity", value=60.0)
    SensorData.objects.create(robot=robot, sensor_type="temperature", value=22.0)
    response = api_client.get(sensors_url(robot), {"limit": 2})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["sensor_type"] == "temperature"
    assert data[0]["value"] == 22.0
    assert set(data[0].keys()) == {"sensor_type", "value", "timestamp"}


def test_sensors_ownership_and_auth(api_client, parent, stranger):
    robot = _robot(parent)
    SensorData.objects.create(robot=robot, sensor_type="temperature", value=20.0)
    assert stranger.get(sensors_url(robot)).status_code == 404
    anon = APIClient()
    assert anon.get(sensors_url(robot)).status_code == 401


# --- Reads: configuration --------------------------------------------------


def test_configuration_returns_current_version(api_client, parent):
    robot = _robot(parent)
    Configuration.objects.create(
        robot=robot, version=1, data={"volume": 30}, is_current=False
    )
    Configuration.objects.create(
        robot=robot,
        version=2,
        data={"volume": 55, "language": "bn"},
        is_current=True,
    )
    response = api_client.get(config_url(robot))
    assert response.status_code == 200
    assert response.json() == {
        "version": 2,
        "data": {"volume": 55, "language": "bn"},
        "is_current": True,
    }


def test_configuration_redacts_secret_keys(api_client, parent):
    robot = _robot(parent)
    Configuration.objects.create(
        robot=robot,
        version=1,
        data={
            "volume": 50,
            "service_token": "raw-secret-value",
            "nested": {"api_key": "raw-key-value"},
        },
        is_current=True,
    )
    response = api_client.get(config_url(robot))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["volume"] == 50
    assert data["service_token"] == "[redacted]"
    assert data["nested"]["api_key"] == "[redacted]"
    body = response.content.decode()
    assert "raw-secret-value" not in body
    assert "raw-key-value" not in body


def test_configuration_absent_returns_nulls(api_client, parent):
    robot = _robot(parent)
    response = api_client.get(config_url(robot))
    assert response.status_code == 200
    assert response.json() == {
        "version": None,
        "data": None,
        "is_current": None,
    }


def test_configuration_ownership_and_auth(api_client, parent, stranger):
    robot = _robot(parent)
    Configuration.objects.create(
        robot=robot, version=1, data={"volume": 10}, is_current=True
    )
    assert stranger.get(config_url(robot)).status_code == 404
    anon = APIClient()
    assert anon.get(config_url(robot)).status_code == 401


# --- Reads: controller summary ---------------------------------------------


def test_summary_assembles_identity_state_events_configuration(api_client, parent):
    robot = _robot(parent)
    update_status(
        robot=robot, online=True, state="idle", battery_percent=90, rssi=-40
    )
    _make_events(robot, 12)
    Configuration.objects.create(
        robot=robot, version=1, data={"volume": 40}, is_current=True
    )

    response = api_client.get(summary_url(robot))
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"robot", "state", "events", "configuration"}
    assert set(data["robot"].keys()) == {
        "id",
        "device_id",
        "nickname",
        "pairing_status",
        "firmware_version",
        "last_seen",
        "paired_at",
    }
    assert data["state"]["online"] is True
    assert data["state"]["battery_percent"] == 90
    assert len(data["events"]) == 10
    assert data["events"][0]["event_type"] == "e11"
    assert data["configuration"]["version"] == 1
    assert data["configuration"]["data"] == {"volume": 40}


def test_summary_ownership_auth_and_405(api_client, parent, stranger):
    robot = _robot(parent)
    assert stranger.get(summary_url(robot)).status_code == 404
    anon = APIClient()
    assert anon.get(summary_url(robot)).status_code == 401
    assert api_client.post(summary_url(robot), {}, format="json").status_code == 405


# --- Cross-cutting method and authentication boundaries ---------------------

READ_SUFFIXES = ["state", "events", "sensors", "configuration", "summary"]


@pytest.mark.parametrize("suffix", READ_SUFFIXES)
def test_read_endpoints_reject_write_methods(api_client, parent, suffix):
    robot = _robot(parent)
    url = f"/api/v1/devices/{robot.pk}/{suffix}/"
    assert api_client.post(url, {}, format="json").status_code == 405
    assert api_client.put(url, {}, format="json").status_code == 405
    assert api_client.patch(url, {}, format="json").status_code == 405
    assert api_client.delete(url).status_code == 405


@pytest.mark.parametrize("suffix", READ_SUFFIXES)
def test_read_endpoints_require_session_auth(api_client, parent, suffix):
    robot = _robot(parent)
    anon = APIClient()
    response = anon.get(f"/api/v1/devices/{robot.pk}/{suffix}/")
    assert response.status_code == 401
    assert response.json() == {
        "detail": "Authentication credentials were not provided."
    }


def test_device_bearer_token_not_accepted_on_read_endpoints(api_client, parent):
    # Reads are human/session-only: a device token (even for a robot this
    # parent owns) can never drive the panel read APIs.
    robot = _robot(parent)
    _, token = issue_token(api_client)
    api_client.logout()
    response = api_client.get(state_url(robot), **bearer(token))
    assert response.status_code == 401
    assert response.json() == {
        "detail": "Authentication credentials were not provided."
    }
