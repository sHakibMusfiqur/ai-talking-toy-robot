import uuid

from accounts.models import Parent, User
from accounts.services import create_parent_account

PASSWORD = "S3cure!Passw0rd"

PUBLIC_KEY = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEsimulatedDevicePublicKeyMaterial"
    "usedByPhase6TestsOnly00000000000000000000000000000000000000000000\n"
    "-----END PUBLIC KEY-----"
)

REGISTER_URL = "/api/v1/devices/register/"
REGISTER_URL_RAW = "/api/v1/devices/register"
PAIR_URL = "/api/v1/devices/pair/"
DEVICE_LIST_URL = "/api/v1/devices/"


def new_device_id():
    return str(uuid.uuid4())


def register_device(client, device_id=None, **extra):
    payload = {
        "device_id": device_id or new_device_id(),
        "public_key": PUBLIC_KEY,
        **extra,
    }
    response = client.post(REGISTER_URL, payload, format="json")
    assert response.status_code == 201, response.content
    return response


def setup_parent(client, email):
    create_parent_account(email, PASSWORD)
    response = client.post(
        "/api/v1/auth/login/",
        {"email": email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200
    return Parent.objects.get(user__email=email)


def setup_admin(client, email="admin@example.com"):
    User.objects.create_user(email=email, password=PASSWORD, role="admin")
    response = client.post(
        "/api/v1/auth/login/",
        {"email": email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200
    return User.objects.get(email=email)


def robot_detail_url(robot):
    return f"/api/v1/devices/{robot.pk}/"


def assert_robot_shape(data):
    assert set(data.keys()) == {
        "id",
        "device_id",
        "nickname",
        "pairing_status",
        "firmware_version",
        "last_seen",
        "paired_at",
    }
    assert isinstance(data["id"], str)
    assert len(data["id"]) == 24
