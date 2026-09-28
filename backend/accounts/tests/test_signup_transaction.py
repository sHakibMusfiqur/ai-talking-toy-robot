import pytest
from accounts.models import Parent, User

PASSWORD = "S3cure!Passw0rd"
SIGNUP = {"email": "atomic@example.com", "password": PASSWORD}


@pytest.mark.django_db(transaction=True)
def test_signup_persists_user_and_parent(api_client):
    response = api_client.post("/api/v1/auth/signup/", SIGNUP, format="json")
    assert response.status_code == 201
    user = User.objects.get(email="atomic@example.com")
    assert Parent.objects.filter(user=user).count() == 1


@pytest.mark.django_db(transaction=True)
def test_parent_creation_failure_rolls_back_user(api_client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("parent creation failed")

    monkeypatch.setattr("accounts.services.Parent.objects.create", boom)

    with pytest.raises(RuntimeError):
        api_client.post("/api/v1/auth/signup/", SIGNUP, format="json")

    assert User.objects.filter(email="atomic@example.com").count() == 0
    assert Parent.objects.count() == 0
