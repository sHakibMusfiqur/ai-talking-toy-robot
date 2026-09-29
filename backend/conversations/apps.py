from django.apps import AppConfig


class ConversationsConfig(AppConfig):
    default_auto_field = "django_mongodb_backend.fields.ObjectIdAutoField"
    name = "conversations"
