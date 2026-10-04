import base64
import json
import uuid
from types import SimpleNamespace

import pytest
from rest_framework.test import APIClient

from conversations import services
from conversations.ai_client import AIServiceError
from conversations.ai_contract import (
    FAILURE_REASONS,
    INPUT_FIELDS,
    OUTPUT_FIELDS,
)
from conversations.models import AIInteraction, Conversation, Message
from conversations.services import HARD_FAILURE_TEXT, RECENT_TURNS_LIMIT
from accounts.models import ChildProfile
from devices.models import Robot
from devices.tests.helpers import PAIR_URL, register_device, setup_admin, setup_parent
from devices.tests.test_device_auth import complete_auth, make_ec_keypair
from memory.models import Memory
from safety.models import SafetyEvent

TURN_URL = "/api/v1/conversations/turn/"
TURN_URL_RAW = "/api/v1/conversations/turn"

MOCK_REPLY = "Hello! I am happy to talk with you."
HARDWARE_TERMS = ("servo", "pwm", "gpio", "motor", "voltage")

pytestmark = pytest.mark.django_db


# --- fixtures & helpers ---------------------------------------------------


def make_child(parent, first_name="Ayaan"):
    return ChildProfile.objects.create(
        parent=parent, first_name=first_name, age_band="6-9", language="en"
    )


def pair_robot(client):
    registration = register_device(client).json()
    response = client.post(
        PAIR_URL, {"pairing_code": registration["pairing_code"]}, format="json"
    )
    assert response.status_code == 200, response.content
    return Robot.objects.get(device_id=registration["device_id"])


def turn_body(robot, child, text="Hello", **extra):
    body = {
        "robot_id": str(robot.pk),
        "child_profile_id": str(child.pk),
    }
    if text is not None:
        body["text"] = text
    body.update(extra)
    return body


def family(email="parent@example.com"):
    client = APIClient()
    parent = setup_parent(client, email)
    child = make_child(parent)
    robot = pair_robot(client)
    return SimpleNamespace(client=client, parent=parent, child=child, robot=robot)


def device_session(client):
    
    private_key, pem = make_ec_keypair()
    registration = register_device(client, public_key=pem).json()
    response = client.post(
        PAIR_URL, {"pairing_code": registration["pairing_code"]}, format="json"
    )
    assert response.status_code == 200, response.content
    token = complete_auth(
        APIClient(), registration["device_id"], private_key
    )["access_token"]
    robot = Robot.objects.get(device_id=registration["device_id"])
    return robot, token


class FakeAI:
    def __init__(self):
        self.calls = []
        self.output = {}
        self.error = None

    def __call__(self, payload):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        data = {
            "conversation_id": payload["conversation_id"],
            "text": MOCK_REPLY,
            "language": "en",
            "emotion": "HAPPY",
            "action": "WAVE",
            "safety": {
                "flagged": False,
                "category": None,
                "action_taken": "ALLOWED",
            },
            "audio": None,
            "memory_writes": [],
        }
        data.update(self.output)
        data["conversation_id"] = payload["conversation_id"]
        return data


@pytest.fixture
def ai(monkeypatch):
    fake = FakeAI()
    monkeypatch.setattr(services, "call_ai_turn", fake)
    return fake


@pytest.fixture
def world(ai):
    client = APIClient()
    parent = setup_parent(client, "parent@example.com")
    child = make_child(parent)
    robot = pair_robot(client)
    return SimpleNamespace(client=client, parent=parent, child=child, robot=robot)


# --- happy path & persistence ---------------------------------------------


def test_happy_path_returns_frozen_output_and_persists_the_turn(world, ai):
    response = world.client.post(
        TURN_URL, turn_body(world.robot, world.child), format="json"
    )

    assert response.status_code == 200, response.content
    data = response.json()
    assert set(data) == set(OUTPUT_FIELDS)
    assert data["text"] == MOCK_REPLY
    assert data["emotion"] == "HAPPY"
    assert data["action"] == "WAVE"
    assert data["language"] == "en"
    assert data["safety"]["flagged"] is False
    assert data["audio"] is None
    assert data["memory_writes"] == []
    assert len(ai.calls) == 1

    conversation = Conversation.objects.get()
    assert conversation.robot_id == world.robot.pk
    assert conversation.child_profile_id == world.child.pk
    assert conversation.language == "en"

    child_message, robot_message = conversation.messages.order_by("timestamp", "pk")
    assert child_message.role == Message.ROLE_CHILD
    assert child_message.text == "Hello"
    assert child_message.audio_reference == ""
    assert robot_message.role == Message.ROLE_ROBOT
    assert robot_message.text == MOCK_REPLY

    interaction = AIInteraction.objects.get(message=robot_message)
    assert interaction.emotion == "HAPPY"
    assert interaction.action == "WAVE"
    assert isinstance(interaction.latency_ms, int)
    assert interaction.latency_ms >= 0
    assert interaction.safety_flags["flagged"] is False
    assert data["conversation_id"] == str(conversation.pk)


def test_outbound_payload_matches_the_frozen_input_contract(world, ai):
    world.client.post(TURN_URL, turn_body(world.robot, world.child), format="json")

    payload = ai.calls[0]
    assert set(payload) == set(INPUT_FIELDS)
    assert payload["text"] == "Hello"
    assert payload["audio"] is None
    assert payload["language_hint"] is None
    assert payload["mode"] == "conversation"
    assert payload["context"] == {"recent_turns": [], "memories": []}
    assert payload["conversation_id"] == str(Conversation.objects.get().pk)
    assert payload["child_profile_id"] == str(world.child.pk)
    assert payload["robot_id"] == str(world.robot.pk)
    blob = json.dumps(payload).lower()
    for term in HARDWARE_TERMS:
        assert term not in blob


def test_audio_turn_forwards_audio_and_never_persists_the_raw_bytes(world, ai):
    audio = base64.b64encode(b"spoken utterance bytes").decode("ascii")
    response = world.client.post(
        TURN_URL, turn_body(world.robot, world.child, text=None, audio=audio),
        format="json",
    )

    assert response.status_code == 200, response.content
    payload = ai.calls[0]
    assert payload["audio"] == audio
    assert payload["text"] is None

    child_message = Message.objects.get(role=Message.ROLE_CHILD)
    assert child_message.text == ""
    assert child_message.audio_reference == ""
    assert Message.objects.filter(role=Message.ROLE_ROBOT).exists()
    assert AIInteraction.objects.exists()


def test_turn_without_trailing_slash_is_served(world, ai):
    response = world.client.post(
        TURN_URL_RAW, turn_body(world.robot, world.child), format="json"
    )
    assert response.status_code == 200, response.content
    assert len(ai.calls) == 1


# --- input validation ------------------------------------------------------


def test_text_and_audio_are_mutually_exclusive(world, ai):
    body = turn_body(
        world.robot, world.child, audio=base64.b64encode(b"x").decode()
    )
    response = world.client.post(TURN_URL, body, format="json")
    assert response.status_code == 400
    assert "non_field_errors" in response.json()
    assert ai.calls == []


def test_a_turn_needs_exactly_one_of_text_or_audio(world, ai):
    body = {"robot_id": str(world.robot.pk), "child_profile_id": str(world.child.pk)}
    response = world.client.post(TURN_URL, body, format="json")
    assert response.status_code == 400
    assert "non_field_errors" in response.json()
    assert ai.calls == []


def test_invalid_base64_audio_is_rejected(world, ai):
    body = turn_body(world.robot, world.child, text=None, audio="not base64!!")
    response = world.client.post(TURN_URL, body, format="json")
    assert response.status_code == 400
    assert "audio" in response.json()
    assert ai.calls == []


def test_blank_text_is_rejected(world, ai):
    response = world.client.post(
        TURN_URL, turn_body(world.robot, world.child, text="   "), format="json"
    )
    assert response.status_code == 400
    assert "text" in response.json()
    assert ai.calls == []


def test_client_supplied_context_is_rejected(world, ai):
    body = turn_body(
        world.robot,
        world.child,
        context={"recent_turns": [], "memories": [{"key": "k", "value": "v"}]},
    )
    response = world.client.post(TURN_URL, body, format="json")

    assert response.status_code == 400
    assert "context" in response.json()
    assert ai.calls == []
    assert Memory.objects.count() == 0


@pytest.mark.parametrize(
    "field,value", [("language_hint", "fr"), ("mode", "chess")]
)
def test_unknown_choice_values_are_rejected(world, ai, field, value):
    response = world.client.post(
        TURN_URL, turn_body(world.robot, world.child, **{field: value}),
        format="json",
    )
    assert response.status_code == 400
    assert field in response.json()
    assert ai.calls == []


def test_missing_and_malformed_ids_are_field_keyed_400(world, ai):
    missing = {"child_profile_id": str(world.child.pk), "text": "Hello"}
    response = world.client.post(TURN_URL, missing, format="json")
    assert response.status_code == 400
    assert "robot_id" in response.json()

    malformed = turn_body(world.robot, world.child, robot_id="not-an-id")
    response = world.client.post(TURN_URL, malformed, format="json")
    assert response.status_code == 400
    assert response.json() == {"robot_id": ["Not a valid id."]}

    bad_conversation = turn_body(
        world.robot, world.child, conversation_id="nope"
    )
    response = world.client.post(TURN_URL, bad_conversation, format="json")
    assert response.status_code == 400
    assert "conversation_id" in response.json()
    assert ai.calls == []


# --- ownership boundaries ---------------------------------------------------


def test_foreign_robot_is_404_before_any_ai_call(world, ai):
    other = family("other@example.com")
    response = world.client.post(
        TURN_URL, turn_body(other.robot, world.child), format="json"
    )
    assert response.status_code == 404
    assert ai.calls == []


def test_unknown_robot_is_404(world, ai):
    body = turn_body(world.robot, world.child)
    body["robot_id"] = "665f1c2e8b3d4a5f6e7d8c99"
    response = world.client.post(TURN_URL, body, format="json")
    assert response.status_code == 404
    assert ai.calls == []


def test_foreign_child_profile_is_404(world, ai):
    other = family("other-child@example.com")
    response = world.client.post(
        TURN_URL, turn_body(world.robot, other.child), format="json"
    )
    assert response.status_code == 404
    assert ai.calls == []


def test_unknown_child_profile_is_404(world, ai):
    body = turn_body(world.robot, world.child)
    body["child_profile_id"] = "665f1c2e8b3d4a5f6e7d8c98"
    response = world.client.post(TURN_URL, body, format="json")
    assert response.status_code == 404
    assert ai.calls == []


def test_foreign_conversation_is_404(world, ai):
    first = world.client.post(
        TURN_URL, turn_body(world.robot, world.child), format="json"
    )
    other = family("other-conv@example.com")
    body = turn_body(other.robot, other.child)
    body["conversation_id"] = first.json()["conversation_id"]
    response = other.client.post(TURN_URL, body, format="json")
    assert response.status_code == 404
    assert len(ai.calls) == 1


def test_conversation_bound_to_another_child_is_404(world, ai):
    first = world.client.post(
        TURN_URL, turn_body(world.robot, world.child), format="json"
    )
    sibling = make_child(world.parent, first_name="Rimi")
    body = turn_body(world.robot, sibling)
    body["conversation_id"] = first.json()["conversation_id"]
    response = world.client.post(TURN_URL, body, format="json")
    assert response.status_code == 404
    assert len(ai.calls) == 1


def test_admin_session_is_owner_scoped(world, ai):
    admin_client = APIClient()
    setup_admin(admin_client, "admin@example.com")
    response = admin_client.post(
        TURN_URL, turn_body(world.robot, world.child), format="json"
    )
    assert response.status_code == 404
    assert ai.calls == []


# --- authentication ---------------------------------------------------------


def test_anonymous_turn_is_401(ai):
    client = APIClient()
    response = client.post(
        TURN_URL, {"text": "Hello"}, format="json"
    )
    assert response.status_code == 401
    assert "detail" in response.json()
    assert ai.calls == []


def test_get_is_not_allowed(world):
    response = world.client.get(TURN_URL)
    assert response.status_code == 405


def test_session_turn_requires_a_csrf_token(ai):
    client = APIClient(enforce_csrf_checks=True)
    page = client.get("/login/")
    assert page.status_code == 200
    client.credentials(HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
    parent = setup_parent(client, "csrf@example.com")
    child = make_child(parent)
    # Registered/paired up front (the register endpoint enforces CSRF as
    # soon as a session exists), so this test only exercises the turn.
    robot = Robot.objects.create(
        device_id=str(uuid.uuid4()), parent=parent, nickname="CSRFBot"
    )
    # The login exchange rotates the CSRF cookie - send the current value.
    client.credentials(HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)

    ok = client.post(TURN_URL, turn_body(robot, child), format="json")
    assert ok.status_code == 200, ok.content

    client.credentials()
    bad = client.post(TURN_URL, turn_body(robot, child), format="json")
    assert bad.status_code == 403
    assert len(ai.calls) == 1


def test_device_bearer_token_can_submit_a_turn_for_its_own_robot(world, ai):
    robot, token = device_session(world.client)
    response = APIClient().post(  # session-free client: device path only
        TURN_URL,
        turn_body(robot, world.child),
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200, response.content
    assert len(ai.calls) == 1
    assert ai.calls[0]["robot_id"] == str(robot.pk)
    assert Conversation.objects.filter(robot=robot).exists()


def test_device_token_cannot_target_another_robot(world, ai):
    robot, token = device_session(world.client)
    response = APIClient().post(
        TURN_URL,
        turn_body(world.robot, world.child),
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 404
    assert ai.calls == []


def test_device_token_for_an_unpaired_robot_is_404(world, ai):
    private_key, pem = make_ec_keypair()
    registration = register_device(world.client, public_key=pem).json()
    token = complete_auth(
        APIClient(), registration["device_id"], private_key
    )["access_token"]
    body = turn_body(world.robot, world.child)
    body["robot_id"] = str(
        Robot.objects.get(device_id=registration["device_id"]).pk
    )

    response = APIClient().post(
        TURN_URL, body, format="json", HTTP_AUTHORIZATION=f"Bearer {token}"
    )
    assert response.status_code == 404
    assert ai.calls == []


def test_malformed_bearer_token_is_401(world, ai):
    response = APIClient().post(
        TURN_URL,
        turn_body(world.robot, world.child),
        format="json",
        HTTP_AUTHORIZATION="Bearer not.a.token",
    )
    assert response.status_code == 401
    assert ai.calls == []


def test_session_authentication_takes_precedence_over_a_device_token(world, ai):
    other = family("precedence@example.com")
    other_robot, other_token = device_session(other.client)

    response = world.client.post(
        TURN_URL,
        turn_body(other_robot, other.child),
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {other_token}",
    )
    # The session owner does not own that robot, so the turn is a 404 even
    # though the attached device token would have allowed it.
    assert response.status_code == 404
    assert ai.calls == []


# --- conversation continuation ----------------------------------------------


def test_second_turn_reuses_the_conversation_and_sends_recent_turns(world, ai):
    first = world.client.post(
        TURN_URL, turn_body(world.robot, world.child), format="json"
    )
    conversation_id = first.json()["conversation_id"]

    second = world.client.post(
        TURN_URL,
        turn_body(
            world.robot,
            world.child,
            text="What is your name?",
            conversation_id=conversation_id,
        ),
        format="json",
    )

    assert second.status_code == 200, second.content
    assert second.json()["conversation_id"] == conversation_id
    assert ai.calls[1]["conversation_id"] == conversation_id
    assert ai.calls[1]["context"]["recent_turns"] == [
        {"role": "user", "text": "Hello"},
        {"role": "assistant", "text": MOCK_REPLY},
    ]
    assert Conversation.objects.count() == 1
    assert Message.objects.count() == 4


def test_voice_only_history_is_skipped_in_the_context(world, ai):
    audio = base64.b64encode(b"spoken").decode("ascii")
    first = world.client.post(
        TURN_URL,
        turn_body(world.robot, world.child, text=None, audio=audio),
        format="json",
    )
    conversation_id = first.json()["conversation_id"]

    world.client.post(
        TURN_URL,
        turn_body(
            world.robot, world.child, text="Hi again", conversation_id=conversation_id
        ),
        format="json",
    )

    assert ai.calls[1]["context"]["recent_turns"] == [
        {"role": "assistant", "text": MOCK_REPLY}
    ]


def test_recent_turns_context_is_bounded(world, ai):
    first = world.client.post(
        TURN_URL, turn_body(world.robot, world.child), format="json"
    )
    conversation = Conversation.objects.get()
    for index in range(RECENT_TURNS_LIMIT + 5):
        Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_CHILD,
            text=f"m{index:02d}",
        )

    world.client.post(
        TURN_URL,
        turn_body(
            world.robot,
            world.child,
            text="next",
            conversation_id=first.json()["conversation_id"],
        ),
        format="json",
    )

    turns = ai.calls[-1]["context"]["recent_turns"]
    assert len(turns) == RECENT_TURNS_LIMIT
    assert turns[0] == {"role": "user", "text": "m05"}
    assert turns[-1] == {
        "role": "user",
        "text": f"m{RECENT_TURNS_LIMIT + 4:02d}",
    }


def test_conversation_language_follows_the_latest_turn(world, ai):
    first = world.client.post(
        TURN_URL, turn_body(world.robot, world.child), format="json"
    )
    ai.output = {"language": "bn", "emotion": "NEUTRAL", "action": "IDLE"}

    response = world.client.post(
        TURN_URL,
        turn_body(
            world.robot,
            world.child,
            text="Amar naam ki?",
            conversation_id=first.json()["conversation_id"],
        ),
        format="json",
    )

    assert response.json()["language"] == "bn"
    assert Conversation.objects.get().language == "bn"


# --- memory write path ------------------------------------------------------


def test_memory_writes_are_persisted_against_the_child(world, ai):
    ai.output = {"memory_writes": [{"key": "favorite_animal", "value": "otter"}]}

    response = world.client.post(
        TURN_URL, turn_body(world.robot, world.child), format="json"
    )

    assert response.status_code == 200
    assert response.json()["memory_writes"] == [
        {"key": "favorite_animal", "value": "otter"}
    ]
    memory = Memory.objects.get()
    assert memory.child_profile_id == world.child.pk
    assert memory.source_conversation_id == Conversation.objects.get().pk
    assert memory.key == "favorite_animal"
    assert memory.value == "otter"


def test_memory_write_that_does_not_fit_the_model_is_skipped(world, ai):
    ai.output = {
        "memory_writes": [
            {"key": "k" * 120, "value": "too long"},
            {"key": "valid_key", "value": "kept"},
        ]
    }

    response = world.client.post(
        TURN_URL, turn_body(world.robot, world.child), format="json"
    )

    assert response.status_code == 200
    assert response.json()["memory_writes"] == [
        {"key": "valid_key", "value": "kept"}
    ]
    assert Memory.objects.count() == 1


# --- safety event logging ---------------------------------------------------


@pytest.mark.parametrize(
    "action_taken,severity",
    [
        ("REDIRECTED", "medium"),
        ("BLOCKED", "high"),
        ("ALLOWED_WITH_CAUTION", "low"),
    ],
)
def test_flagged_turn_logs_a_safety_event(world, ai, action_taken, severity):
    ai.output = {
        "safety": {
            "flagged": True,
            "category": "PERSONAL_INFO_REQUEST",
            "action_taken": action_taken,
        }
    }

    response = world.client.post(
        TURN_URL, turn_body(world.robot, world.child), format="json"
    )

    assert response.status_code == 200
    assert response.json()["safety"] == ai.output["safety"]
    event = SafetyEvent.objects.get()
    assert event.child_profile_id == world.child.pk
    assert event.robot_id == world.robot.pk
    assert event.conversation_id == Conversation.objects.get().pk
    assert event.message_id == Message.objects.get(role=Message.ROLE_CHILD).pk
    assert event.category == "PERSONAL_INFO_REQUEST"
    assert event.severity == severity
    assert event.action_taken == action_taken
    assert event.reviewed_by is None
    assert AIInteraction.objects.get().safety_flags == ai.output["safety"]


def test_unflagged_turn_logs_no_safety_event(world, ai):
    world.client.post(TURN_URL, turn_body(world.robot, world.child), format="json")
    assert SafetyEvent.objects.count() == 0


# --- hard-failure fallback ---------------------------------------------------


@pytest.mark.parametrize("reason", sorted(FAILURE_REASONS))
def test_ai_service_failure_falls_back_and_still_persists_the_turn(
    world, ai, reason
):
    ai.error = AIServiceError(reason)

    response = world.client.post(
        TURN_URL, turn_body(world.robot, world.child), format="json"
    )

    assert response.status_code == 200, response.content
    data = response.json()
    assert set(data) == set(OUTPUT_FIELDS)
    assert data["text"] == HARD_FAILURE_TEXT
    assert data["emotion"] == "CONCERNED"
    assert data["action"] == "IDLE"
    assert data["language"] == "en"
    assert data["safety"] is None
    assert data["audio"] is None
    assert data["memory_writes"] == []
    assert len(ai.calls) == 1

    assert Conversation.objects.count() == 1
    messages = list(Conversation.objects.get().messages.order_by("timestamp", "pk"))
    assert [message.role for message in messages] == ["child", "robot"]
    assert messages[0].text == "Hello"
    assert messages[1].text == HARD_FAILURE_TEXT

    interaction = AIInteraction.objects.get()
    assert interaction.safety_flags == {"failed": True, "reason": reason}
    assert interaction.emotion == "CONCERNED"
    assert interaction.action == "IDLE"
    assert isinstance(interaction.latency_ms, int)

    assert SafetyEvent.objects.count() == 0
    assert Memory.objects.count() == 0


def test_fallback_never_silences_the_child_without_persisting_the_input(
    world, ai
):
    ai.error = AIServiceError("unavailable")
    world.client.post(TURN_URL, turn_body(world.robot, world.child), format="json")

    child_message = Message.objects.get(role=Message.ROLE_CHILD)
    assert child_message.text == "Hello"
    assert Message.objects.filter(role=Message.ROLE_ROBOT).exists()


# --- leak checks -------------------------------------------------------------


def test_no_hardware_vocabulary_reaches_the_wire(world, ai):
    response = world.client.post(
        TURN_URL, turn_body(world.robot, world.child), format="json"
    )

    blobs = [json.dumps(ai.calls[0]), json.dumps(response.json())]
    interaction = AIInteraction.objects.get()
    blobs.extend(
        [
            interaction.emotion,
            interaction.action,
            json.dumps(interaction.safety_flags),
        ]
    )
    for blob in blobs:
        lowered = blob.lower()
        for term in HARDWARE_TERMS:
            assert term not in lowered
