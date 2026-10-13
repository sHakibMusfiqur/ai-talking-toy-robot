from types import SimpleNamespace

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from conversations import idempotency
from conversations import services
from conversations.ai_client import AIServiceError
from conversations.models import AIInteraction, Conversation, Message
from conversations.services import HARD_FAILURE_TEXT
from devices.models import Robot
from devices.tests.helpers import PAIR_URL, register_device, setup_parent
from devices.tests.test_device_auth import complete_auth, make_ec_keypair
from memory.models import Memory
from safety.models import SafetyEvent

from conversations.tests.test_audio_upload import (  # plain helpers only
    AUDIO_URL,
    MOCK_REPLY,
    audio_file,
    make_child,
    sidecar_json,
)

pytestmark = pytest.mark.django_db


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


class ScriptedAI:
    """Deterministic AI double: normal reply, flagged reply, or error."""

    def __init__(self):
        self.calls = []
        self.error = None
        self.flagged = False

    def __call__(self, payload):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        safety = (
            {
                "flagged": True,
                "category": "UNSAFE_TOPIC",
                "action_taken": "BLOCKED",
            }
            if self.flagged
            else {"flagged": False, "category": None, "action_taken": "ALLOWED"}
        )
        return {
            "conversation_id": payload["conversation_id"],
            "text": MOCK_REPLY,
            "language": "en",
            "emotion": "HAPPY",
            "action": "WAVE",
            "safety": safety,
            "audio": None,
            "memory_writes": [],
        }


@pytest.fixture
def ai(monkeypatch):
    fake = ScriptedAI()
    monkeypatch.setattr(services, "call_ai_turn", fake)
    return fake


@pytest.fixture
def device(ai):
    private_key, pem = make_ec_keypair()
    session_client = APIClient()
    parent = setup_parent(session_client, "boundary-parent@example.com")
    child = make_child(parent)
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


def audio_request(device, *, key, file=None, sidecar=..., **extra):
    headers = {"Authorization": f"Bearer {device.token}", "Idempotency-Key": key}
    value = sidecar_json(device.child) if sidecar is ... else sidecar
    if value is not None:
        headers["Conversation-Sidecar"] = value
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {device.token}")
    return client.post(
        AUDIO_URL,
        {"audio": file if file is not None else audio_file()},
        format="multipart",
        headers=headers,
        **extra,
    )


def turn_counts():
    return {
        "conversations": Conversation.objects.count(),
        "messages": Message.objects.count(),
        "interactions": AIInteraction.objects.count(),
        "memory": Memory.objects.count(),
        "safety": SafetyEvent.objects.count(),
    }


NO_TURNS = {
    "conversations": 0,
    "messages": 0,
    "interactions": 0,
    "memory": 0,
    "safety": 0,
}


def fail_on(manager_path):
    def boom(*args, **kwargs):
        raise RuntimeError("injected persistence failure")

    return (manager_path, boom)


# --- regression tests ------------------------------------------------------


def test_persistence_failure_leaves_no_orphaned_conversation_or_turn_records(
    device, ai, monkeypatch
):
    ai.flagged = True  # reach SafetyEvent.objects.create - last write in the block
    with monkeypatch.context() as patch:
        path, boom = fail_on("conversations.services.SafetyEvent.objects.create")
        patch.setattr(path, boom)
        with pytest.raises(RuntimeError, match="injected persistence failure"):
            audio_request(device, key="boundary-orphan")

    # The AI call completed before the injected failure: the failure is a
    # genuine mid-workflow persistence failure, not a pre-database error.
    assert len(ai.calls) == 1
    assert turn_counts() == NO_TURNS


def test_retry_after_persistence_failure_creates_exactly_one_turn(device, ai, monkeypatch):
    ai.flagged = True
    with monkeypatch.context() as patch:
        path, boom = fail_on("conversations.services.SafetyEvent.objects.create")
        patch.setattr(path, boom)
        with pytest.raises(RuntimeError, match="injected persistence failure"):
            audio_request(device, key="boundary-retry")
    assert turn_counts() == NO_TURNS
    assert len(ai.calls) == 1

    retried = audio_request(device, key="boundary-retry")
    assert retried.status_code == 200, retried.content
    assert not retried.has_header("Idempotency-Replayed")  # re-executed, not replayed
    assert turn_counts() == {
        "conversations": 1,
        "messages": 2,
        "interactions": 1,
        "memory": 0,
        "safety": 1,
    }
    assert len(ai.calls) == 2  # at-least-once AI processing on retry
    body = retried.json()
    assert body["text"] == MOCK_REPLY
    assert str(Conversation.objects.get().pk) == body["conversation_id"]


def test_persistence_failure_on_existing_conversation_leaves_no_partial_records(
    device, ai, monkeypatch
):
    conversation = Conversation.objects.create(
        robot=device.robot, child_profile=device.child
    )
    ai.flagged = True
    with monkeypatch.context() as patch:
        path, boom = fail_on("conversations.services.SafetyEvent.objects.create")
        patch.setattr(path, boom)
        with pytest.raises(RuntimeError, match="injected persistence failure"):
            audio_request(
                device,
                key="boundary-existing",
                sidecar=sidecar_json(device.child, conversation_id=conversation.pk),
            )

    assert turn_counts() == {**NO_TURNS, "conversations": 1}
    assert Conversation.objects.get().pk == conversation.pk


def test_fallback_persistence_failure_rolls_back_and_retry_preserves_fallback(
    device, ai, monkeypatch
):
    ai.error = AIServiceError("unavailable")
    with monkeypatch.context() as patch:
        path, boom = fail_on("conversations.services.AIInteraction.objects.create")
        patch.setattr(path, boom)
        with pytest.raises(RuntimeError, match="injected persistence failure"):
            audio_request(device, key="boundary-fallback")

    # Pre-fix, the conversation row survived this failure; now it rolls back.
    assert turn_counts() == NO_TURNS
    assert len(ai.calls) == 1

    # Keep the AI outage active: the retry must take the fallback path and
    # persist exactly one fallback turn (no leftovers from the failed try).
    retried = audio_request(device, key="boundary-fallback")
    assert retried.status_code == 200, retried.content
    body = retried.json()
    assert body["text"] == HARD_FAILURE_TEXT
    assert body["safety"] is None
    assert turn_counts() == {
        "conversations": 1,
        "messages": 2,
        "interactions": 1,
        "memory": 0,
        "safety": 0,
    }
    interaction = AIInteraction.objects.get()
    assert interaction.safety_flags == {"failed": True, "reason": "unavailable"}
    assert len(ai.calls) == 2


def test_successful_new_conversation_inserts_exactly_one_row_with_returned_id(
    device, ai
):
    response = audio_request(device, key="boundary-ok")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["text"] == MOCK_REPLY
    assert turn_counts() == {
        "conversations": 1,
        "messages": 2,
        "interactions": 1,
        "memory": 0,
        "safety": 0,
    }
    conversation = Conversation.objects.get()
    assert str(conversation.pk) == body["conversation_id"]
    assert conversation.language == "en"
