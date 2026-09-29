from django.db import models


class Conversation(models.Model):
    robot = models.ForeignKey(
        "devices.Robot", on_delete=models.CASCADE, related_name="conversations"
    )
    child_profile = models.ForeignKey(
        "accounts.ChildProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="conversations",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    language = models.CharField(max_length=32, blank=True, default="")

    def __str__(self):
        return f"conversation {self.pk}"


class Message(models.Model):
    ROLE_CHILD = "child"
    ROLE_ROBOT = "robot"
    ROLE_CHOICES = [
        (ROLE_CHILD, "Child"),
        (ROLE_ROBOT, "Robot"),
    ]

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=8, choices=ROLE_CHOICES)
    text = models.TextField(blank=True, default="")
    audio_reference = models.CharField(max_length=255, blank=True, default="")
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.role} message in {self.conversation_id}"


class AIInteraction(models.Model):
    message = models.OneToOneField(
        Message, on_delete=models.CASCADE, related_name="ai_interaction"
    )
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    model_version = models.CharField(max_length=64, blank=True, default="")
    safety_flags = models.JSONField(default=dict, blank=True)
    emotion = models.CharField(max_length=32, blank=True, default="")
    action = models.CharField(max_length=32, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"AI interaction for message {self.message_id}"
