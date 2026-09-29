from django.db import models


class Configuration(models.Model):
    
    robot = models.ForeignKey(
        "devices.Robot", on_delete=models.CASCADE, related_name="configurations"
    )
    version = models.PositiveIntegerField(default=1)
    data = models.JSONField(default=dict, blank=True)
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"config v{self.version} for {self.robot_id}"
