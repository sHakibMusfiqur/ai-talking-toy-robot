from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    ROLE_PARENT = "parent"
    ROLE_ADMIN = "admin"
    ROLE_CHOICES = [
        (ROLE_PARENT, "Parent"),
        (ROLE_ADMIN, "Admin"),
    ]

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_PARENT)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"


class Parent(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="parent",
    )
    display_name = models.CharField(max_length=100, blank=True, default="")
    contact_preferences = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.display_name or self.user.email


class ChildProfile(models.Model):
    parent = models.ForeignKey(Parent, on_delete=models.CASCADE, related_name="children")
    first_name = models.CharField(max_length=100)
    age_band = models.CharField(max_length=32)
    language = models.CharField(max_length=32)

    def __str__(self):
        return self.first_name
