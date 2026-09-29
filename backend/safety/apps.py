from django.apps import AppConfig


class SafetyConfig(AppConfig):
    default_auto_field = "django_mongodb_backend.fields.ObjectIdAutoField"
    name = "safety"
