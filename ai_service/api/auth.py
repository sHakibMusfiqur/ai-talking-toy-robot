import hmac
import os

from fastapi import Header, HTTPException

UNAUTHORIZED = "Unauthorized."
MISCONFIGURED = "Internal service misconfigured."


def _expected_token() -> str:
    return os.environ.get("AI_SERVICE_INTERNAL_TOKEN", "")


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail=UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})


async def require_internal_token(authorization: str | None = Header(default=None)) -> None:
    expected = _expected_token()
    if not expected:
        # Never treat an empty configured token as valid (no dev bypass).
        raise HTTPException(status_code=500, detail=MISCONFIGURED)
    if not authorization:
        raise _unauthorized()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized()
    if not hmac.compare_digest(token.strip(), expected):
        raise _unauthorized()
