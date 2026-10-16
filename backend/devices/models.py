from django.db import models


class Robot(models.Model):
    PAIRING_UNPAIRED = "unpaired"
    PAIRING_PAIRED = "paired"
    PAIRING_CHOICES = [
        (PAIRING_UNPAIRED, "Unpaired"),
        (PAIRING_PAIRED, "Paired"),
    ]

    device_id = models.CharField(max_length=64, unique=True)
    nickname = models.CharField(max_length=100, blank=True, default="")
    parent = models.ForeignKey(
        "accounts.Parent",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="robots",
    )
    pairing_status = models.CharField(
        max_length=16, choices=PAIRING_CHOICES, default=PAIRING_UNPAIRED
    )
    pairing_code_hash = models.CharField(max_length=64, blank=True, default="")
    pairing_code_expires_at = models.DateTimeField(null=True, blank=True)
    paired_at = models.DateTimeField(null=True, blank=True)
    firmware_version = models.CharField(max_length=32, blank=True, default="")
    last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.device_id


class DeviceCredential(models.Model):
    robot = models.OneToOneField(
        Robot, on_delete=models.CASCADE, related_name="credential"
    )
    public_key = models.TextField()
    token_hash = models.CharField(max_length=128, blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"credential for {self.robot.device_id}"


class RobotState(models.Model):
    robot = models.OneToOneField(Robot, on_delete=models.CASCADE, related_name="state")
    state = models.CharField(max_length=32, blank=True, default="")
    battery_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    online = models.BooleanField(default=False)
    rssi = models.IntegerField(null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"state of {self.robot.device_id}"


class RobotEvent(models.Model):
    robot = models.ForeignKey(Robot, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.event_type} @ {self.robot.device_id}"


class SensorData(models.Model):
    robot = models.ForeignKey(
        Robot, on_delete=models.CASCADE, related_name="sensor_data"
    )
    sensor_type = models.CharField(max_length=32)
    value = models.FloatField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sensor_type}={self.value} @ {self.robot.device_id}"


class DeviceChallenge(models.Model):
    robot = models.ForeignKey(
        Robot, on_delete=models.CASCADE, related_name="challenges"
    )
    challenge_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"challenge for {self.robot.device_id}"


class DeviceClaimTicket(models.Model):
    """Device-issued claim ticket (Wi-Fi onboarding): after the robot
    reaches the backend over the parent's Wi-Fi it requests a single-use
    ticket; only the SHA-256 hash is persisted. One live ticket per robot
    (issuance deletes any previous row), short-TTL, consumed atomically by
    the explicit claim confirm — possession of the raw ticket is the sole
    capability it grants, and it never carries ownership data."""

    robot = models.OneToOneField(
        Robot, on_delete=models.CASCADE, related_name="claim_ticket"
    )
    ticket_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"claim ticket for {self.robot.device_id}"
