from rest_framework import status
from rest_framework.exceptions import APIException


class DeviceAlreadyRegistered(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Device already registered."
    default_code = "device_already_registered"


class InvalidPairingCode(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Invalid pairing code."
    default_code = "invalid_pairing_code"


class InvalidDeviceId(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Invalid device id."
    default_code = "invalid_device_id"


class InvalidPublicKey(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Invalid public key."
    default_code = "invalid_public_key"


class InvalidDeviceCredentials(APIException):
    """Uniform device-authentication failure: unknown device, expired or
    replayed challenge, bad signature — indistinguishable by design."""

    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Invalid credentials."
    default_code = "invalid_device_credentials"


class RobotCommandUnavailable(APIException):
    """MQTT transport failure while publishing a command (Phase 8): the
    command was not accepted by the broker, so nothing was delivered."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Robot command service unavailable."
    default_code = "robot_command_unavailable"


class RobotCommandThrottled(APIException):
    """Duplicate-command suppression (Phase A): the same authenticated user
    repeated a command for the same robot inside the cooldown window."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Command repeated too quickly."
    default_code = "robot_command_throttled"


class RobotAlreadyPaired(APIException):
    """A device-authenticated robot that is already linked to a parent asks
    for a claim ticket — pairing state changes go through explicit pairing
    or claim flows, never through ticket reissuance."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "Robot already paired."
    default_code = "robot_already_paired"


class InvalidClaimTicket(APIException):
    """Uniform claim failure: malformed, unknown, expired, already-consumed
    or already-paired tickets are indistinguishable by design."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "This setup link is invalid or has expired."
    default_code = "invalid_claim_ticket"


class ClaimThrottled(APIException):
    """Per-user claim attempt limit: repeated invalid claim posts from one
    session inside the window are rejected before ticket lookup."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Too many claim attempts. Please wait and try again."
    default_code = "claim_throttled"
