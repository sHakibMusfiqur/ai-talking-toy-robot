import uuid

import pytest
from devices.tests.test_device_auth import (
    complete_auth,
    make_ec_keypair,
    register,
)
from mqtt_bridge.provisioning import (
    ensure_device_client,
    ensure_bridge_client,
)

from .conftest import _deprovision
from .helpers import ConnectionRefused, MqttTestClient


def _refused(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ConnectionRefused as exc:
        return exc
    raise AssertionError("expected connection to be refused")


@pytest.mark.django_db
def test_ensure_device_client_creates_scoped_credentials(broker):
    device_id = str(uuid.uuid4())
    try:
        ensure_device_client(device_id, "first-token-value")
        client = MqttTestClient(username=device_id, password="first-token-value")
        client.connect()
        client.disconnect()

        wrong = MqttTestClient(username=device_id, password="not-the-token")
        exc = _refused(wrong.connect)
        assert "authoriz" in exc.reason.lower() or "not authorised" in exc.reason.lower()
    finally:
        _deprovision(device_id)


@pytest.mark.django_db
def test_reprovision_rotates_password_idempotently(broker):
    device_id = str(uuid.uuid4())
    try:
        ensure_device_client(device_id, "token-one")
        # Re-provision (idempotent, as happens on every token exchange):
        ensure_device_client(device_id, "token-two")

        old = MqttTestClient(username=device_id, password="token-one")
        _refused(old.connect)  # old JWT no longer valid at the broker

        new = MqttTestClient(username=device_id, password="token-two")
        new.connect()
        new.disconnect()
    finally:
        _deprovision(device_id)


@pytest.mark.django_db
def test_token_exchange_provisions_jwt_as_mqtt_password(broker, api_client):
    """End-to-end Section 26 proof: the JWT returned by Phase 7's exchange
    endpoint is immediately usable as the device's MQTT password."""
    private_key, pem = make_ec_keypair()
    device_id = str(uuid.uuid4())
    try:
        register(api_client, device_id, pem)
        token = complete_auth(api_client, device_id, private_key)["access_token"]
        client = MqttTestClient(username=device_id, password=token)
        client.connect()
        client.disconnect()
    finally:
        _deprovision(device_id)


@pytest.mark.django_db
def test_token_exchange_survives_broker_outage(api_client, monkeypatch):
    """Provisioning is best-effort: a broker outage must never fail (or
    change) the token-exchange response."""
    import mqtt_bridge.provisioning as provisioning
    from mqtt_bridge.client import MqttError

    def _down(device_id, token):
        raise MqttError("broker unreachable")

    monkeypatch.setattr(provisioning, "ensure_device_client", _down)
    private_key, pem = make_ec_keypair()
    device_id = str(uuid.uuid4())
    register(api_client, device_id, pem)
    token = complete_auth(api_client, device_id, private_key)["access_token"]
    assert token


@pytest.mark.django_db
def test_unknown_device_exchange_never_provisions(monkeypatch, api_client):
    calls = []
    import mqtt_bridge.provisioning as provisioning

    monkeypatch.setattr(
        provisioning,
        "ensure_device_client",
        lambda device_id, token: calls.append(device_id),
    )
    from devices.tests.test_device_auth import exchange

    response = exchange(
        api_client,
        str(uuid.uuid4()),
        "0" * 24,
        "challenge",
        "signature",
    )
    assert response.status_code == 401
    assert calls == []


@pytest.mark.django_db
def test_ensure_bridge_client_idempotent(broker):
    ensure_bridge_client()
    ensure_bridge_client()  # second run must not raise
