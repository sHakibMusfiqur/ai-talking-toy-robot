from django.urls import path

from . import views

urlpatterns = [
    path(
        "conversations/turn/",
        views.ConversationTurnView.as_view(),
        name="api-conversation-turn",
    ),
    # Same view without the trailing slash, mirroring the device-facing
    # endpoint convention: a firmware POST without the slash is served
    # directly instead of following an APPEND_SLASH redirect, which would
    # not reliably re-send the method and body.
    path(
        "conversations/turn",
        views.ConversationTurnView.as_view(),
        name="api-conversation-turn-raw",
    ),
]
