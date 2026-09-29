from django.conf import settings
from django.db import models


class SafetyEvent(models.Model):
    child_profile = models.ForeignKey(
        "accounts.ChildProfile",
        on_delete=models.CASCADE,
        related_name="safety_events",
    )
    robot = models.ForeignKey(
        "devices.Robot",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="safety_events",
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="safety_events",
    )
    message = models.ForeignKey(
        "conversations.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="safety_events",
    )
    category = models.CharField(max_length=64)
    severity = models.CharField(max_length=16, blank=True, default="")
    action_taken = models.CharField(max_length=255, blank=True, default="")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_safety_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"safety event: {self.category}"
