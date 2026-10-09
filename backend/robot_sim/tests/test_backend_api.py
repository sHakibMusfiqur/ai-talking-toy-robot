"""Unit tests for the backend REST client (fake transport, no sockets)."""

import base64
import logging

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    load_pem_public_key,
)

from robot_sim import backend_api
from robot_sim.backend_api import (
    BackendApi,
    BackendError,
    HttpxTransport,
    token_refresh_due,
)
from robot_sim.identity import load_or_create_identity


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, path, data):
        self.calls.append((path, data))
        if not self.responses:
            raise AssertionError(f"unexpected transport call: {path}")
        return self.responses.pop(0)


class DeadTransport:
    def post(self, path, data):
        raise BackendError("backend unreachable (ConnectError)")


@pytest.fixture
def ident(tmp_path):
    return load_or_create_identity(tmp_path)


def _api(ident, transport):
    return BackendApi(transport, ident)


def test_register_201_returns_pairing_code_and_sends_contract_payload(ident):
    transport = FakeTransport((201, {"pairing_code": "ABCDE-12345"}))
    code = _api(ident, transport).register()
    assert code == "ABCDE-12345"
    path, payload = transport.calls[0]
    assert path == backend_api.REGISTER_PATH
    assert payload["device_id"] == ident.device_id
    assert payload["firmware_version"] == backend_api.DEFAULT_FIRMWARE_VERSION
    assert payload["public_key"].startswith("-----BEGIN PUBLIC KEY-----")


def test_register_409_means_already_registered(ident):
    transport = FakeTransport((409, {"detail": "already registered"}))
    assert _api(ident, transport).register() is None
    assert len(transport.calls) == 1


@pytest.mark.parametrize("status", [400, 401, 500, 503])
def test_register_failures_raise_without_echoing_body(ident, status):
    transport = FakeTransport((status, {"detail": "sensitive server text"}))
    with pytest.raises(BackendError) as excinfo:
        _api(ident, transport).register()
    message = str(excinfo.value)
    assert f"HTTP {status}" in message
    assert "sensitive server text" not in message


def test_register_201_without_pairing_code_is_malformed(ident):
    transport = FakeTransport((201, {}))
    with pytest.raises(BackendError, match="pairing code"):
        _api(ident, transport).register()


def test_fetch_token_full_challenge_flow_with_valid_signature(ident):
    challenge = "c" * 64
    transport = FakeTransport(
        (
            200,
            {"challenge_id": "42", "challenge": challenge, "expires_in": 60},
        ),
        (
            200,
            {
                "access_token": "jwt-token-value",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        ),
    )
    token, expires_in = _api(ident, transport).fetch_token()
    assert token == "jwt-token-value"
    assert expires_in == 3600

    challenge_path, challenge_payload = transport.calls[0]
    assert challenge_path == backend_api.CHALLENGE_PATH
    assert challenge_payload == {"device_id": ident.device_id}

    token_path, token_payload = transport.calls[1]
    assert token_path == backend_api.TOKEN_PATH
    assert token_payload["device_id"] == ident.device_id
    assert token_payload["challenge_id"] == "42"
    assert token_payload["challenge"] == challenge

    # The signature must verify server-side against the registered key.
    der = base64.b64decode(token_payload["signature"])
    public = load_pem_public_key(ident.public_key_pem.encode("ascii"))
    public.verify(der, challenge.encode("utf-8"), ec.ECDSA(hashes.SHA256()))


@pytest.mark.parametrize("status", [401, 403, 500])
def test_token_endpoint_failures_raise_status_only(ident, status):
    transport = FakeTransport(
        (200, {"challenge_id": "1", "challenge": "x" * 64, "expires_in": 60}),
        (status, {"detail": "Invalid credentials."}),
    )
    with pytest.raises(BackendError) as excinfo:
        _api(ident, transport).fetch_token()
    assert f"HTTP {status}" in str(excinfo.value)
    assert "Invalid credentials" not in str(excinfo.value)


@pytest.mark.parametrize("status", [400, 404, 503])
def test_challenge_endpoint_failures_raise_status_only(ident, status):
    transport = FakeTransport((status, {"detail": "server text"}))
    with pytest.raises(BackendError) as excinfo:
        _api(ident, transport).fetch_token()
    assert f"HTTP {status}" in str(excinfo.value)
    assert "server text" not in str(excinfo.value)


def test_malformed_challenge_response_rejected(ident):
    transport = FakeTransport((200, {"challenge_id": "1"}))
    with pytest.raises(BackendError, match="malformed"):
        _api(ident, transport).fetch_token()


def test_malformed_token_response_rejected(ident):
    transport = FakeTransport(
        (200, {"challenge_id": "1", "challenge": "x" * 64, "expires_in": 60}),
        (200, {"token_type": "Bearer"}),
    )
    with pytest.raises(BackendError, match="malformed"):
        _api(ident, transport).fetch_token()


def test_transport_error_propagates_as_backend_error(ident):
    with pytest.raises(BackendError, match="unreachable"):
        _api(ident, DeadTransport()).register()


def test_no_secrets_are_logged(ident, caplog):
    caplog.set_level(logging.DEBUG)
    transport = FakeTransport(
        (201, {"pairing_code": "TOPSECRET-CODE-9999"}),
        (
            200,
            {"challenge_id": "7", "challenge": "d" * 64, "expires_in": 60},
        ),
        (
            200,
            {"access_token": "super-secret-jwt", "expires_in": 3600},
        ),
    )
    api = _api(ident, transport)
    code = api.register()
    token, _expires = api.fetch_token()
    assert code == "TOPSECRET-CODE-9999"
    assert token == "super-secret-jwt"
    assert "TOPSECRET-CODE-9999" not in caplog.text
    assert "super-secret-jwt" not in caplog.text
    assert "PRIVATE KEY" not in caplog.text


def test_token_refresh_due_mirrors_firmware_rule():
    # expires_in 3600 -> effective 3300 (300 s margin)
    assert token_refresh_due(issued_monotonic=0, expires_in=3600, now_monotonic=3299) is False
    assert token_refresh_due(issued_monotonic=0, expires_in=3600, now_monotonic=3300) is True
    # short lifetimes floor at 60 s
    assert token_refresh_due(issued_monotonic=0, expires_in=100, now_monotonic=59) is False
    assert token_refresh_due(issued_monotonic=0, expires_in=100, now_monotonic=60) is True
    # absurd lifetimes never refresh "immediately"
    assert token_refresh_due(issued_monotonic=0, expires_in=0, now_monotonic=59) is False


def test_httpx_transport_wraps_connection_errors(monkeypatch):
    import httpx

    def _boom(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _boom)
    transport = HttpxTransport("http://127.0.0.1:9")
    with pytest.raises(BackendError, match="unreachable"):
        transport.post("/api/v1/devices/register/", {"device_id": "x"})


def test_django_test_transport_returns_status_and_body(api_client):
    transport = backend_api.DjangoTestTransport(api_client)
    status, body = transport.post("/health/", {})  # require_GET -> 405
    assert status == 405
    assert body == {}  # non-JSON body degrades to an empty dict
