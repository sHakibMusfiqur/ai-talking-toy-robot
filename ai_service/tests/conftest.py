import os

import pytest
from fastapi.testclient import TestClient

from api.main import app

TOKEN = os.environ["AI_SERVICE_INTERNAL_TOKEN"]


@pytest.fixture
def client() -> TestClient:
    # raise_server_exceptions=False so controlled-500 behavior is observable.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_headers() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def make_payload():
    def _make(**overrides) -> dict:
        payload = {
            "conversation_id": "665f1c2e8b3d4a5f6e7d8c9b",
            "child_profile_id": "665f1c2e8b3d4a5f6e7d8c9c",
            "robot_id": "665f1c2e8b3d4a5f6e7d8c9d",
            "text": "Hello",
            "language_hint": "en",
            "context": {
                "recent_turns": [{"role": "user", "text": "Hi robot"}],
                "memories": [{"key": "favorite_animal", "value": "cat"}],
            },
            "mode": "conversation",
        }
        payload.update(overrides)
        for key in payload.pop("__delete__", []):
            payload.pop(key, None)
        return payload

    return _make


@pytest.fixture
def block_network(monkeypatch):
    """Fail the test if the request path opens an *external* connection -
    proves the Phase 9 pipeline performs no DB or provider I/O.

    Loopback stays allowed: asyncio's Windows event loop builds its internal
    self-pipe from two connected loopback sockets.
    """
    import socket

    loopback = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}

    def _host(address):
        return address[0] if isinstance(address, tuple) else address

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo
    original_create_connection = socket.create_connection
    original_gethostbyname = socket.gethostbyname
    original_gethostbyname_ex = socket.gethostbyname_ex

    def deny_connect(self, address, *args, **kwargs):
        if _host(address) in loopback:
            return original_connect(self, address, *args, **kwargs)
        raise AssertionError(f"network access attempted during AI turn: {address!r}")

    def deny_connect_ex(self, address, *args, **kwargs):
        if _host(address) in loopback:
            return original_connect_ex(self, address, *args, **kwargs)
        raise AssertionError(f"network access attempted during AI turn: {address!r}")

    def deny_getaddrinfo(host, *args, **kwargs):
        if host in loopback:
            return original_getaddrinfo(host, *args, **kwargs)
        raise AssertionError(f"DNS/network access attempted during AI turn: {host!r}")

    def deny_create_connection(address, *args, **kwargs):
        if _host(address) in loopback:
            return original_create_connection(address, *args, **kwargs)
        raise AssertionError(f"network access attempted during AI turn: {address!r}")

    def deny_gethostbyname(host, *args, **kwargs):
        if host in loopback:
            return original_gethostbyname(host, *args, **kwargs)
        raise AssertionError(f"DNS/network access attempted during AI turn: {host!r}")

    def deny_gethostbyname_ex(host, *args, **kwargs):
        if host in loopback:
            return original_gethostbyname_ex(host, *args, **kwargs)
        raise AssertionError(f"DNS/network access attempted during AI turn: {host!r}")

    monkeypatch.setattr(socket.socket, "connect", deny_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", deny_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", deny_create_connection)
    monkeypatch.setattr(socket, "gethostbyname", deny_gethostbyname)
    monkeypatch.setattr(socket, "gethostbyname_ex", deny_gethostbyname_ex)
