import pytest

ENDPOINT = "/v1/conversation/turn"


def test_health_endpoint_works_without_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_app_starts_and_exposes_internal_endpoints(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/health" in paths
    assert ENDPOINT in paths


def test_valid_text_turn_accepted(client, auth_headers, make_payload):
    r = client.post(ENDPOINT, json=make_payload(), headers=auth_headers)
    assert r.status_code == 200


def test_output_has_exact_frozen_contract_fields(client, auth_headers, make_payload):
    r = client.post(ENDPOINT, json=make_payload(), headers=auth_headers)
    body = r.json()
    assert set(body) == {
        "conversation_id",
        "text",
        "language",
        "emotion",
        "action",
        "safety",
        "audio",
        "memory_writes",
    }


def test_audio_shaped_turn_accepted_via_mock_stt(client, auth_headers, make_payload):
    # Base64 placeholder audio: accepted, processed by the STT stub only.
    import base64

    audio_b64 = base64.b64encode(b"\x00\x01fake-audio-bytes").decode()
    payload = make_payload()
    payload.pop("text")
    payload["audio"] = audio_b64
    r = client.post(ENDPOINT, json=payload, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["text"] == "Hello! I am happy to talk with you."


@pytest.mark.parametrize(
    "missing",
    ["conversation_id", "child_profile_id", "robot_id", "context"],
)
def test_missing_required_fields_rejected(client, auth_headers, make_payload, missing):
    r = client.post(ENDPOINT, json=make_payload(__delete__=[missing]), headers=auth_headers)
    assert r.status_code == 422


def test_missing_both_text_and_audio_rejected(client, auth_headers, make_payload):
    r = client.post(ENDPOINT, json=make_payload(__delete__=["text"]), headers=auth_headers)
    assert r.status_code == 422


def test_both_text_and_audio_rejected(client, auth_headers, make_payload):
    r = client.post(
        ENDPOINT,
        json=make_payload(audio="AAAAAAAA"),
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_wrong_type_rejected(client, auth_headers, make_payload):
    r = client.post(ENDPOINT, json=make_payload(conversation_id=12345), headers=auth_headers)
    assert r.status_code == 422


def test_blank_text_rejected(client, auth_headers, make_payload):
    r = client.post(ENDPOINT, json=make_payload(text="   "), headers=auth_headers)
    assert r.status_code == 422


def test_invalid_base64_audio_rejected(client, auth_headers, make_payload):
    payload = make_payload()
    payload.pop("text")
    payload["audio"] = "!!!not-base64!!!"
    r = client.post(ENDPOINT, json=payload, headers=auth_headers)
    assert r.status_code == 422


def test_extra_unknown_field_rejected(client, auth_headers, make_payload):
    r = client.post(ENDPOINT, json=make_payload(servo_angle=45), headers=auth_headers)
    assert r.status_code == 422


def test_unknown_language_hint_rejected(client, auth_headers, make_payload):
    r = client.post(ENDPOINT, json=make_payload(language_hint="klingon"), headers=auth_headers)
    assert r.status_code == 422


def test_unknown_mode_rejected(client, auth_headers, make_payload):
    r = client.post(ENDPOINT, json=make_payload(mode="battle-arena"), headers=auth_headers)
    assert r.status_code == 422


def test_nested_context_extra_field_rejected(client, auth_headers, make_payload):
    payload = make_payload()
    payload["context"] = {"recent_turns": [], "memories": [], "db_connection": "mongodb://x"}
    r = client.post(ENDPOINT, json=payload, headers=auth_headers)
    assert r.status_code == 422


# --- payload bounds (Phase A, mirrors Django CONVERSATION_MAX_* settings) ---


def test_oversized_text_rejected(client, auth_headers, make_payload):
    from contract.schemas import MAX_TEXT_LENGTH

    r = client.post(
        ENDPOINT,
        json=make_payload(text="x" * (MAX_TEXT_LENGTH + 1)),
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_oversized_audio_rejected(client, auth_headers, make_payload):
    from contract.schemas import MAX_AUDIO_BASE64_LENGTH

    payload = make_payload()
    payload.pop("text")
    payload["audio"] = "A" * (MAX_AUDIO_BASE64_LENGTH + 1)
    r = client.post(ENDPOINT, json=payload, headers=auth_headers)
    assert r.status_code == 422
