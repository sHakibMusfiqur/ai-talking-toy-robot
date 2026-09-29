from django.apps import AppConfig


class FirmwareConfig(AppConfig):
    default_auto_field = "django_mongodb_backend.fields.ObjectIdAutoField"
    name = "firmware"
