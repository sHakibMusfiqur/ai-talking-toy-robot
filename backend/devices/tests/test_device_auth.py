import base64
import logging

import pytest
from analytics.models import AuditLog
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from django.conf import settings
from django.utils import timezone
from devices.models import DeviceChallenge
from datetime import timedelta

from .helpers import REGISTER_URL, new_device_id

pytestmark = pytest.mark.django_db

CHALLENGE_URL = "/api/v1/devices/auth/challenge/"
TOKEN_URL = "/api/v1/devices/auth/token/"
ME_URL = "/api/v1/devices/me/"

UNIFORM_401 = {"detail": "Invalid credentials."}


def make_ec_keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_key, pem


def make_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_key, pem


def sign_der(private_key, message):
    der = private_key.sign(message.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(der).decode("ascii")


def sign_rsa(private_key, message):
    sig = private_key.sign(
        message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
    )
    return base64.b64encode(sig).decode("ascii")


def register(client, device_id, public_key_pem):
    response = client.post(
        REGISTER_URL,
        {"device_id": device_id, "public_key": public_key_pem},
        format="json",
    )
    assert response.status_code == 201, response.content
    return response.json()


def request_challenge(client, device_id):
    return client.post(CHALLENGE_URL, {"device_id": device_id}, format="json")


def exchange(client, device_id, challenge_id, challenge, signature):
    return client.post(
        TOKEN_URL,
        {
            "device_id": device_id,
            "challenge_id": challenge_id,
            "challenge": challenge,
            "signature": signature,
        },
        format="json",
    )


def complete_auth(client, device_id, private_key):
    challenge = request_challenge(client, device_id)
    assert challenge.status_code == 200, challenge.content
    body = challenge.json()
    response = exchange(
        client,
        device_id,
        body["challenge_id"],
        body["challenge"],
        sign_der(private_key, body["challenge"]),
    )
    assert response.status_code == 200, response.content
    return response.json()


def test_challenge_issued_to_registered_device(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)

    response = request_challenge(api_client, device_id)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"challenge_id", "challenge", "expires_in"}
    assert body["expires_in"] == settings.DEVICE_CHALLENGE_TTL_SECONDS
    assert len(body["challenge"]) == 64  # secrets.token_hex(32)

    row = DeviceChallenge.objects.get(pk=body["challenge_id"])
    assert row.challenge_hash != body["challenge"]
    assert len(row.challenge_hash) == 64
    assert row.consumed_at is None


def test_challenge_requires_valid_uuid(api_client):
    response = request_challenge(api_client, "not-a-uuid")
    assert response.status_code == 400
    assert "device_id" in response.json()


def test_challenge_unknown_device_is_uniform_401(api_client):
    response = request_challenge(api_client, new_device_id())
    assert response.status_code == 401
    assert response.json() == UNIFORM_401


def test_unknown_and_known_device_failures_are_indistinguishable(api_client):
    known = new_device_id()
    register(api_client, known, make_ec_keypair()[1])

    unknown = request_challenge(api_client, new_device_id())
    assert unknown.status_code == 401
    assert unknown.json() == UNIFORM_401

    body = request_challenge(api_client, known).json()
    replay = exchange(api_client, known, body["challenge_id"], body["challenge"], "x")
    assert replay.status_code == 401
    assert replay.json() == unknown.json()


def test_new_challenge_invalidates_previous_one(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)

    first = request_challenge(api_client, device_id).json()
    second = request_challenge(api_client, device_id).json()
    assert first["challenge"] != second["challenge"]

    stale = exchange(
        api_client,
        device_id,
        first["challenge_id"],
        first["challenge"],
        sign_der(private_key, first["challenge"]),
    )
    assert stale.status_code == 401
    assert stale.json() == UNIFORM_401

    fresh = exchange(
        api_client,
        device_id,
        second["challenge_id"],
        second["challenge"],
        sign_der(private_key, second["challenge"]),
    )
    assert fresh.status_code == 200
    # Only the latest challenge row remains unconsumed-by-invalidation.
    assert DeviceChallenge.objects.filter(
        pk=first["challenge_id"], consumed_at__isnull=False
    ).count() == 1


def test_token_exchange_success_returns_device_jwt(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    registration = register(api_client, device_id, pem)

    body = complete_auth(api_client, device_id, private_key)
    assert set(body.keys()) == {"access_token", "token_type", "expires_in"}
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == settings.DEVICE_JWT_TTL_SECONDS

    import jwt as pyjwt

    claims = pyjwt.decode(
        body["access_token"],
        settings.DEVICE_JWT_SECRET,
        algorithms=[settings.DEVICE_JWT_ALGORITHM],
        issuer=settings.DEVICE_JWT_ISSUER,
        audience=settings.DEVICE_JWT_AUDIENCE,
    )
    assert claims["sub"] == registration["id"]
    assert claims["iss"] == settings.DEVICE_JWT_ISSUER
    assert claims["aud"] == settings.DEVICE_JWT_AUDIENCE
    assert claims["exp"] - claims["iat"] == settings.DEVICE_JWT_TTL_SECONDS


def test_token_exchange_is_single_use(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)

    challenge = request_challenge(api_client, device_id).json()
    signature = sign_der(private_key, challenge["challenge"])

    first = exchange(
        api_client,
        device_id,
        challenge["challenge_id"],
        challenge["challenge"],
        signature,
    )
    assert first.status_code == 200

    replay = exchange(
        api_client,
        device_id,
        challenge["challenge_id"],
        challenge["challenge"],
        signature,
    )
    assert replay.status_code == 401
    assert replay.json() == UNIFORM_401
    row = DeviceChallenge.objects.get(pk=challenge["challenge_id"])
    assert row.consumed_at is not None


def test_expired_challenge_is_rejected(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)

    challenge = request_challenge(api_client, device_id).json()
    DeviceChallenge.objects.filter(pk=challenge["challenge_id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    response = exchange(
        api_client,
        device_id,
        challenge["challenge_id"],
        challenge["challenge"],
        sign_der(private_key, challenge["challenge"]),
    )
    assert response.status_code == 401
    assert response.json() == UNIFORM_401


def test_signature_from_wrong_key_is_rejected(api_client):
    private_key, pem = make_ec_keypair()
    attacker_key, _ = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)

    challenge = request_challenge(api_client, device_id).json()
    response = exchange(
        api_client,
        device_id,
        challenge["challenge_id"],
        challenge["challenge"],
        sign_der(attacker_key, challenge["challenge"]),
    )
    assert response.status_code == 401
    assert response.json() == UNIFORM_401


def test_cross_device_challenge_binding(api_client):
    private_key_a, pem_a = make_ec_keypair()
    _, pem_b = make_ec_keypair()
    device_a = new_device_id()
    device_b = new_device_id()
    register(api_client, device_a, pem_a)
    register(api_client, device_b, pem_b)

    challenge_a = request_challenge(api_client, device_a).json()
    response = exchange(
        api_client,
        device_b,
        challenge_a["challenge_id"],
        challenge_a["challenge"],
        sign_der(private_key_a, challenge_a["challenge"]),
    )
    assert response.status_code == 401
    assert response.json() == UNIFORM_401


def test_signature_by_a_cannot_authenticate_as_b(api_client):
    private_key_a, pem_a = make_ec_keypair()
    _, pem_b = make_ec_keypair()
    device_a = new_device_id()
    device_b = new_device_id()
    register(api_client, device_a, pem_a)
    register(api_client, device_b, pem_b)

    challenge_b = request_challenge(api_client, device_b).json()
    response = exchange(
        api_client,
        device_b,
        challenge_b["challenge_id"],
        challenge_b["challenge"],
        sign_der(private_key_a, challenge_b["challenge"]),
    )
    assert response.status_code == 401
    assert response.json() == UNIFORM_401


def test_malformed_signatures_return_401_not_500(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)

    for bad_signature in (
        "not-base64!!!",
        base64.b64encode(b"short").decode("ascii"),
        base64.b64encode(b"\x00" * 70).decode("ascii"),
        "",
    ):
        challenge = request_challenge(api_client, device_id).json()
        response = exchange(
            api_client,
            device_id,
            challenge["challenge_id"],
            challenge["challenge"],
            bad_signature,
        )
        if bad_signature == "":
            assert response.status_code == 400, bad_signature
        else:
            assert response.status_code == 401, bad_signature
            assert response.json() == UNIFORM_401


def test_unknown_device_token_exchange_is_uniform_401(api_client):
    response = exchange(
        api_client, new_device_id(), "0" * 24, "challenge", "signature"
    )
    assert response.status_code == 401
    assert response.json() == UNIFORM_401


def test_malformed_challenge_id_is_401_not_500(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)
    challenge = request_challenge(api_client, device_id).json()

    for bad_id in ("not-an-objectid", "", "zzzzzzzzzzzzzzzzzzzzzzzz"):
        response = exchange(
            api_client,
            device_id,
            bad_id,
            challenge["challenge"],
            sign_der(private_key, challenge["challenge"]),
        )
        if bad_id == "":
            assert response.status_code == 400, bad_id
        else:
            assert response.status_code == 401, bad_id
            assert response.json() == UNIFORM_401


def test_token_endpoint_missing_fields_return_400(api_client):
    device_id = new_device_id()
    register(api_client, device_id, make_ec_keypair()[1])

    missing = [
        {"device_id": device_id},
        {"device_id": device_id, "challenge_id": "x"},
        {
            "device_id": device_id,
            "challenge_id": "x",
            "challenge": "y",
        },
    ]
    for payload in missing:
        response = api_client.post(TOKEN_URL, payload, format="json")
        assert response.status_code == 400, payload


def test_raw_challenge_never_persisted_or_logged(api_client, caplog):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)

    with caplog.at_level(logging.DEBUG):
        body = complete_auth(api_client, device_id, private_key)
        raw_challenge = request_challenge(api_client, device_id).json()["challenge"]
        pending = (
            DeviceChallenge.objects.filter(
                robot__device_id=device_id, consumed_at__isnull=True
            )
            .order_by("-created_at")
            .first()
        )
        exchange(
            api_client,
            device_id,
            str(pending.pk),
            raw_challenge,
            sign_der(private_key, raw_challenge),
        )

    assert raw_challenge not in caplog.text
    for record in caplog.records:
        assert raw_challenge not in record.getMessage()
    assert body["access_token"] not in caplog.text

    for row in DeviceChallenge.objects.all():
        assert row.challenge_hash != raw_challenge
    for entry in AuditLog.objects.all():
        for field in (entry.action, entry.target, entry.ip):
            assert raw_challenge not in str(field)


def test_rsa_public_key_is_accepted(api_client):
    private_key, pem = make_rsa_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)

    challenge = request_challenge(api_client, device_id).json()
    response = exchange(
        api_client,
        device_id,
        challenge["challenge_id"],
        challenge["challenge"],
        sign_rsa(private_key, challenge["challenge"]),
    )
    assert response.status_code == 200


def test_raw_rs_signature_is_accepted(api_client):
    """ESP32/mbedTLS emits raw r||s (64 bytes) instead of DER."""
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)

    challenge = request_challenge(api_client, device_id).json()
    der = private_key.sign(
        challenge["challenge"].encode("utf-8"), ec.ECDSA(hashes.SHA256())
    )
    r, s = decode_dss_signature(der)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    response = exchange(
        api_client,
        device_id,
        challenge["challenge_id"],
        challenge["challenge"],
        base64.b64encode(raw_sig).decode("ascii"),
    )
    assert response.status_code == 200


def test_token_response_contains_no_key_or_challenge_material(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)

    challenge_body = request_challenge(api_client, device_id).json()
    token_body = complete_auth_from(api_client, device_id, private_key, challenge_body)

    challenge_text = challenge_body["challenge"]
    for body in (challenge_body, token_body):
        assert "public_key" not in body
        assert "signature" not in body
        assert pem not in str(body)
    assert challenge_text not in token_body["access_token"]
    assert token_body["access_token"] not in str(challenge_body)


def complete_auth_from(client, device_id, private_key, challenge_body):
    response = exchange(
        client,
        device_id,
        challenge_body["challenge_id"],
        challenge_body["challenge"],
        sign_der(private_key, challenge_body["challenge"]),
    )
    assert response.status_code == 200, response.content
    return response.json()


def test_audit_events_for_token_issued_and_auth_failures(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    registration = register(api_client, device_id, pem)

    complete_auth(api_client, device_id, private_key)

    challenge = request_challenge(api_client, device_id).json()
    exchange(
        api_client,
        device_id,
        challenge["challenge_id"],
        challenge["challenge"],
        "bm90LWEtc2lnbmF0dXJl",  # base64("not-a-signature")
    )

    actions = list(AuditLog.objects.values_list("action", flat=True))
    issued = AuditLog.objects.filter(
        action="device.token_issued", target=registration["id"]
    )
    assert issued.count() == 1
    assert "device.auth_failed.signature" in actions


def test_failed_auth_for_unknown_device_writes_no_audit(api_client):
    exchange(api_client, new_device_id(), "0" * 24, "challenge", "signature")
    failed = AuditLog.objects.filter(action__startswith="device.auth_failed")
    assert failed.count() == 0


def test_unpaired_robot_can_authenticate(api_client):
    """Auth proves machine identity only; pairing/ownership is separate."""
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)

    token_body = complete_auth(api_client, device_id, private_key)
    response = api_client.get(
        ME_URL, HTTP_AUTHORIZATION=f"Bearer {token_body['access_token']}"
    )
    assert response.status_code == 200
    assert response.json()["pairing_status"] == "unpaired"
