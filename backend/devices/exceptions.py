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
