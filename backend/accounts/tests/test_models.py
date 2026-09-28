import pytest
from accounts.models import ChildProfile, Parent, User
from accounts.services import create_parent_account
from django.db import IntegrityError

pytestmark = pytest.mark.django_db


def test_create_user_hashes_password():
    user = User.objects.create_user(
        email="hash@example.com", password="S3cure!Passw0rd"
    )
    assert user.password != "S3cure!Passw0rd"
    assert user.check_password("S3cure!Passw0rd")
    assert not user.check_password("wrong-password")


def test_user_role_defaults_to_parent():
    user = User.objects.create_user(
        email="role@example.com", password="S3cure!Passw0rd"
    )
    assert user.role == User.ROLE_PARENT
    assert user.is_staff is False


def test_create_parent_account_creates_linked_parent():
    user = create_parent_account("linked@example.com", "S3cure!Passw0rd")
    parent = Parent.objects.get(user=user)
    assert parent.user_id == user.pk
    assert user.parent.pk == parent.pk


def test_email_uniqueness_enforced_at_database_level():
    User.objects.create_user(email="dupe@example.com", password="S3cure!Passw0rd")
    with pytest.raises(IntegrityError):
        User.objects.create_user(email="dupe@example.com", password="S3cure!Passw0rd")


def test_childprofile_queryset_scopes_by_parent():
    parent_a = Parent.objects.create(
        user=User.objects.create_user(email="a@example.com", password="S3cure!Passw0rd")
    )
    parent_b = Parent.objects.create(
        user=User.objects.create_user(email="b@example.com", password="S3cure!Passw0rd")
    )
    ChildProfile.objects.create(
        parent=parent_a, first_name="Ayan", age_band="7-9", language="en"
    )
    ChildProfile.objects.create(
        parent=parent_b, first_name="Rafi", age_band="7-9", language="bn"
    )
    assert ChildProfile.objects.filter(parent=parent_a).count() == 1
    assert ChildProfile.objects.filter(parent=parent_a).first().first_name == "Ayan"
    assert ChildProfile.objects.filter(parent=parent_b).count() == 1
