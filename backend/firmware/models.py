from django.db import models


class Firmware(models.Model):
    version = models.CharField(max_length=64, unique=True)
    checksum = models.CharField(max_length=128, blank=True, default="")
    signature = models.TextField(blank=True, default="")
    release_notes = models.TextField(blank=True, default="")
    min_hardware_rev = models.CharField(max_length=32, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.version


class OTAUpdate(models.Model):
    STATUS_PENDING = "pending"
    STATUS_DOWNLOADING = "downloading"
    STATUS_APPLIED = "applied"
    STATUS_FAILED = "failed"
    STATUS_ROLLED_BACK = "rolled_back"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_DOWNLOADING, "Downloading"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_FAILED, "Failed"),
        (STATUS_ROLLED_BACK, "Rolled back"),
    ]

    robot = models.ForeignKey(
        "devices.Robot", on_delete=models.CASCADE, related_name="ota_updates"
    )
    firmware = models.ForeignKey(
        Firmware, on_delete=models.CASCADE, related_name="ota_updates"
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"OTA {self.firmware.version} -> {self.robot_id} ({self.status})"
