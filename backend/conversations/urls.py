from django.urls import path

from . import views

urlpatterns = [
    path(
        "conversations/turn/",
        views.ConversationTurnView.as_view(),
        name="api-conversation-turn",
    ),
    
    path(
        "conversations/turn",
        views.ConversationTurnView.as_view(),
        name="api-conversation-turn-raw",
    ),
    path(
        "conversations/turn/audio/",
        views.ConversationAudioTurnView.as_view(),
        name="api-conversation-turn-audio",
    ),
    # Same audio view without the trailing slash (same rationale as above).
    path(
        "conversations/turn/audio",
        views.ConversationAudioTurnView.as_view(),
        name="api-conversation-turn-audio-raw",
    ),
]
