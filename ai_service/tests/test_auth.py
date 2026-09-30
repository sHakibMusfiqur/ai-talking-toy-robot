import os

import pytest

ENDPOINT = "/v1/conversation/turn"
TOKEN = os.environ["AI_SERVICE_INTERNAL_TOKEN"]
DEVICE_JWT_STYLE = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkZXZpY2UtMDEifQ.c2lnbmF0dXJl"


def _post(client, payload, headers=None):
    return client.post(ENDPOINT, json=payload, headers=headers or {})


def test_missing_token_rejected(client, make_payload):
    r = _post(client, make_payload())
    assert r.status_code == 401
    assert r.json() == {"detail": "Unauthorized."}
    assert r.headers.get("WWW-Authenticate") == "Bearer"


def test_invalid_token_rejected(client, auth_headers, make_payload):
    r = _post(client, make_payload(), {"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Unauthorized."}


@pytest.mark.parametrize(
    "authorization",
    [
        "Token abcdef",
        "Basic dXNlcjpwYXNz",
        "Bearer",
        "Bearer    ",
        "bearer-without-separator",
        "",
    ],
)
def test_malformed_authorization_rejected(client, make_payload, authorization):
    r = _post(client, make_payload(), {"Authorization": authorization})
    assert r.status_code == 401


def test_valid_internal_token_accepted(client, auth_headers, make_payload):
    r = _post(client, make_payload(), auth_headers)
    assert r.status_code == 200


def test_401_body_is_uniform_across_failure_modes(client, make_payload):
    missing = _post(client, make_payload())
    invalid = _post(client, make_payload(), {"Authorization": "Bearer nope"})
    malformed = _post(client, make_payload(), {"Authorization": "Token x"})
    assert missing.json() == invalid.json() == malformed.json()


def test_device_jwt_is_not_accepted_as_ai_service_token(client, make_payload):
    r = _post(client, make_payload(), {"Authorization": f"Bearer {DEVICE_JWT_STYLE}"})
    assert r.status_code == 401


def test_human_session_cookie_is_not_accepted_as_ai_service_auth(client, make_payload):
    r = client.post(
        ENDPOINT,
        json=make_payload(),
        cookies={"sessionid": "session-value", "csrftoken": "csrf-value"},
    )
    assert r.status_code == 401


def test_response_never_contains_the_service_token(client, auth_headers, make_payload):
    r = _post(client, make_payload(), auth_headers)
    assert TOKEN not in r.text
