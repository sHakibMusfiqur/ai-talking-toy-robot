import pytest
from accounts.models import Parent, User
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

PASSWORD = "S3cure!Passw0rd"


def signup(client, email="parent@example.com"):
    return client.post(
        "/api/v1/auth/signup/",
        {"email": email, "password": PASSWORD},
        format="json",
    )


def login(client, email="parent@example.com"):
    return client.post(
        "/api/v1/auth/login/",
        {"email": email, "password": PASSWORD},
        format="json",
    )


def test_signup_creates_user_and_parent_without_session(api_client):
    response = signup(api_client)
    assert response.status_code == 201
    data = response.json()
    assert set(data.keys()) == {"id", "email"}
    assert data["email"] == "parent@example.com"
    assert isinstance(data["id"], str)
    assert len(data["id"]) == 24
    assert "password" not in data

    user = User.objects.get(email="parent@example.com")
    assert user.check_password(PASSWORD)
    assert Parent.objects.filter(user=user).count() == 1

    assert api_client.get("/api/v1/children/").status_code == 401


def test_signup_duplicate_email_rejected(api_client):
    assert signup(api_client).status_code == 201
    response = signup(api_client)
    assert response.status_code == 400
    assert User.objects.filter(email="parent@example.com").count() == 1
    assert Parent.objects.count() == 1


def test_signup_weak_password_rejected(api_client):
    response = api_client.post(
        "/api/v1/auth/signup/",
        {"email": "weak@example.com", "password": "abc"},
        format="json",
    )
    assert response.status_code == 400
    assert "password" in response.json()
    assert User.objects.filter(email="weak@example.com").exists() is False


def test_signup_email_is_normalized(api_client):
    response = api_client.post(
        "/api/v1/auth/signup/",
        {"email": "  MixedCase@EXAMPLE.COM ", "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["email"] == "mixedcase@example.com"
    assert User.objects.filter(email="mixedcase@example.com").exists()


def test_login_success_returns_identity_and_opens_session(api_client):
    signup(api_client)
    api_client.logout()
    response = login(api_client)
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"id", "email", "role"}
    assert data["email"] == "parent@example.com"
    assert data["role"] == "parent"
    assert isinstance(data["id"], str)
    assert api_client.get("/api/v1/children/").status_code == 200


def test_login_wrong_password_returns_401(api_client):
    signup(api_client)
    api_client.logout()
    response = api_client.post(
        "/api/v1/auth/login/",
        {"email": "parent@example.com", "password": "WrongPass!42"},
        format="json",
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials."}


def test_login_unknown_email_returns_identical_401(api_client):
    wrong_password = api_client.post(
        "/api/v1/auth/login/",
        {"email": "parent@example.com", "password": "WrongPass!42"},
        format="json",
    )
    unknown_email = api_client.post(
        "/api/v1/auth/login/",
        {"email": "ghost@example.com", "password": "WrongPass!42"},
        format="json",
    )
    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_logout_revokes_session(api_client):
    signup(api_client)
    api_client.logout()
    assert login(api_client).status_code == 200
    response = api_client.post("/api/v1/auth/logout/", format="json")
    assert response.status_code == 204
    assert api_client.get("/api/v1/children/").status_code == 401


def test_logout_requires_authentication(api_client):
    response = api_client.post("/api/v1/auth/logout/", format="json")
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication credentials were not provided."}


def test_authenticated_write_requires_csrf_token():
    client = APIClient(enforce_csrf_checks=True)
    signup(client)
    client.post(
        "/api/v1/auth/login/",
        {"email": "parent@example.com", "password": PASSWORD},
        format="json",
    )
    response = client.post(
        "/api/v1/children/",
        {"first_name": "Nadi", "age_band": "7-9", "language": "en"},
        format="json",
    )
    assert response.status_code == 403


def test_authenticated_write_succeeds_with_valid_csrf_token():
    client = APIClient(enforce_csrf_checks=True)
    signup(client)
    client.post(
        "/api/v1/auth/login/",
        {"email": "parent@example.com", "password": PASSWORD},
        format="json",
    )
    client.get("/login/")
    token = client.cookies["csrftoken"].value
    response = client.post(
        "/api/v1/children/",
        {"first_name": "Nadi", "age_band": "7-9", "language": "en"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 201
