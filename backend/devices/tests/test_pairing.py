from datetime import timedelta

import pytest
from analytics.models import AuditLog
from django.utils import timezone
from devices.models import Robot

from .helpers import (
    PAIR_URL,
    assert_robot_shape,
    new_device_id,
    register_device,
    robot_detail_url,
    setup_parent,
)

pytestmark = pytest.mark.django_db


def pair(client, code, **extra):
    return client.post(PAIR_URL, {"pairing_code": code, **extra}, format="json")


def register_and_pair(client, email, **pair_extra):
    setup_parent(client, email)
    registration = register_device(client).json()
    response = pair(client, registration["pairing_code"], **pair_extra)
    return registration, response


def test_pair_success_with_correct_code(api_client):
    registration, response = register_and_pair(api_client, "a@example.com")
    assert response.status_code == 200
    data = response.json()
    assert_robot_shape(data)
    assert data["id"] == registration["id"]
    assert data["pairing_status"] == "paired"
    assert data["device_id"] == registration["device_id"]

    robot = Robot.objects.get(pk=data["id"])
    assert robot.pairing_status == Robot.PAIRING_PAIRED
    assert robot.parent is not None
    assert robot.parent.user.email == "a@example.com"
    assert robot.paired_at is not None


def test_pair_consumes_code_single_use(api_client):
    registration, response = register_and_pair(api_client, "a@example.com")
    assert response.status_code == 200

    robot = Robot.objects.get(pk=registration["id"])
    assert robot.pairing_code_hash == ""
    assert robot.pairing_code_expires_at is None

    replay = pair(api_client, registration["pairing_code"])
    assert replay.status_code == 400
    assert replay.json() == {"detail": "Invalid pairing code."}
    robot.refresh_from_db()
    assert robot.parent.user.email == "a@example.com"
    assert robot.pairing_status == Robot.PAIRING_PAIRED


def test_pair_with_incorrect_code_returns_400(api_client):
    setup_parent(api_client, "a@example.com")
    register_device(api_client)
    response = pair(api_client, "ZZZZZZZZ")
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid pairing code."}
    assert Robot.objects.filter(parent__isnull=False).count() == 0


def test_pair_with_expired_code_returns_400(api_client):
    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    Robot.objects.filter(pk=registration["id"]).update(
        pairing_code_expires_at=timezone.now() - timedelta(seconds=1)
    )
    response = pair(api_client, registration["pairing_code"])
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid pairing code."}
    assert Robot.objects.filter(parent__isnull=False).count() == 0


def test_invalid_expired_and_used_codes_are_indistinguishable(api_client):
    setup_parent(api_client, "a@example.com")

    expired = register_device(api_client).json()
    Robot.objects.filter(pk=expired["id"]).update(
        pairing_code_expires_at=timezone.now() - timedelta(seconds=1)
    )
    expired_response = pair(api_client, expired["pairing_code"])

    used = register_device(api_client).json()
    assert pair(api_client, used["pairing_code"]).status_code == 200
    used_response = pair(api_client, used["pairing_code"])

    invalid_response = pair(api_client, "QQQQQQQQ")

    assert expired_response.status_code == 400
    assert used_response.status_code == 400
    assert invalid_response.status_code == 400
    assert expired_response.json() == used_response.json() == invalid_response.json()


def test_pairing_robot_a_does_not_grant_access_to_robot_b(api_client):
    setup_parent(api_client, "a@example.com")
    robot_a = register_device(api_client).json()
    robot_b = register_device(api_client).json()

    response = pair(api_client, robot_a["pairing_code"])
    assert response.status_code == 200
    assert response.json()["id"] == robot_a["id"]

    detail_a = api_client.get(f"/api/v1/devices/{robot_a['id']}/")
    detail_b = api_client.get(f"/api/v1/devices/{robot_b['id']}/")
    assert detail_a.status_code == 200
    assert detail_b.status_code == 404

    robot_b_row = Robot.objects.get(pk=robot_b["id"])
    assert robot_b_row.pairing_status == Robot.PAIRING_UNPAIRED
    assert robot_b_row.parent is None


def test_same_account_can_pair_robot_b_separately(api_client):
    setup_parent(api_client, "a@example.com")
    robot_a = register_device(api_client).json()
    robot_b = register_device(api_client).json()

    first = pair(api_client, robot_a["pairing_code"])
    second = pair(api_client, robot_b["pairing_code"])
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == robot_a["id"]
    assert second.json()["id"] == robot_b["id"]

    assert Robot.objects.filter(
        parent__user__email="a@example.com", pairing_status=Robot.PAIRING_PAIRED
    ).count() == 2
    assert (
        api_client.get(f"/api/v1/devices/{robot_a['id']}/").status_code == 200
    )
    assert (
        api_client.get(f"/api/v1/devices/{robot_b['id']}/").status_code == 200
    )


def test_each_robot_requires_its_own_pairing_operation(api_client):
    setup_parent(api_client, "a@example.com")
    robot_a = register_device(api_client).json()
    robot_b = register_device(api_client).json()

    pair(api_client, robot_a["pairing_code"])
    # Robot B has never gone through its own pairing step.
    assert Robot.objects.get(pk=robot_b["id"]).parent is None
    assert (
        api_client.get(f"/api/v1/devices/{robot_b['id']}/").status_code == 404
    )


def test_pairing_can_set_nickname(api_client):
    registration, response = register_and_pair(
        api_client, "a@example.com", nickname="  Ayaan's Robo  "
    )
    assert response.status_code == 200
    assert response.json()["nickname"] == "Ayaan's Robo"
    assert Robot.objects.get(pk=registration["id"]).nickname == "Ayaan's Robo"


def test_client_supplied_parent_id_is_ignored(api_client):
    from accounts.models import Parent, User
    from accounts.services import create_parent_account

    create_parent_account("victim@example.com", "S3cure!Passw0rd")
    victim = Parent.objects.get(user__email="victim@example.com")

    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    response = api_client.post(
        PAIR_URL,
        {"pairing_code": registration["pairing_code"], "parent_id": str(victim.pk)},
        format="json",
    )
    assert response.status_code == 200
    robot = Robot.objects.get(pk=registration["id"])
    assert robot.parent.user.email == "a@example.com"
    assert Robot.objects.filter(parent=victim).count() == 0
    assert victim.robots.count() == 0


def test_client_cannot_override_ownership_with_owner_id(api_client):
    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    response = api_client.post(
        PAIR_URL,
        {
            "pairing_code": registration["pairing_code"],
            "owner_id": "000000000000000000000000",
        },
        format="json",
    )
    assert response.status_code == 200
    robot = Robot.objects.get(pk=registration["id"])
    assert robot.parent.user.email == "a@example.com"


def test_cross_account_robot_access_returns_404(api_client):
    from rest_framework.test import APIClient

    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    pair(api_client, registration["pairing_code"])

    other = APIClient()
    setup_parent(other, "b@example.com")

    detail = other.get(f"/api/v1/devices/{registration['id']}/")
    assert detail.status_code == 404
    assert detail.json() == {"detail": "Not found."}


def test_other_account_cannot_pair_already_paired_robot(api_client):
    from rest_framework.test import APIClient

    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    pair(api_client, registration["pairing_code"])

    other = APIClient()
    setup_parent(other, "b@example.com")
    response = other.post(
        PAIR_URL, {"pairing_code": registration["pairing_code"]}, format="json"
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid pairing code."}
    robot = Robot.objects.get(pk=registration["id"])
    assert robot.parent.user.email == "a@example.com"


def test_unauthenticated_pair_returns_401(api_client):
    register_device(api_client)
    response = api_client.post(
        PAIR_URL, {"pairing_code": "ZZZZZZZZ"}, format="json"
    )
    assert response.status_code == 401
    assert response.json() == {
        "detail": "Authentication credentials were not provided."
    }


def test_pair_without_trailing_slash_also_works(api_client):
    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    response = api_client.post(
        "/api/v1/devices/pair",
        {"pairing_code": registration["pairing_code"]},
        format="json",
    )
    assert response.status_code == 200


def test_pair_blank_code_returns_400(api_client):
    setup_parent(api_client, "a@example.com")
    response = api_client.post(PAIR_URL, {"pairing_code": ""}, format="json")
    assert response.status_code == 400
    assert "pairing_code" in response.json()


def test_pair_missing_code_returns_400(api_client):
    setup_parent(api_client, "a@example.com")
    response = api_client.post(PAIR_URL, {}, format="json")
    assert response.status_code == 400
    assert "pairing_code" in response.json()


def test_pair_records_audit_log_without_raw_code(api_client):
    registration, response = register_and_pair(api_client, "a@example.com")
    assert response.status_code == 200
    entry = AuditLog.objects.get(action="robot.paired")
    assert entry.target == registration["id"]
    assert entry.actor is not None
    assert entry.actor.email == "a@example.com"
    assert registration["pairing_code"] not in entry.target
    assert registration["pairing_code"] not in entry.action
    assert not AuditLog.objects.filter(
        target__contains=registration["pairing_code"]
    ).exists()
