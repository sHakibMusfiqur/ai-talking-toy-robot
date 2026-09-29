import time

import jwt
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed

INVALID_CREDENTIALS = "Invalid credentials."


def issue_device_token(robot):
    now = int(time.time())
    ttl = settings.DEVICE_JWT_TTL_SECONDS
    payload = {
        "sub": str(robot.pk),
        "iss": settings.DEVICE_JWT_ISSUER,
        "aud": settings.DEVICE_JWT_AUDIENCE,
        "iat": now,
        "exp": now + ttl,
    }
    token = jwt.encode(
        payload,
        settings.DEVICE_JWT_SECRET,
        algorithm=settings.DEVICE_JWT_ALGORITHM,
    )
    return token, ttl


def decode_device_token(token):
    """Validate signature, algorithm, issuer, audience, expiry and required
    claims. Raises AuthenticationFailed with a uniform detail on any fault."""
    try:
        return jwt.decode(
            token,
            settings.DEVICE_JWT_SECRET,
            algorithms=[settings.DEVICE_JWT_ALGORITHM],
            issuer=settings.DEVICE_JWT_ISSUER,
            audience=settings.DEVICE_JWT_AUDIENCE,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError:
        raise AuthenticationFailed(INVALID_CREDENTIALS)
