import logging

import pytest
from analytics.models import AuditLog
from devices.models import DeviceCredential, Robot
from rest_framework.test import APIClient

from .helpers import (
    DEVICE_LIST_URL,
    PAIR_URL,
    PUBLIC_KEY,
    REGISTER_URL,
    new_device_id,
    register_device,
    robot_detail_url,
    setup_admin,
    setup_parent,
)

pytestmark = pytest.mark.django_db


def test_robot_detail_hides_credential_and_pairing_material(api_client):
    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    pair = api_client.post(
        PAIR_URL, {"pairing_code": registration["pairing_code"]}, format="json"
    )
    assert pair.status_code == 200

    detail = api_client.get(f"/api/v1/devices/{registration['id']}/")
    assert detail.status_code == 200
    data = detail.json()
    for secret_key in (
        "pairing_code",
        "pairing_code_hash",
        "token_hash",
        "public_key",
        "credential",
        "parent",
        "owner_id",
    ):
        assert secret_key not in data
    body = detail.content.decode()
    assert registration["pairing_code"] not in body
    assert PUBLIC_KEY not in body


def test_pair_response_does_not_leak_secrets(api_client):
    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    response = api_client.post(
        PAIR_URL, {"pairing_code": registration["pairing_code"]}, format="json"
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert registration["pairing_code"] not in body
    assert "pairing_code_hash" not in response.json()
    assert PUBLIC_KEY not in body


def test_admin_list_does_not_leak_secrets(api_client):
    setup_admin(api_client)
    registration = register_device(api_client).json()
    listing = api_client.get(DEVICE_LIST_URL)
    assert listing.status_code == 200
    body = listing.content.decode()
    assert registration["pairing_code"] not in body
    assert "pairing_code_hash" not in listing.content.decode()
    assert PUBLIC_KEY not in body


def test_raw_pairing_code_never_written_to_logs(api_client, caplog):
    setup_parent(api_client, "a@example.com")
    with caplog.at_level(logging.DEBUG):
        registration = register_device(api_client).json()
        api_client.post(
            PAIR_URL,
            {"pairing_code": registration["pairing_code"]},
            format="json",
        )
    code = registration["pairing_code"]
    assert code not in caplog.text
    for record in caplog.records:
        assert code not in record.getMessage()


def test_raw_pairing_code_not_stored_anywhere(api_client):
    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    code = registration["pairing_code"]
    api_client.post(PAIR_URL, {"pairing_code": code}, format="json")

    robot = Robot.objects.get(pk=registration["id"])
    from devices.services import hash_pairing_code

    assert robot.pairing_code_hash != code
    assert robot.pairing_code_hash != hash_pairing_code(code)  # consumed
    for entry in AuditLog.objects.all():
        assert code not in entry.target
        assert code not in entry.action
        assert code not in entry.ip


def test_robot_detail_write_methods_are_405(api_client):
    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    api_client.post(
        PAIR_URL, {"pairing_code": registration["pairing_code"]}, format="json"
    )
    url = f"/api/v1/devices/{registration['id']}/"

    patch = api_client.patch(url, {"nickname": "stolen"}, format="json")
    put = api_client.put(url, {"nickname": "stolen"}, format="json")
    delete = api_client.delete(url)
    assert patch.status_code == 405
    assert put.status_code == 405
    assert delete.status_code == 405

    robot = Robot.objects.get(pk=registration["id"])
    assert robot.nickname == ""
    assert robot.parent.user.email == "a@example.com"


def test_collection_level_writes_are_405(api_client):
    setup_admin(api_client)
    assert api_client.delete(DEVICE_LIST_URL).status_code == 405
    assert (
        api_client.patch(DEVICE_LIST_URL, {}, format="json").status_code == 405
    )
    assert (
        api_client.post(
            DEVICE_LIST_URL, {"owner_id": "x"}, format="json"
        ).status_code
        == 405
    )


def test_unauthenticated_device_endpoints_return_401(api_client):
    register_device(api_client)  # ensures robots exist; register itself is open
    list_response = api_client.get(DEVICE_LIST_URL)
    pair_response = api_client.post(
        PAIR_URL, {"pairing_code": "ZZZZZZZZ"}, format="json"
    )
    detail_response = api_client.get(
        "/api/v1/devices/000000000000000000000000/"
    )
    for response in (list_response, pair_response, detail_response):
        assert response.status_code == 401
        assert response.json() == {
            "detail": "Authentication credentials were not provided."
        }


def test_malformed_object_ids_return_404_not_500(api_client):
    setup_parent(api_client, "a@example.com")
    for bad_id in (
        "not-an-objectid",
        "123",
        "000000000000000000000g00",
        "zzzz-zzzz",
    ):
        url = f"/api/v1/devices/{bad_id}/"
        response = api_client.get(url)
        assert response.status_code == 404, bad_id
        assert response.json() == {"detail": "Not found."}


def test_path_traversal_like_id_returns_404(api_client):
    setup_parent(api_client, "a@example.com")
    response = api_client.get("/api/v1/devices/..%2F..%2Fetc%2Fpasswd/")
    assert response.status_code in (404, 400)
    assert b"Traceback" not in response.content


def test_non_owned_and_unknown_ids_are_indistinguishable(api_client):
    from rest_framework.test import APIClient

    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    api_client.post(
        PAIR_URL, {"pairing_code": registration["pairing_code"]}, format="json"
    )

    other = APIClient()
    setup_parent(other, "b@example.com")
    non_owned = other.get(f"/api/v1/devices/{registration['id']}/")
    unknown = other.get("/api/v1/devices/000000000000000000000000/")
    malformed = other.get("/api/v1/devices/not-an-objectid/")

    assert non_owned.status_code == unknown.status_code == 404
    assert non_owned.json() == unknown.json() == malformed.json()


def test_admin_list_requires_admin_role(api_client):
    setup_parent(api_client, "a@example.com")
    response = api_client.get(DEVICE_LIST_URL)
    assert response.status_code == 403
    assert response.json() == {"detail": "Admin access required."}


def test_admin_list_allows_admin_role(api_client):
    setup_admin(api_client)
    register_device(api_client)
    response = api_client.get(DEVICE_LIST_URL)
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) == 1
    assert response.json()[0]["device_id"]


def test_admin_can_see_all_robots_across_accounts(api_client):
    from rest_framework.test import APIClient

    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    api_client.post(
        PAIR_URL, {"pairing_code": registration["pairing_code"]}, format="json"
    )

    admin = APIClient()
    setup_admin(admin)
    listing = admin.get(DEVICE_LIST_URL)
    assert listing.status_code == 200
    ids = [robot["id"] for robot in listing.json()]
    assert registration["id"] in ids


def test_no_authentication_bypass_via_header(api_client):
    setup_parent(api_client, "a@example.com")
    registration = register_device(api_client).json()
    api_client.post(
        PAIR_URL, {"pairing_code": registration["pairing_code"]}, format="json"
    )
    api_client.logout()

    fake_headers = {
        "HTTP_AUTHORIZATION": "Bearer fake-token",
        "HTTP_X_DEVICE_ID": registration["device_id"],
    }
    response = api_client.get(
        f"/api/v1/devices/{registration['id']}/", **fake_headers
    )
    assert response.status_code == 401


def test_register_rejects_garbage_without_creating_rows(api_client):
    response = api_client.post(
        REGISTER_URL,
        {"device_id": new_device_id(), "public_key": "nope"},
        format="json",
    )
    assert response.status_code == 400
    assert Robot.objects.count() == 0
    assert DeviceCredential.objects.count() == 0
