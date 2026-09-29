import base64

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.utils import timezone
from devices.models import Robot

from .helpers import DEVICE_LIST_URL, new_device_id, register_device, setup_parent
from .test_device_auth import (
    ME_URL,
    UNIFORM_401,
    complete_auth,
    make_ec_keypair,
    register,
    request_challenge,
    sign_der,
    exchange,
)

pytestmark = pytest.mark.django_db

CHILDREN_URL = "/api/v1/children/"
HUMAN_DETAIL_MESSAGE = {"detail": "Authentication credentials were not provided."}


def issue_token(api_client, device_id=None, private_key=None, pem=None):
    if private_key is None:
        private_key, generated_pem = make_ec_keypair()
        pem = generated_pem
    device_id = device_id or new_device_id()
    register(api_client, device_id, pem)
    body = complete_auth(api_client, device_id, private_key)
    return device_id, body["access_token"]


def bearer(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def test_device_me_returns_own_robot(api_client):
    device_id, token = issue_token(api_client)
    response = api_client.get(ME_URL, **bearer(token))
    assert response.status_code == 200
    data = response.json()
    assert data["device_id"] == device_id
    assert set(data.keys()) == {
        "id",
        "device_id",
        "nickname",
        "pairing_status",
        "firmware_version",
        "last_seen",
        "paired_at",
    }
    for secret_key in ("parent", "owner_id", "token_hash", "public_key", "credential"):
        assert secret_key not in data


def test_device_me_requires_bearer_header(api_client):
    register(api_client, new_device_id(), make_ec_keypair()[1])
    response = api_client.get(ME_URL)
    assert response.status_code == 401
    assert response.json() == HUMAN_DETAIL_MESSAGE


def test_malformed_or_invalid_tokens_are_uniform_401(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    registration = register(api_client, device_id, pem)
    token = complete_auth(api_client, device_id, private_key)["access_token"]

    other_id = new_device_id()
    other_claims = pyjwt.decode(
        token,
        settings.DEVICE_JWT_SECRET,
        algorithms=["HS256"],
        issuer=settings.DEVICE_JWT_ISSUER,
        audience=settings.DEVICE_JWT_AUDIENCE,
    )
    other_claims["sub"] = other_id  # attempt to switch context
    forged_same_key = pyjwt.encode(
        other_claims, settings.DEVICE_JWT_SECRET, algorithm="HS256"
    )
    forged_wrong_key = pyjwt.encode(other_claims, "attacker-secret", algorithm="HS256")

    expired_claims = dict(other_claims)
    expired_claims["sub"] = registration["id"]
    expired_claims["iat"] = int(timezone.now().timestamp()) - 7200
    expired_claims["exp"] = int(timezone.now().timestamp()) - 3600
    expired = pyjwt.encode(
        expired_claims, settings.DEVICE_JWT_SECRET, algorithm="HS256"
    )

    wrong_iss_claims = dict(other_claims)
    wrong_iss_claims["sub"] = registration["id"]
    wrong_iss_claims["iss"] = "evil-issuer"
    wrong_iss = pyjwt.encode(
        wrong_iss_claims, settings.DEVICE_JWT_SECRET, algorithm="HS256"
    )

    wrong_aud_claims = dict(other_claims)
    wrong_aud_claims["sub"] = registration["id"]
    wrong_aud_claims["aud"] = "evil-audience"
    wrong_aud = pyjwt.encode(
        wrong_aud_claims, settings.DEVICE_JWT_SECRET, algorithm="HS256"
    )

    del other_claims["aud"]
    missing_claim = pyjwt.encode(
        other_claims, settings.DEVICE_JWT_SECRET, algorithm="HS256"
    )

    for bad_token in (
        "abc",
        "a.b.c",
        base64.urlsafe_b64encode(b"not-a-jwt").decode(),
        forged_same_key,
        forged_wrong_key,
        expired,
        wrong_iss,
        wrong_aud,
        missing_claim,
    ):
        response = api_client.get(ME_URL, **bearer(bad_token))
        assert response.status_code == 401, bad_token
        assert response.json() == UNIFORM_401


def test_token_for_deleted_robot_is_rejected(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)
    token = complete_auth(api_client, device_id, private_key)["access_token"]

    Robot.objects.filter(device_id=device_id).delete()
    response = api_client.get(ME_URL, **bearer(token))
    assert response.status_code == 401
    assert response.json() == UNIFORM_401


def test_client_supplied_ids_cannot_switch_context(api_client):
    device_a, token_a = issue_token(api_client)
    device_b, _ = issue_token(api_client)

    response = api_client.get(
        f"{ME_URL}?robot_id={device_b}&device_id={device_b}",
        **bearer(token_a),
    )
    assert response.status_code == 200
    assert response.json()["device_id"] == device_a

    response = api_client.get(
        ME_URL,
        {"robot_id": device_b, "owner_id": "attacker"},
        **bearer(token_a),
    )
    assert response.status_code == 200
    assert response.json()["device_id"] == device_a


def test_device_token_not_accepted_by_human_endpoints(api_client):
    _, token = issue_token(api_client)
    api_client.logout()  # ensure no session masks the result

    children = api_client.get(CHILDREN_URL, **bearer(token))
    detail = api_client.get("/api/v1/devices/000000000000000000000000/", **bearer(token))
    admin_list = api_client.get(DEVICE_LIST_URL, **bearer(token))
    for response in (children, detail, admin_list):
        assert response.status_code == 401, response.content


def test_human_session_not_accepted_on_device_endpoint(api_client):
    setup_parent(api_client, "a@example.com")
    response = api_client.get(ME_URL)
    assert response.status_code == 401
    assert response.json() == HUMAN_DETAIL_MESSAGE


def test_pairing_flow_still_requires_human_session(api_client):
    """Regression: a device Bearer token never authenticates pairing."""
    _, token = issue_token(api_client)
    registration = register_device(api_client).json()
    response = api_client.post(
        "/api/v1/devices/pair/",
        {"pairing_code": registration["pairing_code"]},
        format="json",
        **bearer(token),
    )
    assert response.status_code == 401


def test_device_jwt_is_not_the_human_auth_mechanism(api_client):
    """No human JWT exists: even a perfectly valid device token cannot act
    as a logged-in parent anywhere."""
    _, token = issue_token(api_client)
    setup_parent(api_client, "a@example.com")
    api_client.logout()

    response = api_client.get(CHILDREN_URL, **bearer(token))
    assert response.status_code == 401
    assert Robot.objects.filter(parent__isnull=False).count() == 0


def test_audit_rows_never_contain_secrets(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)
    token = complete_auth(api_client, device_id, private_key)["access_token"]
    challenge = request_challenge(api_client, device_id).json()
    exchange(
        api_client,
        device_id,
        challenge["challenge_id"],
        challenge["challenge"],
        sign_der(private_key, challenge["challenge"]),
    )

    from analytics.models import AuditLog

    for entry in AuditLog.objects.all():
        blob = f"{entry.action}|{entry.target}|{entry.ip}"
        assert token not in blob
        assert challenge["challenge"] not in blob
        assert pem not in blob
        assert "Bearer" not in blob


def test_me_endpoint_response_hides_credential_material(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    register(api_client, device_id, pem)
    token = complete_auth(api_client, device_id, private_key)["access_token"]

    response = api_client.get(ME_URL, **bearer(token))
    body = response.content.decode()
    assert pem not in body
    assert token not in body
    assert "token_hash" not in body
    assert "challenge" not in body


def test_expired_token_is_rejected_with_uniform_detail(api_client):
    private_key, pem = make_ec_keypair()
    device_id = new_device_id()
    registration = register(api_client, device_id, pem)
    complete_auth(api_client, device_id, private_key)

    now = int(timezone.now().timestamp())
    claims = {
        "sub": registration["id"],
        "iss": settings.DEVICE_JWT_ISSUER,
        "aud": settings.DEVICE_JWT_AUDIENCE,
        "iat": now - 7200,
        "exp": now - 3600,
    }
    token = pyjwt.encode(claims, settings.DEVICE_JWT_SECRET, algorithm="HS256")

    response = api_client.get(ME_URL, **bearer(token))
    assert response.status_code == 401
    assert response.json() == UNIFORM_401
