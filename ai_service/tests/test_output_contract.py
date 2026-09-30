from contract.enums import HARDWARE_KEY_BANNERS, Action, Emotion, SafetyAction

ENDPOINT = "/v1/conversation/turn"


def _turn(client, auth_headers, make_payload, **overrides):
    r = client.post(ENDPOINT, json=make_payload(**overrides), headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_valid_ai_output_contract_returned(client, auth_headers, make_payload):
    body = _turn(client, auth_headers, make_payload)
    assert isinstance(body["conversation_id"], str)
    assert isinstance(body["text"], str) and body["text"]
    assert isinstance(body["safety"], dict)
    assert isinstance(body["memory_writes"], list)


def test_conversation_id_echoed_unchanged(client, auth_headers, make_payload):
    sent_id = "507f1f77bcf86cd799439011"
    body = _turn(client, auth_headers, make_payload, conversation_id=sent_id)
    assert body["conversation_id"] == sent_id


def test_emotion_belongs_to_approved_enum(client, auth_headers, make_payload):
    body = _turn(client, auth_headers, make_payload)
    assert body["emotion"] in {e.value for e in Emotion}


def test_action_belongs_to_approved_enum(client, auth_headers, make_payload):
    body = _turn(client, auth_headers, make_payload)
    assert body["action"] in {a.value for a in Action}


def test_semantic_action_only_no_hardware_keys(client, auth_headers, make_payload):
    body = _turn(client, auth_headers, make_payload)

    def walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                lowered = key.lower()
                for banned in HARDWARE_KEY_BANNERS:
                    assert banned not in lowered, f"hardware key leaked: {key}"
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(body)
    raw = str(body).lower()
    for banned in ("servo", "motor", "gpio", "pwm", "led_pin"):
        assert banned not in raw


def test_safety_object_shape_and_unflagged_default(client, auth_headers, make_payload):
    safety = _turn(client, auth_headers, make_payload)["safety"]
    assert set(safety) == {"flagged", "category", "action_taken"}
    assert safety["flagged"] is False
    assert safety["category"] is None
    assert safety["action_taken"] in {a.value for a in SafetyAction}


def test_memory_writes_is_list_of_key_value(client, auth_headers, make_payload):
    body = _turn(client, auth_headers, make_payload)
    assert isinstance(body["memory_writes"], list)
    for entry in body["memory_writes"]:
        assert set(entry) == {"key", "value"}


def test_deterministic_mock_response(client, auth_headers, make_payload):
    first = _turn(client, auth_headers, make_payload, text="Hello")
    second = _turn(client, auth_headers, make_payload, text="Hello")
    assert first == second


def test_mock_reply_is_fixed_text(client, auth_headers, make_payload):
    body = _turn(client, auth_headers, make_payload, text="anything at all")
    assert body["text"] == "Hello! I am happy to talk with you."
    assert body["emotion"] == "HAPPY"
    assert body["action"] == "WAVE"


def test_language_is_contract_valid(client, auth_headers, make_payload):
    body = _turn(client, auth_headers, make_payload, language_hint="bn")
    assert body["language"] in ("en", "bn")


def test_audio_is_null_placeholder_not_fake_binary(client, auth_headers, make_payload):
    import base64

    payload = make_payload()
    payload.pop("text")
    payload["audio"] = base64.b64encode(b"fake-audio-bytes").decode()
    r = client.post(ENDPOINT, json=payload, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["audio"] is None


def test_context_is_request_provided_and_no_network_needed(
    client, auth_headers, make_payload, block_network
):
    # With all sockets blocked: request-supplied context flows through with
    # no database or network access of any kind (AR-5b).
    payload = make_payload(
        context={
            "recent_turns": [{"role": "user", "text": "tell me a story"}],
            "memories": [{"key": "favorite_animal", "value": "otter"}],
        }
    )
    r = client.post(ENDPOINT, json=payload, headers=auth_headers)
    assert r.status_code == 200


def test_no_external_provider_called_for_text_turn(
    client, auth_headers, make_payload, block_network
):
    r = client.post(ENDPOINT, json=make_payload(), headers=auth_headers)
    assert r.status_code == 200


def test_audio_turn_does_not_trigger_real_stt(client, auth_headers, make_payload, block_network):
    import base64

    payload = make_payload()
    payload.pop("text")
    payload["audio"] = base64.b64encode(b"\x00\x01\x02").decode()
    r = client.post(ENDPOINT, json=payload, headers=auth_headers)
    # Deterministic mock transcript path: no provider socket could open
    # (block_network would raise), and the reply is the fixed mock text.
    assert r.status_code == 200
    assert r.json()["text"] == "Hello! I am happy to talk with you."
