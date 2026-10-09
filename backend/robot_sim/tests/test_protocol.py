"""Unit tests for the frozen device-side protocol logic (pure, no I/O)."""

import json

import pytest
from core.semantic_actions import SEMANTIC_ACTIONS
from mqtt_bridge import topics
from mqtt_bridge.payloads import EventPayloadSerializer

from robot_sim import protocol

DEVICE_ID = "11111111-2222-3333-4444-555555555555"


def test_own_subscriptions_are_exact_four_own_topics_qos1():
    expected = (
        (f"robots/{DEVICE_ID}/command", 1),
        (f"robots/{DEVICE_ID}/config/notify", 1),
        (f"robots/{DEVICE_ID}/ota/notify", 1),
        (f"robots/{DEVICE_ID}/content/notify", 1),
    )
    subs = protocol.own_subscriptions(DEVICE_ID)
    assert subs == expected
    for topic, qos in subs:
        assert qos == 1
        assert "+" not in topic and "#" not in topic
        assert topics.parse_topic(topic) is not None


def test_own_subscriptions_reject_hostile_device_id():
    for hostile in ("a/b", "a+b", "a#b", ""):
        with pytest.raises(ValueError):
            protocol.own_subscriptions(hostile)


def test_heartbeat_payload_is_bare_empty_object():
    assert protocol.HEARTBEAT_PAYLOAD == "{}"


def test_status_json_is_partial_online_state_only():
    parsed = json.loads(protocol.status_json(protocol.STATE_BOOT))
    assert parsed == {"online": True, "state": "BOOT"}
    assert set(parsed) == {"online", "state"}
    # Never fabricate telemetry the simulator does not have.
    assert "rssi" not in parsed and "battery_percent" not in parsed


def test_offline_status_json_shape():
    parsed = json.loads(protocol.OFFLINE_STATUS_JSON)
    assert parsed == {"online": False}
    assert set(parsed) == {"online"}


@pytest.mark.parametrize("action", sorted(SEMANTIC_ACTIONS))
def test_match_semantic_action_accepts_all_cases(action):
    assert protocol.match_semantic_action(action) == action
    assert protocol.match_semantic_action(action.lower()) == action
    assert protocol.match_semantic_action(action[0] + action[1:].lower()) == action


@pytest.mark.parametrize(
    "value",
    [
        None,
        5,
        b"WAVE",
        "",
        "SERVO",
        "WAVE;LEDs.on",
        "W" * 64,
        "W" * 65,
        "WAVE_UP",
    ],
)
def test_match_semantic_action_rejects_non_contract_values(value):
    assert protocol.match_semantic_action(value) is None


def test_match_semantic_action_length_boundary():
    assert protocol.match_semantic_action("W" * 64) is None  # not a member
    assert protocol.match_semantic_action("W" * 65) is None  # len > 64
    assert protocol.match_semantic_action("LISTENING_RETRY") == "LISTENING_RETRY"
    assert len("LISTENING_RETRY") <= protocol.MAX_ACTION_LEN


@pytest.mark.parametrize("action", sorted(SEMANTIC_ACTIONS))
def test_evaluate_accepts_case_insensitive_and_returns_canonical(action):
    for variant in (action, action.lower(), action[0] + action[1:].lower()):
        chosen, reject = protocol.evaluate_command(
            json.dumps({"action": variant}).encode("utf-8")
        )
        assert chosen == action
        assert reject is None


def test_evaluate_ignores_extra_keys_like_firmware():
    chosen, reject = protocol.evaluate_command(
        b'{"action":"wave","angle":45,"note":"ignored"}'
    )
    assert chosen == "WAVE"
    assert reject is None


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"",
        b"123",
        b'"a string"',
        b"[]",
        b"null",
        b"true",
        b"{}",
        b'{"action":123}',
        b'{"action":null}',
        b'{"action":true}',
        b'{"action":[]}',
        b'{"action":{"a":1}}',
        None,
        12345,
    ],
)
def test_evaluate_rejects_invalid_payload_firmware_shape(raw):
    chosen, reject = protocol.evaluate_command(raw)
    assert chosen is None
    assert reject == {
        "event_type": "command.rejected",
        "payload": {"action": "", "simulated": True, "reason": "invalid_payload"},
    }


@pytest.mark.parametrize(
    "raw,expected_action",
    [
        (b'{"action":"servo"}', "servo"),
        (b'{"action":"move_servo9000"}', "move_servo9000"),
        (b'{"action":""}', ""),
        (b'{"action":"WAVE;LEDs.on"}', "WAVE;LEDs.on"),
        (b'{"action":"wave_up"}', "wave_up"),
        (b'{"action":"' + b"W" * 65 + b'"}', "W" * 64),  # %.64s truncation
    ],
)
def test_evaluate_rejects_unknown_action_verbatim(raw, expected_action):
    chosen, reject = protocol.evaluate_command(raw)
    assert chosen is None
    assert reject["event_type"] == "command.rejected"
    assert reject["payload"] == {
        "action": expected_action,
        "simulated": True,
        "reason": "unknown_action",
    }


def test_command_events_pass_backend_event_serializer():
    samples = [
        protocol.command_event("command.received", action="WAVE"),
        protocol.command_event("command.executed", action="WAVE"),
        protocol.evaluate_command(b"not-json")[1],
        protocol.evaluate_command(b'{"action":"servo"}')[1],
        protocol.lifecycle_event("simulator.started", {"device_id": DEVICE_ID}),
    ]
    for event in samples:
        serializer = EventPayloadSerializer(data=event)
        assert serializer.is_valid(), serializer.errors
        assert protocol.EVENT_TYPE_RE.match(event["event_type"])


def test_every_simulator_event_stamps_simulated_flag():
    events = [
        protocol.command_event("command.received", action="IDLE"),
        protocol.command_event("command.executed", action="IDLE"),
        protocol.command_event("command.rejected", reason="unknown_action"),
        protocol.lifecycle_event("simulator.started"),
    ]
    for event in events:
        assert event["payload"]["simulated"] is True
        assert set(event) == {"event_type", "payload"}


def test_lifecycle_event_merges_extra_payload():
    event = protocol.lifecycle_event("simulator.started", {"device_id": "x"})
    assert event["payload"] == {"simulated": True, "device_id": "x"}
