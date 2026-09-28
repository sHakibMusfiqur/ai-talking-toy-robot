import pytest
from accounts.models import ChildProfile, Parent, User
from accounts.services import create_parent_account

pytestmark = pytest.mark.django_db

PASSWORD = "S3cure!Passw0rd"

CHILD = {"first_name": "Nadi", "age_band": "7-9", "language": "en"}


def setup_parent(client, email):
    create_parent_account(email, PASSWORD)
    response = client.post(
        "/api/v1/auth/login/",
        {"email": email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200
    return Parent.objects.get(user__email=email)


def test_create_child_returns_201_with_contract_shape(api_client):
    parent = setup_parent(api_client, "a@example.com")
    response = api_client.post("/api/v1/children/", CHILD, format="json")
    assert response.status_code == 201
    data = response.json()
    assert set(data.keys()) == {"id", "first_name", "age_band", "language"}
    assert data["first_name"] == "Nadi"
    assert data["age_band"] == "7-9"
    assert data["language"] == "en"
    assert isinstance(data["id"], str)
    assert len(data["id"]) == 24
    child = ChildProfile.objects.get(pk=data["id"])
    assert child.parent_id == parent.pk


def test_create_child_ignores_client_supplied_parent_id(api_client):
    parent_a = setup_parent(api_client, "a@example.com")
    parent_b = Parent.objects.create(
        user=User.objects.create_user(email="b@example.com", password=PASSWORD)
    )
    response = api_client.post(
        "/api/v1/children/",
        {**CHILD, "parent_id": str(parent_b.pk)},
        format="json",
    )
    assert response.status_code == 201
    child = ChildProfile.objects.get(pk=response.json()["id"])
    assert child.parent_id == parent_a.pk
    assert ChildProfile.objects.filter(parent=parent_b).count() == 0


def test_list_children_returns_only_own(api_client):
    parent_a = setup_parent(api_client, "a@example.com")
    ChildProfile.objects.create(
        parent=parent_a, first_name="Ayan", age_band="7-9", language="en"
    )
    other_parent = Parent.objects.create(
        user=User.objects.create_user(email="b@example.com", password=PASSWORD)
    )
    ChildProfile.objects.create(
        parent=other_parent, first_name="Rafi", age_band="7-9", language="bn"
    )
    response = api_client.get("/api/v1/children/")
    assert response.status_code == 200
    names = [c["first_name"] for c in response.json()]
    assert names == ["Ayan"]


def test_get_child_detail_owned(api_client):
    setup_parent(api_client, "a@example.com")
    child_id = api_client.post(
        "/api/v1/children/", CHILD, format="json"
    ).json()["id"]
    response = api_client.get(f"/api/v1/children/{child_id}/")
    assert response.status_code == 200
    assert response.json()["id"] == child_id


def test_partial_update_child(api_client):
    setup_parent(api_client, "a@example.com")
    child_id = api_client.post(
        "/api/v1/children/", CHILD, format="json"
    ).json()["id"]
    response = api_client.patch(
        f"/api/v1/children/{child_id}/", {"age_band": "10-12"}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["age_band"] == "10-12"
    assert response.json()["first_name"] == "Nadi"


def test_delete_child_removes_it(api_client):
    setup_parent(api_client, "a@example.com")
    child_id = api_client.post(
        "/api/v1/children/", CHILD, format="json"
    ).json()["id"]
    response = api_client.delete(f"/api/v1/children/{child_id}/")
    assert response.status_code == 204
    assert ChildProfile.objects.filter(pk=child_id).count() == 0
    assert api_client.get(f"/api/v1/children/{child_id}/").status_code == 404


def test_child_of_other_parent_returns_404_not_403(api_client):
    setup_parent(api_client, "a@example.com")
    other_parent = Parent.objects.create(
        user=User.objects.create_user(email="b@example.com", password=PASSWORD)
    )
    victim = ChildProfile.objects.create(
        parent=other_parent, first_name="Rafi", age_band="7-9", language="bn"
    )

    get_response = api_client.get(f"/api/v1/children/{victim.pk}/")
    patch_response = api_client.patch(
        f"/api/v1/children/{victim.pk}/", {"age_band": "10-12"}, format="json"
    )
    delete_response = api_client.delete(f"/api/v1/children/{victim.pk}/")

    assert get_response.status_code == 404
    assert patch_response.status_code == 404
    assert delete_response.status_code == 404

    victim.refresh_from_db()
    assert victim.first_name == "Rafi"
    assert victim.age_band == "7-9"


def test_unknown_id_and_foreign_id_have_same_404_status(api_client):
    setup_parent(api_client, "a@example.com")
    other_parent = Parent.objects.create(
        user=User.objects.create_user(email="b@example.com", password=PASSWORD)
    )
    victim = ChildProfile.objects.create(
        parent=other_parent, first_name="Rafi", age_band="7-9", language="bn"
    )
    foreign = api_client.get(f"/api/v1/children/{victim.pk}/")
    unknown = api_client.get("/api/v1/children/000000000000000000000000/")
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()


def test_malformed_child_id_returns_404(api_client):
    setup_parent(api_client, "a@example.com")
    response = api_client.get("/api/v1/children/not-an-objectid/")
    assert response.status_code == 404


def test_unauthenticated_requests_return_401(api_client):
    list_response = api_client.get("/api/v1/children/")
    create_response = api_client.post("/api/v1/children/", CHILD, format="json")
    detail_response = api_client.get(
        "/api/v1/children/000000000000000000000000/"
    )
    for response in (list_response, create_response, detail_response):
        assert response.status_code == 401
        assert response.json() == {
            "detail": "Authentication credentials were not provided."
        }


def test_missing_required_fields_return_400(api_client):
    setup_parent(api_client, "a@example.com")
    response = api_client.post("/api/v1/children/", {}, format="json")
    assert response.status_code == 400
    errors = response.json()
    assert set(errors.keys()) == {"first_name", "age_band", "language"}


def test_blank_first_name_returns_400(api_client):
    setup_parent(api_client, "a@example.com")
    response = api_client.post(
        "/api/v1/children/", {**CHILD, "first_name": ""}, format="json"
    )
    assert response.status_code == 400
    assert "first_name" in response.json()


def test_put_and_collection_delete_are_405(api_client):
    setup_parent(api_client, "a@example.com")
    child_id = api_client.post(
        "/api/v1/children/", CHILD, format="json"
    ).json()["id"]
    put_response = api_client.put(
        f"/api/v1/children/{child_id}/", CHILD, format="json"
    )
    collection_delete = api_client.delete("/api/v1/children/")
    assert put_response.status_code == 405
    assert collection_delete.status_code == 405
