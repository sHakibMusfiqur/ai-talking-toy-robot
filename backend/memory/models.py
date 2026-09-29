from django.db import models


class Memory(models.Model):
    child_profile = models.ForeignKey(
        "accounts.ChildProfile",
        on_delete=models.CASCADE,
        related_name="memories",
    )
    key = models.CharField(max_length=100)
    value = models.TextField()
    source_conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memories",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.key} ({self.child_profile_id})"
