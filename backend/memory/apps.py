from django.apps import AppConfig


class MemoryConfig(AppConfig):
    default_auto_field = "django_mongodb_backend.fields.ObjectIdAutoField"
    name = "memory"
