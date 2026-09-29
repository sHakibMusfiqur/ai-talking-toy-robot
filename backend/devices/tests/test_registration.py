import pytest
from devices.models import DeviceCredential, Robot
from devices.services import hash_pairing_code

from .helpers import PUBLIC_KEY, REGISTER_URL, REGISTER_URL_RAW, new_device_id

pytestmark = pytest.mark.django_db

ROBOT_KEYS = {
    "id",
    "device_id",
    "nickname",
    "pairing_status",
    "firmware_version",
    "last_seen",
    "paired_at",
}


def test_register_returns_201_with_contract_shape(api_client):
    device_id = new_device_id()
    response = api_client.post(
        REGISTER_URL,
        {"device_id": device_id, "public_key": PUBLIC_KEY},
        format="json",
    )
    assert response.status_code == 201
    data = response.json()
    assert set(data.keys()) == {
        "id",
        "device_id",
        "pairing_status",
        "pairing_code",
    }
    assert data["device_id"] == device_id
    assert data["pairing_status"] == "unpaired"
    assert isinstance(data["id"], str)
    assert len(data["id"]) == 24
    assert isinstance(data["pairing_code"], str)
    assert len(data["pairing_code"]) >= 8


def test_register_creates_robot_and_device_credential(api_client):
    device_id = new_device_id()
    response = api_client.post(
        REGISTER_URL,
        {"device_id": device_id, "public_key": PUBLIC_KEY},
        format="json",
    )
    robot = Robot.objects.get(pk=response.json()["id"])
    assert robot.device_id == device_id
    assert robot.pairing_status == Robot.PAIRING_UNPAIRED
    assert robot.parent is None
    assert robot.pairing_code_hash
    assert robot.pairing_code_expires_at is not None

    credential = DeviceCredential.objects.get(robot=robot)
    assert credential.public_key == PUBLIC_KEY
    assert credential.token_hash == ""


def test_register_stores_hash_not_raw_pairing_code(api_client):
    response = api_client.post(
        REGISTER_URL,
        {"device_id": new_device_id(), "public_key": PUBLIC_KEY},
        format="json",
    )
    raw_code = response.json()["pairing_code"]
    robot = Robot.objects.get(pk=response.json()["id"])
    assert robot.pairing_code_hash != raw_code
    assert robot.pairing_code_hash == hash_pairing_code(raw_code)


def test_register_without_trailing_slash_also_works(api_client):
    response = api_client.post(
        REGISTER_URL_RAW,
        {"device_id": new_device_id(), "public_key": PUBLIC_KEY},
        format="json",
    )
    assert response.status_code == 201


def test_register_works_unauthenticated(api_client):
    response = api_client.post(
        REGISTER_URL,
        {"device_id": new_device_id(), "public_key": PUBLIC_KEY},
        format="json",
    )
    assert response.status_code == 201


def test_register_stores_firmware_version(api_client):
    response = api_client.post(
        REGISTER_URL,
        {
            "device_id": new_device_id(),
            "public_key": PUBLIC_KEY,
            "firmware_version": "1.2.3",
        },
        format="json",
    )
    robot = Robot.objects.get(pk=response.json()["id"])
    assert robot.firmware_version == "1.2.3"


def test_register_missing_fields_return_400(api_client):
    response = api_client.post(REGISTER_URL, {}, format="json")
    assert response.status_code == 400
    errors = response.json()
    assert set(errors.keys()) == {"device_id", "public_key"}


def test_register_invalid_uuid_device_id_returns_400(api_client):
    response = api_client.post(
        REGISTER_URL,
        {"device_id": "not-a-uuid", "public_key": PUBLIC_KEY},
        format="json",
    )
    assert response.status_code == 400
    assert "device_id" in response.json()
    assert Robot.objects.count() == 0


def test_register_blank_device_id_returns_400(api_client):
    response = api_client.post(
        REGISTER_URL,
        {"device_id": "   ", "public_key": PUBLIC_KEY},
        format="json",
    )
    assert response.status_code == 400
    assert "device_id" in response.json()


def test_register_non_pem_public_key_returns_400(api_client):
    response = api_client.post(
        REGISTER_URL,
        {"device_id": new_device_id(), "public_key": "just some text"},
        format="json",
    )
    assert response.status_code == 400
    assert "public_key" in response.json()
    assert Robot.objects.count() == 0


def test_register_duplicate_device_id_returns_409(api_client):
    device_id = new_device_id()
    first = api_client.post(
        REGISTER_URL,
        {"device_id": device_id, "public_key": PUBLIC_KEY},
        format="json",
    )
    assert first.status_code == 201
    second = api_client.post(
        REGISTER_URL,
        {"device_id": device_id, "public_key": PUBLIC_KEY},
        format="json",
    )
    assert second.status_code == 409
    assert second.json() == {"detail": "Device already registered."}
    assert Robot.objects.filter(device_id=device_id).count() == 1


def test_register_duplicate_does_not_reissue_pairing_code(api_client):
    device_id = new_device_id()
    first = api_client.post(
        REGISTER_URL,
        {"device_id": device_id, "public_key": PUBLIC_KEY},
        format="json",
    )
    original_hash = Robot.objects.get(device_id=device_id).pairing_code_hash
    api_client.post(
        REGISTER_URL,
        {"device_id": device_id, "public_key": PUBLIC_KEY},
        format="json",
    )
    robot = Robot.objects.get(device_id=device_id)
    assert robot.pairing_code_hash == original_hash
    assert first.json()["pairing_code"]


def test_register_normalizes_device_id_case(api_client):
    device_id = new_device_id().upper()
    response = api_client.post(
        REGISTER_URL,
        {"device_id": device_id, "public_key": PUBLIC_KEY},
        format="json",
    )
    assert response.status_code == 201
    assert Robot.objects.filter(device_id=device_id.lower()).count() == 1


def test_register_records_audit_log(api_client):
    response = api_client.post(
        REGISTER_URL,
        {"device_id": new_device_id(), "public_key": PUBLIC_KEY},
        format="json",
    )
    from analytics.models import AuditLog

    entry = AuditLog.objects.get(action="robot.registered")
    assert entry.target == response.json()["id"]
    assert entry.actor is None
