import threading
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from conversations import idempotency
from conversations import views as conversation_views
from conversations.models import AIInteraction, Conversation, Message
from devices.models import Robot
from devices.tests.helpers import PAIR_URL, register_device, setup_parent
from devices.tests.test_device_auth import complete_auth, make_ec_keypair
from memory.models import Memory
from safety.models import SafetyEvent

from conversations.tests.test_audio_upload import (  # fixture re-exports for pytest
    AUDIO_URL,
    MOCK_REPLY,
    SENTINEL_B64,
    TURN_URL,
    ai,
    audio_file,
    device,
    device_client,
    make_child,
    parse_counter,
    sidecar_json,
)

pytestmark = pytest.mark.django_db

_NO_KEY = object()


# --- fixtures & helpers ---------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _clean_idempotency_registry():
    def clear():
        with idempotency._registry_guard:
            idempotency._registry.clear()

    clear()
    yield
    clear()


@pytest.fixture
def second_device(ai):
    """A second paired robot with its own parent and device token."""
    private_key, pem = make_ec_keypair()
    session_client = APIClient()
    parent = setup_parent(session_client, "second-robot-parent@example.com")
    child = make_child(parent, first_name="Rumi")
    registration = register_device(session_client, public_key=pem).json()
    response = session_client.post(
        PAIR_URL, {"pairing_code": registration["pairing_code"]}, format="json"
    )
    assert response.status_code == 200, response.content
    token = complete_auth(
        APIClient(), registration["device_id"], private_key
    )["access_token"]
    robot = Robot.objects.get(device_id=registration["device_id"])
    return SimpleNamespace(
        parent=parent, child=child, robot=robot, token=token, session=session_client
    )


def audio_request(
    device, *, key=_NO_KEY, url=AUDIO_URL, file=None, sidecar=..., token=..., **extra
):
    """POST one audio upload, optionally carrying an `Idempotency-Key`."""
    headers = {}
    auth = device.token if token is ... else token
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    value = sidecar_json(device.child) if sidecar is ... else sidecar
    if value is not None:
        headers["Conversation-Sidecar"] = value
    if key is not _NO_KEY:
        headers["Idempotency-Key"] = key
    client = APIClient()
    if auth:
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {auth}")
    return client.post(
        url,
        {"audio": file if file is not None else audio_file()},
        format="multipart",
        headers=headers,
        **extra,
    )


def db_counts():
    return {
        "conversations": Conversation.objects.count(),
        "messages": Message.objects.count(),
        "interactions": AIInteraction.objects.count(),
        "memory": Memory.objects.count(),
        "safety": SafetyEvent.objects.count(),
    }


# --- store units -----------------------------------------------------------


def test_contract_constants():
    assert idempotency.IDEMPOTENCY_TTL_SECONDS == 600
    assert idempotency.MAX_KEY_LENGTH == 255


@pytest.mark.parametrize(
    "key", ["a", "A", "0", "x" * 255, "Robot:2026-10-13.v1_a-b", "conv.001:_x"]
)
def test_valid_keys_accepted(key):
    assert idempotency.validate_idempotency_key(key) is None


@pytest.mark.parametrize(
    "key",
    [
        "",
        "x" * 256,
        "has space",
        "comma,joined",
        "k\u00e9y",
        "!",
        " leading",
        "trailing ",
    ],
)
def test_invalid_keys_rejected_deterministically(key):
    message = idempotency.validate_idempotency_key(key)
    assert message == idempotency.KEY_INVALID_MESSAGE


def test_absent_key_is_valid_and_means_no_idempotency():
    assert idempotency.validate_idempotency_key(None) is None


def test_storage_key_is_per_robot_and_hides_the_raw_key():
    first = idempotency.make_storage_key("robot-1", "my-key")
    assert first == idempotency.make_storage_key("robot-1", "my-key")
    assert first != idempotency.make_storage_key("robot-2", "my-key")
    assert first != idempotency.make_storage_key("robot-1", "my-key-2")
    assert len(first) == 64
    assert "my-key" not in first


def test_fingerprint_is_canonical_sha256_of_payload_not_raw_audio():
    sidecar = {
        "child_profile_id": "child-1",
        "conversation_id": "conv-1",
        "language_hint": "en",
    }
    fingerprint = idempotency.make_request_fingerprint(
        sidecar=sidecar, audio_bytes=b"abc"
    )
    assert len(fingerprint) == 64
    assert all(char in "0123456789abcdef" for char in fingerprint)

    shuffled = {
        "language_hint": "en",
        "conversation_id": "conv-1",
        "child_profile_id": "child-1",
    }
    assert (
        idempotency.make_request_fingerprint(sidecar=shuffled, audio_bytes=b"abc")
        == fingerprint
    )
    assert (
        idempotency.make_request_fingerprint(
            sidecar={"child_profile_id": "child-1"}, audio_bytes=b"abc"
        )
        != fingerprint
    )
    assert (
        idempotency.make_request_fingerprint(
            sidecar=dict(sidecar, language_hint="bn"), audio_bytes=b"abc"
        )
        != fingerprint
    )
    assert (
        idempotency.make_request_fingerprint(sidecar=sidecar, audio_bytes=b"abd")
        != fingerprint
    )
    assert "abc" not in fingerprint


def test_failed_workflow_leaves_no_stuck_record():
    def boom():
        raise RuntimeError("workflow failed")

    with pytest.raises(RuntimeError):
        idempotency.execute_with_idempotency("sk-fail", "fp", boom)

    entry = idempotency._registry.get("sk-fail")
    if entry is not None:
        assert entry.result is None

    executed = []

    def ok():
        executed.append(1)
        return {"ok": True}

    first_result, first_replayed = idempotency.execute_with_idempotency(
        "sk-fail", "fp", ok
    )
    assert (first_result, first_replayed) == ({"ok": True}, False)
    second_result, second_replayed = idempotency.execute_with_idempotency(
        "sk-fail", "fp", ok
    )
    assert (second_result, second_replayed) == ({"ok": True}, True)
    assert executed == [1]


def test_conflict_raises_and_leaves_the_stored_record_intact():
    idempotency.execute_with_idempotency("sk-conflict", "fp-one", lambda: {"v": 1})
    with pytest.raises(idempotency.IdempotencyConflict):
        idempotency.execute_with_idempotency("sk-conflict", "fp-two", lambda: {"v": 2})
    result, replayed = idempotency.execute_with_idempotency(
        "sk-conflict", "fp-one", lambda: {"v": 3}
    )
    assert (result, replayed) == ({"v": 1}, True)


def test_records_are_deep_copies_in_both_directions():
    payload = {"nested": {"value": 1}}
    stored, first_replayed = idempotency.execute_with_idempotency(
        "sk-copy", "fp", lambda: payload
    )
    assert first_replayed is False
    stored["nested"]["value"] = 999
    payload["nested"]["value"] = 888
    again, second_replayed = idempotency.execute_with_idempotency(
        "sk-copy", "fp", lambda: payload
    )
    assert second_replayed is True
    assert again == {"nested": {"value": 1}}


def test_concurrent_identical_requests_execute_workflow_once():
    started = threading.Event()
    release = threading.Event()
    executions = []
    outcomes = {}
    errors = []

    def workflow():
        executions.append(1)
        started.set()
        if not release.wait(timeout=10):
            raise AssertionError("release event was never set")
        return {"conversation_id": "c", "text": "once"}

    def call(label):
        try:
            result, replayed = idempotency.execute_with_idempotency(
                "concurrent-key", "fingerprint-one", workflow
            )
            outcomes[label] = (result, replayed)
        except BaseException as exc:  # noqa: BLE001 - collected for assertions
            errors.append(exc)

    first = threading.Thread(target=call, args=("first",))
    second = None
    first.start()
    try:
        assert started.wait(timeout=10), "first execution never started"
        second_entered = threading.Event()

        def call_second():
            second_entered.set()
            call("second")

        second = threading.Thread(target=call_second)
        second.start()
        assert second_entered.wait(timeout=10)
    finally:
        release.set()
        first.join(timeout=10)
        if second is not None:
            second.join(timeout=10)

    assert errors == []
    assert not first.is_alive()
    assert second is not None and not second.is_alive()
    assert executions == [1]
    assert outcomes["first"][1] is False
    assert outcomes["second"][1] is True
    assert outcomes["first"][0] == outcomes["second"][0]


# --- view: execute, replay, conflict ---------------------------------------


def test_first_request_with_key_executes_and_is_not_marked_replayed(device, ai):
    response = audio_request(device, key="first-key")
    assert response.status_code == 200, response.content
    assert not response.has_header("Idempotency-Replayed")
    body = response.json()
    assert body["text"] == MOCK_REPLY
    assert body["action"] == "WAVE"
    assert len(ai.calls) == 1
    assert Conversation.objects.count() == 1


def test_identical_request_replays_without_ai_or_db_writes(device, ai):
    first = audio_request(device, key="replay-key")
    assert first.status_code == 200, first.content
    before = db_counts()
    assert len(ai.calls) == 1

    second = audio_request(device, key="replay-key")
    assert second.status_code == 200, second.content
    assert second.headers.get("Idempotency-Replayed") == "true"
    assert second.json() == first.json()
    assert len(ai.calls) == 1
    assert db_counts() == before


def test_same_key_different_audio_is_409(device, ai):
    first = audio_request(device, key="conflict-audio")
    assert first.status_code == 200, first.content
    before = db_counts()

    conflicting = audio_request(
        device, key="conflict-audio", file=audio_file(b"some other recording")
    )
    assert conflicting.status_code == 409, conflicting.content
    assert "detail" in conflicting.json()
    assert len(ai.calls) == 1
    assert db_counts() == before


def test_same_key_different_sidecar_is_409(device, ai):
    sibling = make_child(device.parent, "Bibi")
    first = audio_request(device, key="conflict-sidecar")
    assert first.status_code == 200, first.content
    before = db_counts()

    conflicting = audio_request(
        device, key="conflict-sidecar", sidecar=sidecar_json(sibling)
    )
    assert conflicting.status_code == 409, conflicting.content
    assert len(ai.calls) == 1
    assert db_counts() == before


def test_same_key_is_independent_per_robot(device, second_device, ai):
    first_a = audio_request(device, key="shared-key")
    assert first_a.status_code == 200, first_a.content
    assert not first_a.has_header("Idempotency-Replayed")

    first_b = audio_request(second_device, key="shared-key")
    assert first_b.status_code == 200, first_b.content
    assert not first_b.has_header("Idempotency-Replayed")
    assert len(ai.calls) == 2

    replay_b = audio_request(second_device, key="shared-key")
    assert replay_b.headers.get("Idempotency-Replayed") == "true"
    replay_a = audio_request(device, key="shared-key")
    assert replay_a.headers.get("Idempotency-Replayed") == "true"
    assert len(ai.calls) == 2


def test_without_key_every_request_executes_as_before(device, ai):
    first = audio_request(device)
    second = audio_request(device)
    assert first.status_code == 200 and second.status_code == 200
    assert not first.has_header("Idempotency-Replayed")
    assert not second.has_header("Idempotency-Replayed")
    assert len(ai.calls) == 2
    assert Conversation.objects.count() == 2


def test_ttl_expires_exactly_after_600_seconds(device, ai, monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(idempotency, "_now", lambda: clock["now"])

    first = audio_request(device, key="ttl-key")
    assert first.status_code == 200, first.content
    assert not first.has_header("Idempotency-Replayed")

    clock["now"] += 599
    within_ttl = audio_request(device, key="ttl-key")
    assert within_ttl.status_code == 200, within_ttl.content
    assert within_ttl.headers.get("Idempotency-Replayed") == "true"
    assert len(ai.calls) == 1

    clock["now"] += 1
    after_ttl = audio_request(device, key="ttl-key")
    assert after_ttl.status_code == 200, after_ttl.content
    assert not after_ttl.has_header("Idempotency-Replayed")
    assert len(ai.calls) == 2


def test_view_exception_leaves_no_stuck_key(device, ai, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("simulated processing failure")

    with monkeypatch.context() as patch:
        patch.setattr(conversation_views, "run_conversation_turn", boom)
        with pytest.raises(RuntimeError):
            audio_request(device, key="flaky-key")

    recovered = audio_request(device, key="flaky-key")
    assert recovered.status_code == 200, recovered.content
    assert not recovered.has_header("Idempotency-Replayed")
    assert len(ai.calls) == 1

    replayed = audio_request(device, key="flaky-key")
    assert replayed.status_code == 200, replayed.content
    assert replayed.headers.get("Idempotency-Replayed") == "true"
    assert len(ai.calls) == 1


# --- view: validation, security boundaries, compatibility --------------------


@pytest.mark.parametrize(
    "bad_key",
    ["", "has space", "comma,joined", "k\u00e9y", "!", "x" * 256, " leading"],
)
def test_malformed_key_is_400_before_the_body_is_parsed(
    device, ai, parse_counter, bad_key
):
    response = audio_request(device, key=bad_key)
    assert response.status_code == 400, response.content
    assert "Idempotency-Key" in response.json()
    assert parse_counter["n"] == 0
    assert ai.calls == []
    assert Conversation.objects.count() == 0


def test_key_length_boundaries_are_accepted(device, ai):
    short = audio_request(device, key="k")
    longest = audio_request(device, key="k" * 255)
    assert short.status_code == 200, short.content
    assert longest.status_code == 200, longest.content
    assert len(ai.calls) == 2


def test_key_does_not_bypass_authentication(device, ai, parse_counter):
    response = audio_request(device, key="unauthenticated-key", token=None)
    assert response.status_code == 401, response.content
    assert parse_counter["n"] == 0
    assert ai.calls == []


def test_ownership_failure_precedes_the_idempotency_check(device, ai):
    first = audio_request(device, key="owner-key")
    assert first.status_code == 200, first.content

    other_parent = setup_parent(APIClient(), "other-owner@example.com")
    other_child = make_child(other_parent)
    foreign = audio_request(
        device, key="owner-key", sidecar=sidecar_json(other_child)
    )
    assert foreign.status_code == 404, foreign.content
    assert not foreign.has_header("Idempotency-Replayed")
    assert len(ai.calls) == 1


def test_json_turn_endpoint_ignores_the_idempotency_key(device, ai):
    client = device_client(device.token)
    payload = {
        "robot_id": str(device.robot.pk),
        "child_profile_id": str(device.child.pk),
        "text": "Hello",
    }
    first = client.post(
        TURN_URL, payload, format="json", headers={"Idempotency-Key": "json-key"}
    )
    second = client.post(
        TURN_URL, payload, format="json", headers={"Idempotency-Key": "json-key"}
    )
    assert first.status_code == 200, first.content
    assert second.status_code == 200, second.content
    assert not first.has_header("Idempotency-Replayed")
    assert not second.has_header("Idempotency-Replayed")
    assert len(ai.calls) == 2


def test_stored_record_never_contains_raw_audio_or_the_plaintext_key(device, ai):
    response = audio_request(device, key="privacy-key")
    assert response.status_code == 200, response.content
    blob = repr(dict(idempotency._registry))
    assert "RAW-AUDIO-SENTINEL" not in blob
    assert SENTINEL_B64 not in blob
    assert "privacy-key" not in blob
