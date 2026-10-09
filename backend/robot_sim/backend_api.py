"""Backend REST client for the simulator: register -> challenge -> token,
exactly the three device endpoints the firmware uses (Flow 4 / AR-10).

The HTTP transport is injectable so the automated tests can drive the same
code through Django's test client (no sockets) while the CLI uses httpx
against a running server. Secrets policy: pairing codes and access tokens
are returned to the caller and never logged here; failure messages carry
status codes only, never response bodies.
"""

import json
import logging

logger = logging.getLogger(__name__)

REGISTER_PATH = "/api/v1/devices/register/"
CHALLENGE_PATH = "/api/v1/devices/auth/challenge/"
TOKEN_PATH = "/api/v1/devices/auth/token/"

DEFAULT_FIRMWARE_VERSION = "sim-0.1.0"

# Firmware token refresh rule: effective lifetime = expires_in - 300 s,
# floored at 60 s (never refresh on the very edge of expiry).
TOKEN_REFRESH_MARGIN_SECONDS = 300
TOKEN_REFRESH_MIN_EFFECTIVE_SECONDS = 60


class BackendError(RuntimeError):
    """Transport-level or HTTP failure talking to the Django backend."""


def token_refresh_due(*, issued_monotonic, expires_in, now_monotonic) -> bool:
    effective = max(
        int(expires_in) - TOKEN_REFRESH_MARGIN_SECONDS,
        TOKEN_REFRESH_MIN_EFFECTIVE_SECONDS,
    )
    return (now_monotonic - issued_monotonic) >= effective


def _decode_body(content) -> dict:
    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class HttpxTransport:
    """Production transport for the CLI (httpx is already a backend dep)."""

    def __init__(self, base_url, timeout=10.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def post(self, path, data):
        import httpx

        try:
            response = httpx.post(
                self._base_url + path,
                json=data,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise BackendError(
                f"backend unreachable ({type(exc).__name__})"
            ) from None
        return response.status_code, _decode_body(response.content)


class DjangoTestTransport:
    """Test transport driving Django's own test client - same request
    shapes as httpx, no network involved."""

    def __init__(self, api_client):
        self._client = api_client

    def post(self, path, data):
        response = self._client.post(path, data, format="json")
        return response.status_code, _decode_body(response.content)


class BackendApi:
    def __init__(self, transport, identity, firmware_version=DEFAULT_FIRMWARE_VERSION):
        self._transport = transport
        self._identity = identity
        self._firmware_version = firmware_version

    @property
    def device_id(self):
        return self._identity.device_id

    def register(self):
        """Register the device. Returns the single-use pairing code on a
        fresh 201, or None on 409 (already registered - normal for a
        persistent identity). The caller may print the code once; it is
        never logged here."""
        status, body = self._transport.post(
            REGISTER_PATH,
            {
                "device_id": self._identity.device_id,
                "public_key": self._identity.public_key_pem,
                "firmware_version": self._firmware_version,
            },
        )
        if status == 201:
            code = body.get("pairing_code")
            if not isinstance(code, str) or not code:
                raise BackendError("registration response had no pairing code")
            return code
        if status == 409:
            return None
        raise BackendError(f"device registration failed (HTTP {status})")

    def fetch_token(self):
        """Run the signed-challenge exchange. Returns (token, expires_in).
        The token is returned in memory only and never logged."""
        status, challenge = self._transport.post(
            CHALLENGE_PATH, {"device_id": self._identity.device_id}
        )
        if status != 200:
            raise BackendError(f"challenge request failed (HTTP {status})")
        try:
            challenge_id = challenge["challenge_id"]
            challenge_value = challenge["challenge"]
            challenge_expires = int(challenge["expires_in"])
        except (KeyError, TypeError, ValueError):
            raise BackendError("challenge response was malformed") from None
        if not isinstance(challenge_value, str) or not challenge_value:
            raise BackendError("challenge response was malformed")

        signature = self._identity.sign_challenge(challenge_value)
        status, token_body = self._transport.post(
            TOKEN_PATH,
            {
                "device_id": self._identity.device_id,
                "challenge_id": challenge_id,
                "challenge": challenge_value,
                "signature": signature,
            },
        )
        if status != 200:
            raise BackendError(f"token exchange failed (HTTP {status})")
        token = token_body.get("access_token")
        if not isinstance(token, str) or not token:
            raise BackendError("token response was malformed")
        expires_in = token_body.get("expires_in")
        try:
            expires_in = int(expires_in) if expires_in is not None else challenge_expires
        except (TypeError, ValueError):
            expires_in = challenge_expires
        logger.debug("device token acquired (expires_in=%ss)", expires_in)
        return token, expires_in
