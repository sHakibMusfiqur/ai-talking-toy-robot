import asyncio
import base64
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.contrib import staticfiles  # noqa: F401  (keep app registry happy)
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.files import temp as files_temp
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from accounts.models import ChildProfile
from config import asgi as asgi_module
from config.settings import audio_upload_bound_from_env, validate_audio_upload_bounds
from conversations import services
from conversations.models import AIInteraction, Conversation, Message
from conversations.serializers import ConversationAudioUploadSerializer
from conversations.urls import urlpatterns as conversation_urlpatterns
from conversations.views import ConversationAudioTurnView, ConversationTurnView
from core.audio_upload import content_length_error, scope_post_error
from devices.models import Robot
from devices.tests.helpers import PAIR_URL, register_device, setup_parent
from devices.tests.test_device_auth import complete_auth, make_ec_keypair
from memory.models import Memory
from safety.models import SafetyEvent

AUDIO_URL = "/api/v1/conversations/turn/audio/"
AUDIO_URL_RAW = "/api/v1/conversations/turn/audio"
TURN_URL = "/api/v1/conversations/turn/"

MAX_FILE_BYTES = 1024 * 1024
BODY_MAX_BYTES = MAX_FILE_BYTES + 16 * 1024
SENTINEL = b"RAW-AUDIO-SENTINEL-" + bytes(range(64)) * 8
SENTINEL_B64 = base64.b64encode(SENTINEL).decode("ascii")
MOCK_REPLY = "Hello! I am happy to talk with you."

pytestmark = pytest.mark.django_db


# --- fixtures & helpers ---------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


def make_child(parent, first_name="Ayaan"):
    return ChildProfile.objects.create(
        parent=parent, first_name=first_name, age_band="6-9", language="en"
    )


class FakeAI:
    def __init__(self):
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)
        return {
            "conversation_id": payload["conversation_id"],
            "text": MOCK_REPLY,
            "language": "en",
            "emotion": "HAPPY",
            "action": "WAVE",
            "safety": {"flagged": False, "category": None, "action_taken": "ALLOWED"},
            "audio": None,
            "memory_writes": [],
        }


@pytest.fixture
def ai(monkeypatch):
    fake = FakeAI()
    monkeypatch.setattr(services, "call_ai_turn", fake)
    return fake


@pytest.fixture
def device(ai):
    """A paired robot with a device token, its parent, and a logged-in
    session client for the same parent."""
    private_key, pem = make_ec_keypair()
    session_client = APIClient()
    parent = setup_parent(session_client, "parent@example.com")
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


def audio_file(content=SENTINEL, name="clip.wav"):
    return SimpleUploadedFile(name, content, content_type="audio/wav")


def sidecar_json(child, **extra):
    payload = {"child_profile_id": str(child.pk)}
    payload.update({k: str(v) if k.endswith("_id") else v for k, v in extra.items()})
    return json.dumps(payload)


def device_client(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def upload(
    device,
    *,
    url=AUDIO_URL,
    file=None,
    sidecar=...,
    include_sidecar=True,
    token=...,
    **extra,
):
    """POST one audio upload from the device's Bearer context."""
    headers = {}
    auth = device.token if token is ... else token
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    if include_sidecar:
        value = sidecar_json(device.child) if sidecar is ... else sidecar
        if value is not None:
            headers["Conversation-Sidecar"] = value
    client = APIClient()
    if auth:
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {auth}")
    return client.post(
        url, {"audio": file if file is not None else audio_file()},
        format="multipart", headers=headers, **extra
    )


@pytest.fixture
def parse_counter(monkeypatch):
    """Count real multipart body parses (from django.http.multipartparser)."""
    from django.http.multipartparser import MultiPartParser

    calls = {"n": 0}
    original = MultiPartParser._parse

    def spy(self):
        calls["n"] += 1
        return original(self)

    monkeypatch.setattr(MultiPartParser, "_parse", spy)
    return calls


def forbid_temp_files(monkeypatch, tmp_path):
    """Fail loudly if anything tries to create a temp upload file; keep
    FILE_UPLOAD_TEMP_DIR pointed at an empty sentinel directory."""

    def boom(*args, **kwargs):
        raise AssertionError("a temporary upload file was created on disk")

    monkeypatch.setattr(files_temp, "NamedTemporaryFile", boom)
    return override_settings(FILE_UPLOAD_TEMP_DIR=str(tmp_path))


# --- settings & gate units ------------------------------------------------


def test_settings_bounds_are_the_approved_values():
    assert settings.AUDIO_UPLOAD_MAX_FILE_BYTES == 1048576
    assert settings.AUDIO_UPLOAD_MAX_BODY_BYTES == 1064960
    assert settings.FILE_UPLOAD_MAX_MEMORY_SIZE == settings.AUDIO_UPLOAD_MAX_BODY_BYTES


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 1048576),
        ("", 1048576),
        ("   ", 1048576),
        ("1048576", 1048576),
        ("524288", 524288),
        ("1", 1),
    ],
)
def test_env_bound_overrides_allowed_values(raw, expected):
    assert (
        audio_upload_bound_from_env(
            "AUDIO_UPLOAD_MAX_FILE_BYTES", raw, 1048576, 1048576
        )
        == expected
    )


@pytest.mark.parametrize(
    "raw,match",
    [
        ("2097152", "exceeds the approved"),
        ("1048577", "exceeds the approved"),
        ("abc", "must be a positive integer"),
        ("-1", "must be a positive integer"),
        ("+5", "must be a positive integer"),
        ("1e6", "must be a positive integer"),
        ("１１", "must be a positive integer"),
        ("9" * 11, "must be a positive integer"),
        ("00000000000", "must be a positive integer"),
        ("0", "must be at least 1"),
    ],
)
def test_env_bound_overrides_reject_unsafe_values(raw, match):
    with pytest.raises(ImproperlyConfigured, match=match):
        audio_upload_bound_from_env(
            "AUDIO_UPLOAD_MAX_FILE_BYTES", raw, 1048576, 1048576
        )


def test_validate_audio_upload_bounds_rejects_file_above_body():
    assert validate_audio_upload_bounds(1048576, 1064960) == (1048576, 1064960)
    with pytest.raises(ImproperlyConfigured, match="must not exceed"):
        validate_audio_upload_bounds(1064960, 1048576)


_STARTUP_CODE = (
    "import os;"
    "os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings';"
    "import django;django.setup();"
    "from django.conf import settings;"
    "print(settings.AUDIO_UPLOAD_MAX_FILE_BYTES,"
    "settings.AUDIO_UPLOAD_MAX_BODY_BYTES,"
    "settings.FILE_UPLOAD_MAX_MEMORY_SIZE)"
)


def _settings_startup(extra_env):
    """Boot config.settings in a clean subprocess with given env overrides."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUDIO_UPLOAD_")
    }
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", _STARTUP_CODE],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_env_override_above_contract_fails_startup_deterministically():
    result = _settings_startup({"AUDIO_UPLOAD_MAX_FILE_BYTES": "2097152"})
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "ImproperlyConfigured" in combined
    assert "AUDIO_UPLOAD_MAX_FILE_BYTES" in combined
    assert "exceeds the approved" in combined


def test_env_override_may_lower_bounds_end_to_end():
    result = _settings_startup({"AUDIO_UPLOAD_MAX_FILE_BYTES": "524288"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["524288", "1064960", "1064960"]


def test_env_override_body_below_file_bound_fails_startup_deterministically():
    result = _settings_startup({"AUDIO_UPLOAD_MAX_BODY_BYTES": "1000000"})
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "ImproperlyConfigured" in combined
    assert "must not exceed" in combined


def test_env_override_non_integer_fails_startup_deterministically():
    result = _settings_startup({"AUDIO_UPLOAD_MAX_BODY_BYTES": "abc"})
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "ImproperlyConfigured" in combined
    assert "must be a positive integer" in combined


@pytest.mark.parametrize(
    "raw,expect_none",
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("abc", False),
        ("-1", False),
        ("10, 20", False),
        ("10,10", False),
        (str(BODY_MAX_BYTES + 1), False),
        (str(BODY_MAX_BYTES), True),
        ("0", True),
    ],
)
def test_content_length_error_decisions(raw, expect_none):
    assert (content_length_error(raw) is None) is expect_none


def _compact_id(value):
    """Keep node ids short: PYTEST_CURRENT_TEST is a Windows env var and
    cannot exceed 32767 characters, so huge digit-string params need ids."""
    if isinstance(value, (tuple, list)):
        parts = [_compact_id(item) for item in value]
        joined = "-".join(part for part in parts if part)
        return joined or None
    if isinstance(value, str) and len(value) > 60:
        return f"long{len(value)}"
    if isinstance(value, bytes):
        return "bytes"
    return None


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "Missing Content-Length header."),
        ("", "Missing Content-Length header."),
        ("   ", "Missing Content-Length header."),
        (" 123 ", None),
        ("0", None),
        ("0000005", None),
        (str(BODY_MAX_BYTES), None),
        (
            str(BODY_MAX_BYTES + 1),
            "Content-Length exceeds the allowed upload size.",
        ),
        ("9" * 64, "Content-Length exceeds the allowed upload size."),
        ("9" * 65, "Malformed Content-Length header."),
        ("9" * 4300, "Malformed Content-Length header."),
        ("9" * 100000, "Malformed Content-Length header."),
        ("-1", "Negative Content-Length header."),
        ("-0", "Negative Content-Length header."),
        ("500, 600", "Duplicate or conflicting Content-Length header."),
        ("10,10", "Duplicate or conflicting Content-Length header."),
        ("abc", "Malformed Content-Length header."),
        ("+5", "Malformed Content-Length header."),
        ("5.0", "Malformed Content-Length header."),
        ("12 34", "Malformed Content-Length header."),
        ("1\t2", "Malformed Content-Length header."),
        ("１２３", "Malformed Content-Length header."),
        ("١٢٣", "Malformed Content-Length header."),
        ("²", "Malformed Content-Length header."),
        ("٥", "Malformed Content-Length header."),
        (b"123", "Malformed Content-Length header."),
    ],
    ids=_compact_id,
)
def test_content_length_error_is_strict_ascii_and_never_raises(raw, expected):
    assert content_length_error(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "Missing Content-Length header."),
        ("", "Missing Content-Length header."),
        ("0", None),
        (str(BODY_MAX_BYTES), None),
        (
            str(BODY_MAX_BYTES + 1),
            "Content-Length exceeds the allowed upload size.",
        ),
        ("-1", "Negative Content-Length header."),
        ("500, 600", "Duplicate or conflicting Content-Length header."),
        ("abc", "Malformed Content-Length header."),
        ("²", "Malformed Content-Length header."),
        ("9" * 100000, "Malformed Content-Length header."),
    ],
    ids=_compact_id,
)
def test_wsgi_and_asgi_gates_decide_identically(raw, expected):
    """Same input, same message on both gate surfaces - the scope gate
    only decodes headers, then delegates to content_length_error."""
    assert content_length_error(raw) == expected
    headers = [] if raw is None else [(b"content-length", raw.encode("latin-1"))]
    scope = {"type": "http", "method": "POST", "path": AUDIO_URL, "headers": headers}
    assert scope_post_error(scope) == expected


def test_scope_gate_tolerates_str_headers_and_odd_scopes():
    assert (
        scope_post_error(
            {
                "type": "http",
                "method": b"POST",
                "path": AUDIO_URL.encode("ascii"),
                "headers": [("content-length", "abc")],
            }
        )
        == "Malformed Content-Length header."
    )
    assert scope_post_error({"type": "lifespan"}) is None
    assert (
        scope_post_error(
            {"type": "http", "method": None, "path": None, "headers": None}
        )
        is None
    )


def test_scope_gate_only_targets_audio_posts():
    base = {"type": "http", "method": "POST", "path": AUDIO_URL, "headers": []}
    assert scope_post_error(base) is not None  # missing CL
    assert scope_post_error({**base, "method": "GET"}) is None
    assert scope_post_error({**base, "path": "/api/v1/conversations/turn/"}) is None
    ok = {**base, "headers": [(b"content-length", b"1234")]}
    assert scope_post_error(ok) is None
    dup = {**base, "headers": [(b"content-length", b"1"), (b"content-length", b"2")]}
    assert scope_post_error(dup) is not None
    chunked = {**base, "headers": [(b"transfer-encoding", b"chunked")]}
    assert scope_post_error(chunked) is not None


# --- accepted path & size enforcement --------------------------------------


def test_accepted_upload_stays_in_memory(device, tmp_path, monkeypatch, ai):
    with forbid_temp_files(monkeypatch, tmp_path):
        response = upload(device, file=audio_file(b"tiny clip"))
    assert response.status_code == 200, response.content
    assert set(response.json()) == {
        "conversation_id", "text", "language", "emotion", "action",
        "safety", "audio", "memory_writes",
    }
    assert list(tmp_path.iterdir()) == []
    assert len(ai.calls) == 1
    assert ai.calls[0]["audio"] == base64.b64encode(b"tiny clip").decode("ascii")
    assert ai.calls[0]["text"] is None


def test_exactly_one_mib_is_accepted(device, ai):
    response = upload(device, file=audio_file(b"a" * MAX_FILE_BYTES))
    assert response.status_code == 200, response.content
    assert len(ai.calls) == 1
    # Contract fit: 1 MiB base64s to 1,398,104 chars < 1,400,000 cap.
    assert len(ai.calls[0]["audio"]) < 1_400_000


def test_actual_bytes_over_one_mib_rejected_400(
    device, ai, parse_counter, tmp_path, monkeypatch
):
    with forbid_temp_files(monkeypatch, tmp_path):
        response = upload(device, file=audio_file(b"x" * (MAX_FILE_BYTES + 1)))
    assert response.status_code == 400, response.content
    assert "exceeds" in response.json()["detail"]
    assert parse_counter["n"] == 1  # rejection happens during parsing, on bytes
    assert ai.calls == []
    assert Conversation.objects.count() == 0
    assert list(tmp_path.iterdir()) == []


def test_serializer_rejects_file_over_one_mib():
    serializer = ConversationAudioUploadSerializer(
        data={"audio": audio_file(b"x" * (MAX_FILE_BYTES + 1))}
    )
    assert not serializer.is_valid()
    assert "audio" in serializer.errors


def test_serializer_rejects_unknown_form_fields():
    serializer = ConversationAudioUploadSerializer(
        data={"audio": audio_file(b"ok"), "extra": "nope"}
    )
    assert not serializer.is_valid()
    assert "extra" in serializer.errors


# --- pre-parse Content-Length gate -----------------------------------------


@pytest.mark.parametrize(
    "bad_cl",
    [
        "",
        "abc",
        "-1",
        str(BODY_MAX_BYTES + 1),
        "500, 600",
        "²",
        "１２３",
        "١٢٣",
        "9" * 100000,
        "9" * 4300,
    ],
    ids=[
        "missing",
        "malformed",
        "negative",
        "oversized",
        "duplicate",
        "unicode-superscript",
        "unicode-fullwidth",
        "unicode-arabic",
        "very-long-digits",
        "digits-over-int-limit",
    ],
)
def test_bad_content_length_rejected_before_body_parsing(
    device, parse_counter, bad_cl
):
    response = upload(device, CONTENT_LENGTH=bad_cl)
    assert response.status_code == 400, response.content
    assert "Content-Length" in response.json()["detail"]
    assert parse_counter["n"] == 0


def test_unauthenticated_valid_content_length_never_parses(device, parse_counter):
    response = upload(device, token=None)
    assert response.status_code == 401, response.content
    assert "Bearer" in response.headers.get("WWW-Authenticate", "")
    assert parse_counter["n"] == 0


def test_session_caller_cannot_use_the_device_only_endpoint(
    device, parse_counter, ai
):
    response = device.session.post(
        AUDIO_URL,
        {"audio": audio_file(b"session attempt")},
        format="multipart",
        headers={"Conversation-Sidecar": sidecar_json(device.child)},
    )
    assert response.status_code == 401, response.content
    assert parse_counter["n"] == 0
    # The same session caller still works on the JSON turn endpoint.
    json_turn = device.session.post(
        TURN_URL,
        {"robot_id": str(device.robot.pk), "child_profile_id": str(device.child.pk),
         "text": "Hello"},
        format="json",
    )
    assert json_turn.status_code == 200, json_turn.content


def test_json_endpoint_remains_backward_compatible(device, ai):
    response = device_client(device.token).post(
        TURN_URL,
        {"robot_id": str(device.robot.pk), "child_profile_id": str(device.child.pk),
         "text": "Hello"},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["text"] == MOCK_REPLY


def test_raw_audio_url_served_without_redirect(device, ai):
    response = upload(device, url=AUDIO_URL_RAW)
    assert response.status_code == 200, response.content


def test_non_post_is_405(device):
    response = device_client(device.token).get(AUDIO_URL)
    assert response.status_code == 405, response.content


# --- sidecar & ownership ---------------------------------------------------


def test_missing_sidecar_header_is_400_without_parsing(device, parse_counter):
    response = upload(device, include_sidecar=False)
    assert response.status_code == 400, response.content
    assert "Conversation-Sidecar" in response.json()
    assert parse_counter["n"] == 0


def test_invalid_sidecar_json_is_400(device, parse_counter):
    response = upload(device, sidecar="{not json")
    assert response.status_code == 400, response.content
    assert parse_counter["n"] == 0


def test_sidecar_requires_child_profile_id(device):
    response = upload(device, sidecar=json.dumps({"language_hint": "en"}))
    assert response.status_code == 400, response.content
    assert "child_profile_id" in response.json()


def test_sidecar_rejects_mode(device):
    payload = {"child_profile_id": str(device.child.pk), "mode": "story"}
    response = upload(device, sidecar=json.dumps(payload))
    assert response.status_code == 400, response.content
    assert response.json()["mode"] == ["Unexpected field."]


def test_sidecar_child_of_another_parent_is_404(device, ai):
    other_client = APIClient()
    other_parent = setup_parent(other_client, "other@example.com")
    other_child = make_child(other_parent, first_name="Other")
    response = upload(device, sidecar=sidecar_json(other_child))
    assert response.status_code == 404, response.content
    assert ai.calls == []


def test_sidecar_conversation_bound_to_another_child_is_404(device, ai):
    sibling = make_child(device.parent, first_name="Sibling")
    conversation = Conversation.objects.create(
        robot=device.robot, child_profile=sibling
    )
    response = upload(
        device, sidecar=sidecar_json(device.child, conversation_id=str(conversation.pk))
    )
    assert response.status_code == 404, response.content
    assert ai.calls == []


# --- privacy ----------------------------------------------------------------


def test_raw_audio_never_reaches_db_logs_or_files(
    device, ai, caplog, tmp_path, monkeypatch
):
    with forbid_temp_files(monkeypatch, tmp_path):
        with caplog.at_level(logging.DEBUG):
            response = upload(device, file=audio_file(SENTINEL))
    assert response.status_code == 200, response.content
    assert len(ai.calls) == 1  # forwarded exactly once per request

    for model in (Message, AIInteraction, Conversation, Memory, SafetyEvent):
        for obj in model.objects.all():
            blob = repr(obj.__dict__)
            assert SENTINEL_B64 not in blob
            assert "RAW-AUDIO-SENTINEL" not in blob

    assert all(m.audio_reference == "" for m in Message.objects.all())
    assert SENTINEL_B64 not in caplog.text
    assert "RAW-AUDIO-SENTINEL" not in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_persisted_child_message_has_no_text_for_audio_turn(device, ai):
    response = upload(device, file=audio_file(b"spoken words"))
    assert response.status_code == 200, response.content
    conversation = Conversation.objects.get()
    child_message = conversation.messages.filter(role=Message.ROLE_CHILD).get()
    assert child_message.text == ""
    assert child_message.audio_reference == ""


# --- throttling ------------------------------------------------------------


def test_audio_shares_the_per_robot_throttle_bucket(device, ai):
    for i in range(30):
        response = upload(device, file=audio_file(b"x"))
        assert response.status_code == 200, response.content
    blocked = upload(device, file=audio_file(b"x"))
    assert blocked.status_code == 429, blocked.content
    assert "throttled" in blocked.json()["detail"].lower()
    assert "Retry-After" in blocked.headers
    # Same robot on the JSON turn endpoint shares the exhausted bucket.
    json_turn = device_client(device.token).post(
        TURN_URL,
        {"robot_id": str(device.robot.pk), "child_profile_id": str(device.child.pk),
         "text": "Hello"},
        format="json",
    )
    assert json_turn.status_code == 429, json_turn.content
    # A different principal (the parent session) has its own bucket.
    session_turn = device.session.post(
        TURN_URL,
        {"robot_id": str(device.robot.pk), "child_profile_id": str(device.child.pk),
         "text": "Hello"},
        format="json",
    )
    assert session_turn.status_code == 200, session_turn.content


def test_both_views_attach_the_same_throttle_class():
    from conversations.throttles import ConversationTurnThrottle

    assert ConversationAudioTurnView.throttle_classes == [
        ConversationTurnThrottle
    ]
    assert ConversationTurnView.throttle_classes == [ConversationTurnThrottle]
    assert any(
        pattern.name == "api-conversation-turn-audio" for pattern in conversation_urlpatterns
    )


# --- ASGI wrapper (unit only - no real server buffering) --------------------


def test_asgi_wrapper_gate_logic(monkeypatch):
    

    async def scenario():
        delegated = []
        sent = []

        async def fake_app(scope, receive, send):
            delegated.append(scope)

        async def send(message):
            sent.append(message)

        async def receive():  # pragma: no cover - never used when gated
            return {"type": "http.request", "body": b"", "more_body": False}

        monkeypatch.setattr(asgi_module, "_django_asgi_app", fake_app)

        def scope(headers, method="POST", path=AUDIO_URL):
            return {"type": "http", "method": method, "path": path, "headers": headers}

        # Missing CL -> 400 straight from the scope, never delegated.
        await asgi_module.application(scope([]), receive, send)
        assert delegated == []
        assert sent[0]["status"] == 400
        assert "Content-Length" in json.loads(sent[-1]["body"])["detail"]

        # Duplicate/conflicting CL headers -> 400, never delegated.
        sent.clear()
        await asgi_module.application(
            scope([(b"content-length", b"10"), (b"content-length", b"20")]),
            receive, send,
        )
        assert delegated == []
        assert sent[0]["status"] == 400

        # Oversized CL -> 400, never delegated.
        sent.clear()
        await asgi_module.application(
            scope([(b"content-length", str(BODY_MAX_BYTES + 1).encode())]),
            receive, send,
        )
        assert delegated == []
        assert sent[0]["status"] == 400

        # Valid CL -> delegated unchanged to Django's ASGI app.
        sent.clear()
        await asgi_module.application(
            scope([(b"content-length", b"1024")]), receive, send
        )
        assert len(delegated) == 1
        assert sent == []

        # Non-audio path is never gated.
        await asgi_module.application(
            scope([], path="/api/v1/conversations/turn/"), receive, send
        )
        assert len(delegated) == 2

    asyncio.run(scenario())
