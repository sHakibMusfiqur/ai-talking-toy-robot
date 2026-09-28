import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db


def test_health_returns_200(api_client):
    response = api_client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_rejects_post(api_client):
    response = api_client.post("/health/", {}, format="json")
    assert response.status_code == 405


def test_root_redirects_anonymous_to_login(api_client):
    response = api_client.get("/")
    assert response.status_code == 302
    assert response.url == "/login/"


def test_root_redirects_authenticated_to_health(api_client):
    user = get_user_model().objects.create_user(
        email="root@example.com", password="S3cure!Passw0rd"
    )
    api_client.force_login(user)
    response = api_client.get("/")
    assert response.status_code == 302
    assert response.url == "/health/"


def test_login_page_renders(api_client):
    response = api_client.get("/login/")
    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="username"' in content
    assert 'name="password"' in content
    assert "csrfmiddlewaretoken" in content


def test_login_page_form_authenticates(api_client):
    get_user_model().objects.create_user(
        email="page@example.com", password="S3cure!Passw0rd"
    )
    response = api_client.post(
        "/login/",
        {"username": "page@example.com", "password": "S3cure!Passw0rd"},
    )
    assert response.status_code == 302
    assert response.url == "/"
    assert api_client.get("/health/").status_code == 200
