from django_mongodb_backend import transaction

from .models import Parent, User


def create_parent_account(email, password):
    with transaction.atomic():
        user = User.objects.create_user(email=email, password=password)
        Parent.objects.create(user=user)
    return user
