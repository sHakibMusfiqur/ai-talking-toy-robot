from django.conf import settings
from django.db import models


class UsageLog(models.Model):
    child_profile = models.ForeignKey(
        "accounts.ChildProfile",
        on_delete=models.CASCADE,
        related_name="usage_logs",
    )
    robot = models.ForeignKey(
        "devices.Robot",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="usage_logs",
    )
    date = models.DateField()
    minutes_active = models.PositiveIntegerField(default=0)
    topics_covered = models.JSONField(default=list, blank=True)
    quiz_scores = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"usage {self.date} ({self.minutes_active}m)"


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=64)
    target = models.CharField(max_length=255, blank=True, default="")
    ip = models.CharField(max_length=45, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.action} -> {self.target}"
