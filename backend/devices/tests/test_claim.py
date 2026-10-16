import base64
import hashlib
import secrets
from datetime import timedelta

import pytest
from analytics.models import AuditLog
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from accounts.models import Parent
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from devices.models import DeviceClaimTicket, Robot

from .helpers import (
    PAIR_URL,
    assert_robot_shape,
    new_device_id,
    register_device,
    setup_parent,
)

pytestmark = pytest.mark.django_db

CLAIM_TICKETS_URL = "/api/v1/devices/claim-tickets/"
CLAIM_URL = "/api/v1/devices/claim/"
CHALLENGE_URL = "/api/v1/devices/auth/challenge/"
TOKEN_URL = "/api/v1/devices/auth/token/"

UNIFORM_CLAIM_400 = {"detail": "This setup link is invalid or has expired."}
UNIFORM_PAID_409 = {"detail": "Robot already paired."}
URLSAFE = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


def _keypair():
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


def _sign_der(private_key, message):
    der = private_key.sign(message.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(der).decode("ascii")


def mint_device_token(client):
    """Full Phase 7 device auth: register + signed challenge -> Bearer JWT."""
    device_id = new_device_id()
    private_key, pem = _keypair()
    registration = register_device(
        client, device_id=device_id, public_key=pem
    ).json()

    challenge = client.post(
        CHALLENGE_URL, {"device_id": device_id}, format="json"
    )
    assert challenge.status_code == 200, challenge.content
    body = challenge.json()
    token = client.post(
        TOKEN_URL,
        {
            "device_id": device_id,
            "challenge_id": body["challenge_id"],
            "challenge": body["challenge"],
            "signature": _sign_der(private_key, body["challenge"]),
        },
        format="json",
    )
    assert token.status_code == 200, token.content
    return registration, token.json()["access_token"]


def issue_ticket(client, token):
    response = client.post(
        CLAIM_TICKETS_URL,
        {},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 201, response.content
    return response.json()["ticket"]


def redeem(client, ticket, **extra):
    return client.post(CLAIM_URL, {"ticket": ticket, **extra}, format="json")


# --- Issuance (device side, device-Bearer only) ----------------------------


def test_claim_ticket_issued_to_device_and_hashed_at_rest(api_client):
    registration, token = mint_device_token(api_client)
    response = api_client.post(
        CLAIM_TICKETS_URL,
        {},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert set(body.keys()) == {"ticket", "expires_in"}
    assert body["expires_in"] == settings.DEVICE_CLAIM_TTL_SECONDS

    ticket = body["ticket"]
    assert 40 <= len(ticket) <= 128
    assert set(ticket) <= URLSAFE

    row = DeviceClaimTicket.objects.get(robot__pk=registration["id"])
    digest = hashlib.sha256(ticket.encode("utf-8")).hexdigest()
    assert row.ticket_hash == digest
    assert row.ticket_hash != ticket
    assert len(row.ticket_hash) == 64
    assert ticket not in str(list(DeviceClaimTicket.objects.values()))
    assert row.consumed_at is None
    assert row.expires_at > timezone.now()
    ttl_delta = (row.expires_at - row.created_at).total_seconds()
    assert ttl_delta <= settings.DEVICE_CLAIM_TTL_SECONDS + 5

    assert AuditLog.objects.filter(
        action="device.claim_ticket_issued", target=str(registration["id"])
    ).count() == 1


def test_claim_ticket_response_carries_no_ownership_or_robot_data(api_client):
    registration, token = mint_device_token(api_client)
    response = api_client.post(
        CLAIM_TICKETS_URL,
        {},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 201
    body = response.json()
    serialized = str(body)
    assert registration["id"] not in serialized
    assert registration["device_id"] not in serialized
    assert "parent" not in body
    assert "owner" not in body


def test_claim_ticket_requires_device_authentication(api_client):
    registration, token = mint_device_token(api_client)

    anonymous = api_client.post(CLAIM_TICKETS_URL, {}, format="json")
    assert anonymous.status_code == 401

    forged = api_client.post(
        CLAIM_TICKETS_URL,
        {},
        format="json",
        HTTP_AUTHORIZATION="Bearer not-a-real-token",
    )
    assert forged.status_code == 401
    assert forged.json() == {"detail": "Invalid credentials."}

    # A signed-in human session is not device authentication either.
    setup_parent(api_client, "human@example.com")
    human = api_client.post(CLAIM_TICKETS_URL, {}, format="json")
    assert human.status_code == 401
    assert DeviceClaimTicket.objects.count() == 0


def test_reissuing_claim_ticket_kills_the_previous_link(api_client):
    registration, token = mint_device_token(api_client)
    first = issue_ticket(api_client, token)
    second = issue_ticket(api_client, token)

    assert first != second
    assert DeviceClaimTicket.objects.filter(robot__pk=registration["id"]).count() == 1

    setup_parent(api_client, "reissue@example.com")
    assert redeem(api_client, first).status_code == 400
    assert redeem(api_client, second).status_code == 200


def test_paired_robot_cannot_mint_claim_ticket(api_client):
    registration, token = mint_device_token(api_client)
    setup_parent(api_client, "paired@example.com")
    paired = api_client.post(
        PAIR_URL,
        {"pairing_code": registration["pairing_code"]},
        format="json",
    )
    assert paired.status_code == 200, paired.content

    response = api_client.post(
        CLAIM_TICKETS_URL,
        {},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 409
    assert response.json() == UNIFORM_PAID_409
    assert DeviceClaimTicket.objects.count() == 0


def test_ticket_mint_body_cannot_select_another_robot(api_client):
    robot_a, token_a = mint_device_token(api_client)
    robot_b, _ = mint_device_token(api_client)

    response = api_client.post(
        CLAIM_TICKETS_URL,
        {"robot_id": robot_b["id"], "id": robot_b["id"]},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token_a}",
    )
    assert response.status_code == 201
    row = DeviceClaimTicket.objects.get()
    assert str(row.robot_id) == robot_a["id"]

    # ...and redeeming that ticket pairs robot A, never robot B.
    setup_parent(api_client, "forged-body@example.com")
    assert redeem(api_client, response.json()["ticket"]).status_code == 200
    assert (
        Robot.objects.get(pk=robot_a["id"]).pairing_status
        == Robot.PAIRING_PAIRED
    )
    assert (
        Robot.objects.get(pk=robot_b["id"]).pairing_status
        == Robot.PAIRING_UNPAIRED
    )


# --- Redemption (session + CSRF side) --------------------------------------


def test_claim_redeem_pairs_robot_to_session_parent(api_client):
    registration, token = mint_device_token(api_client)
    ticket = issue_ticket(api_client, token)
    parent = setup_parent(api_client, "claimant@example.com")

    response = redeem(api_client, ticket)
    assert response.status_code == 200, response.content
    data = response.json()
    assert_robot_shape(data)
    assert data["id"] == registration["id"]
    assert data["pairing_status"] == "paired"

    robot = Robot.objects.get(pk=registration["id"])
    assert robot.parent.pk == parent.pk
    assert robot.parent.user.email == "claimant@example.com"
    assert robot.paired_at is not None
    assert robot.pairing_code_hash == ""
    assert robot.pairing_code_expires_at is None

    row = DeviceClaimTicket.objects.get(robot=robot)
    assert row.consumed_at is not None

    entry = AuditLog.objects.get(action="robot.claimed")
    assert entry.target == str(robot.pk)
    assert entry.actor.email == "claimant@example.com"


def test_claim_nickname_is_optional_and_trimmed(api_client):
    registration, token = mint_device_token(api_client)
    ticket = issue_ticket(api_client, token)
    setup_parent(api_client, "nick@example.com")

    response = redeem(api_client, ticket, nickname="  Kitchen buddy  ")
    assert response.status_code == 200, response.content
    robot = Robot.objects.get(pk=registration["id"])
    assert robot.nickname == "Kitchen buddy"

    second_registration, second_token = mint_device_token(api_client)
    second_ticket = issue_ticket(api_client, second_token)
    plain = redeem(api_client, second_ticket, nickname="")
    assert plain.status_code == 200
    second_robot = Robot.objects.get(pk=second_registration["id"])
    assert second_robot.nickname == ""


def test_claim_replay_is_uniform_400(api_client):
    registration, token = mint_device_token(api_client)
    ticket = issue_ticket(api_client, token)
    setup_parent(api_client, "replay@example.com")

    first = redeem(api_client, ticket)
    assert first.status_code == 200

    replay = redeem(api_client, ticket)
    assert replay.status_code == 400
    assert replay.json() == UNIFORM_CLAIM_400

    robot = Robot.objects.get(pk=registration["id"])
    assert robot.pairing_status == Robot.PAIRING_PAIRED
    assert robot.parent.user.email == "replay@example.com"


def test_unknown_expired_and_consumed_faults_are_indistinguishable(api_client):
    expired_reg, expired_token = mint_device_token(api_client)
    expired_ticket = issue_ticket(api_client, expired_token)
    DeviceClaimTicket.objects.filter(robot__pk=expired_reg["id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    consumed_reg, consumed_token = mint_device_token(api_client)
    consumed_ticket = issue_ticket(api_client, consumed_token)

    setup_parent(api_client, "faults@example.com")
    assert redeem(api_client, consumed_ticket).status_code == 200

    consumed = redeem(api_client, consumed_ticket)
    expired = redeem(api_client, expired_ticket)
    unknown = redeem(api_client, secrets.token_urlsafe(32))
    malformed = redeem(api_client, "A" * 64 + "!" * 8)

    for response in (consumed, expired, unknown, malformed):
        assert response.status_code == 400
        assert response.json() == UNIFORM_CLAIM_400

    assert Robot.objects.filter(
        parent__user__email="faults@example.com"
    ).count() == 1


def test_ticket_cannot_rehome_robot_paired_meanwhile(api_client):
    registration, token = mint_device_token(api_client)
    ticket = issue_ticket(api_client, token)

    setup_parent(api_client, "first@example.com")
    paired = api_client.post(
        PAIR_URL,
        {"pairing_code": registration["pairing_code"]},
        format="json",
    )
    assert paired.status_code == 200

    other = APIClient()
    setup_parent(other, "second@example.com")
    response = redeem(other, ticket)
    assert response.status_code == 400
    assert response.json() == UNIFORM_CLAIM_400

    robot = Robot.objects.get(pk=registration["id"])
    assert robot.parent.user.email == "first@example.com"
    assert not AuditLog.objects.filter(
        action="robot.claimed", actor__email="second@example.com"
    ).exists()


def test_ownership_comes_from_session_never_from_request_body(api_client):
    registration, token = mint_device_token(api_client)
    ticket = issue_ticket(api_client, token)
    parent = setup_parent(api_client, "real-owner@example.com")

    stranger = APIClient()
    setup_parent(stranger, "stranger@example.com")
    stranger_parent = Parent.objects.get(user__email="stranger@example.com")

    response = redeem(
        api_client,
        ticket,
        parent_id=str(stranger_parent.pk),
        owner_id=str(stranger_parent.pk),
        user_id=str(stranger_parent.user_id),
    )
    assert response.status_code == 200, response.content

    robot = Robot.objects.get(pk=registration["id"])
    assert robot.parent.pk == parent.pk
    assert robot.parent.user.email == "real-owner@example.com"


def test_claim_requires_login_and_post_only(api_client):
    anonymous = redeem(api_client, "A" * 43)
    assert anonymous.status_code == 401

    setup_parent(api_client, "methods@example.com")
    assert api_client.get(CLAIM_URL).status_code == 405
    assert api_client.get(CLAIM_TICKETS_URL).status_code == 401


def test_claim_from_browser_requires_csrf_token():
    client = APIClient(enforce_csrf_checks=True)
    client.get("/login/")
    setup_parent(client, "claim-csrf@example.com")
    client.credentials(HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)

    device_client = APIClient()
    _, token = mint_device_token(device_client)
    ticket = issue_ticket(device_client, token)

    # Token removed: same session, but CSRF is enforced (403, not 401).
    client.credentials()
    denied = redeem(client, ticket)
    assert denied.status_code == 403

    # The browser always sends the current cookie value with X-CSRFToken.
    client.credentials(HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
    allowed = redeem(client, ticket)
    assert allowed.status_code == 200, allowed.content


@override_settings(
    DEVICE_CLAIM_RATE_LIMIT_ATTEMPTS=3,
    DEVICE_CLAIM_RATE_LIMIT_WINDOW_SECONDS=60,
)
def test_claim_rate_limit_blocks_ticket_guessing(api_client):
    registration, token = mint_device_token(api_client)
    valid_ticket = issue_ticket(api_client, token)
    setup_parent(api_client, "guesser@example.com")

    for guess in range(3):
        response = redeem(api_client, secrets.token_urlsafe(32))
        assert response.status_code == 400, guess
        assert response.json() == UNIFORM_CLAIM_400

    blocked = redeem(api_client, valid_ticket)
    assert blocked.status_code == 429
    assert blocked.json() == {
        "detail": "Too many claim attempts. Please wait and try again."
    }

    # The throttle runs before ticket lookup: nothing was consumed or paired.
    robot = Robot.objects.get(pk=registration["id"])
    assert robot.pairing_status == Robot.PAIRING_UNPAIRED
    row = DeviceClaimTicket.objects.get(robot=robot)
    assert row.consumed_at is None

    # Another user's attempts are unaffected (per-user buckets).
    other = APIClient()
    setup_parent(other, "neighbour@example.com")
    assert redeem(other, secrets.token_urlsafe(32)).status_code == 400


@override_settings(DEVICE_CLAIM_RATE_LIMIT_ATTEMPTS=0)
def test_claim_rate_limit_can_be_disabled_by_configuration(api_client):
    setup_parent(api_client, "unthrottled@example.com")
    for _ in range(11):
        response = redeem(api_client, secrets.token_urlsafe(32))
        assert response.status_code == 400
