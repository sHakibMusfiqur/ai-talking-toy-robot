from bson import ObjectId
from bson.errors import InvalidId
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import Robot
from .tokens import INVALID_CREDENTIALS, decode_device_token


class DeviceJWTAuthentication(BaseAuthentication):
    """Bearer device-JWT authentication, attached only to device-facing
    views (never to human session endpoints). Resolves exactly one Robot
    from the token's `sub` claim — client-supplied identifiers are never
    consulted. Human session auth and device auth are fully separate."""

    def authenticate_header(self, request):
        # WWW-Authenticate: Bearer — keeps auth failures at 401 (DRF falls
        # back to 403 when no scheme is advertised).
        return "Bearer"

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer "):
            return None
        token = header[len("Bearer ") :].strip()
        if not token:
            raise AuthenticationFailed(INVALID_CREDENTIALS)
        payload = decode_device_token(token)
        try:
            if not ObjectId.is_valid(payload["sub"]):
                raise InvalidId(payload["sub"])
            robot = Robot.objects.filter(pk=ObjectId(payload["sub"])).first()
        except (TypeError, ValueError, InvalidId, KeyError):
            raise AuthenticationFailed(INVALID_CREDENTIALS)
        if robot is None:
            raise AuthenticationFailed(INVALID_CREDENTIALS)
        return (None, {"robot": robot, "payload": payload})
