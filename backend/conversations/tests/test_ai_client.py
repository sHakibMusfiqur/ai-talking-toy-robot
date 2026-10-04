import json
import logging

import httpx
import pytest

from conversations.ai_client import AIServiceError, call_ai_turn
from conversations.ai_contract import (
    AIContractError,
    INPUT_FIELDS,
    OUTPUT_FIELDS,
    validate_ai_input,
    validate_ai_output,
)

BASE_URL = "http://ai-service-test:8000"
TOKEN = "unit-test-internal-token-value"
CONVERSATION_ID = "665f1c2e8b3d4a5f6e7d8c9b"
CHILD_PROFILE_ID = "665f1c2e8b3d4a5f6e7d8c01"
ROBOT_ID = "665f1c2e8b3d4a5f6e7d8c02"

MOCK_REPLY = "Hello! I am happy to talk with you."


def make_payload():
    return {
        "conversation_id": CONVERSATION_ID,
        "child_profile_id": CHILD_PROFILE_ID,
        "robot_id": ROBOT_ID,
        "text": "Hello",
        "audio": None,
        "language_hint": None,
        "mode": "conversation",
        "context": {"recent_turns": [], "memories": []},
    }


def make_output(**overrides):
    data = {
        "conversation_id": CONVERSATION_ID,
        "text": MOCK_REPLY,
        "language": "en",
        "emotion": "HAPPY",
        "action": "WAVE",
        "safety": {"flagged": False, "category": None, "action_taken": "ALLOWED"},
        "audio": None,
        "memory_writes": [],
    }
    data.update(overrides)
    return data


def call(payload=None, handler=None, token=TOKEN, **kwargs):
    return call_ai_turn(
        payload or make_payload(),
        base_url=BASE_URL,
        token=token,
        transport=httpx.MockTransport(
            handler or (lambda request: httpx.Response(200, json=make_output(), request=request))
        ),
        **kwargs,
    )


# --- the outbound call ----------------------------------------------------


def test_posts_frozen_path_with_bearer_token_and_returns_contract():
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(200, json=make_output(), request=request)

    data = call(handler=handler)

    request = seen["request"]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/v1/conversation/turn"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert set(json.loads(request.content)) == set(INPUT_FIELDS)
    assert set(data) == set(OUTPUT_FIELDS)
    assert data["text"] == MOCK_REPLY


def test_missing_token_fails_closed_without_sending_a_request():
    def handler(request):
        raise AssertionError("no request may be sent without a token")

    with pytest.raises(AIServiceError) as exc:
        call(handler=handler, token="")
    assert exc.value.reason == "config"


def test_timeout_becomes_the_timeout_reason():
    def handler(request):
        raise httpx.ConnectTimeout("simulated timeout")

    with pytest.raises(AIServiceError) as exc:
        call(handler=handler)
    assert exc.value.reason == "timeout"


def test_connection_error_becomes_the_unavailable_reason():
    def handler(request):
        raise httpx.ConnectError("simulated refusal")

    with pytest.raises(AIServiceError) as exc:
        call(handler=handler)
    assert exc.value.reason == "unavailable"


def test_non_200_becomes_the_http_error_reason():
    def handler(request):
        return httpx.Response(
            500, json={"detail": "Internal server error."}, request=request
        )

    with pytest.raises(AIServiceError) as exc:
        call(handler=handler)
    assert exc.value.reason == "http_error"
    assert exc.value.status_code == 500


def test_non_json_body_becomes_invalid_response():
    def handler(request):
        return httpx.Response(200, content=b"<html>nope</html>", request=request)

    with pytest.raises(AIServiceError) as exc:
        call(handler=handler)
    assert exc.value.reason == "invalid_response"


def test_contract_violation_becomes_invalid_response_without_echoing_values(
    caplog,
):
    def handler(request):
        return httpx.Response(
            200, json=make_output(emotion="ANGRY"), request=request
        )

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AIServiceError) as exc:
            call(handler=handler)

    assert exc.value.reason == "invalid_response"
    # Neither the offending value, the child input, nor the token may leak
    # into the exception message or the logs.
    assert "ANGRY" not in str(exc.value)
    assert TOKEN not in str(exc.value)
    assert "ANGRY" not in caplog.text
    assert TOKEN not in caplog.text
    assert MOCK_REPLY not in caplog.text


def test_extra_output_field_is_a_contract_violation():
    def handler(request):
        return httpx.Response(
            200, json=make_output(servo_angle=12), request=request
        )

    with pytest.raises(AIServiceError) as exc:
        call(handler=handler)
    assert exc.value.reason == "invalid_response"


def test_foreign_conversation_id_echo_is_a_contract_violation():
    def handler(request):
        return httpx.Response(
            200,
            json=make_output(conversation_id="665f1c2e8b3d4a5f6e7d8cff"),
            request=request,
        )

    with pytest.raises(AIServiceError) as exc:
        call(handler=handler)
    assert exc.value.reason == "invalid_response"


# --- backend-owned contract validators ------------------------------------


def test_valid_input_and_output_pass():
    assert validate_ai_input(make_payload(), conversation_id=CONVERSATION_ID)
    assert validate_ai_output(make_output(), conversation_id=CONVERSATION_ID)


def test_input_rejects_text_and_audio_together():
    payload = make_payload()
    payload["audio"] = "aGVsbG8="
    with pytest.raises(AIContractError):
        validate_ai_input(payload, conversation_id=CONVERSATION_ID)


def test_input_rejects_unknown_and_hardware_fields():
    payload = make_payload()
    payload["servo_angle"] = 90
    with pytest.raises(AIContractError) as exc:
        validate_ai_input(payload, conversation_id=CONVERSATION_ID)
    assert "servo_angle" in str(exc.value)


def test_output_rejects_flagged_safety_without_category():
    with pytest.raises(AIContractError):
        validate_ai_output(
            make_output(
                safety={
                    "flagged": True,
                    "category": None,
                    "action_taken": "BLOCKED",
                }
            ),
            conversation_id=CONVERSATION_ID,
        )


def test_output_rejects_values_outside_the_frozen_enums():
    for field, value in (
        ("language", "fr"),
        ("emotion", "ANGRY"),
        ("action", "SPIN"),
        ("memory_writes", [{"key": "k"}]),
    ):
        with pytest.raises(AIContractError):
            validate_ai_output(make_output(**{field: value}), conversation_id=CONVERSATION_ID)
