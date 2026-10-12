import logging

import httpx
from django.conf import settings

from .ai_contract import AIContractError, validate_ai_output

logger = logging.getLogger(__name__)

AI_TURN_PATH = "/v1/conversation/turn"


class AIServiceError(Exception):
    """AI-service call failed; `reason` is one of `FAILURE_REASONS`."""

    def __init__(self, reason, status_code=None):
        super().__init__(f"AI service call failed ({reason})")
        self.reason = reason
        self.status_code = status_code


def call_ai_turn(payload, *, base_url=None, token=None, timeout=None, transport=None):
   
    base_url = settings.AI_SERVICE_URL if base_url is None else base_url
    token = settings.AI_SERVICE_INTERNAL_TOKEN if token is None else token
    timeout = settings.AI_SERVICE_TIMEOUT_SECONDS if timeout is None else timeout

    if not token:
        # Fail closed: no token, no call, no development bypass.
        logger.error("AI_SERVICE_INTERNAL_TOKEN is not configured")
        raise AIServiceError("config")

    url = f"{base_url.rstrip('/')}{AI_TURN_PATH}"
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.TimeoutException:
        logger.warning("AI service call timed out (reason=timeout)")
        raise AIServiceError("timeout")
    except httpx.HTTPError:
        logger.warning("AI service unreachable (reason=unavailable)")
        raise AIServiceError("unavailable")

    if response.status_code != 200:
        logger.warning(
            "AI service returned a non-200 status (reason=http_error, status=%s)",
            response.status_code,
        )
        raise AIServiceError("http_error", status_code=response.status_code)

    try:
        data = response.json()
    except ValueError:
        logger.warning("AI service returned non-JSON (reason=invalid_response)")
        raise AIServiceError("invalid_response")

    try:
        validate_ai_output(data, conversation_id=payload["conversation_id"])
    except AIContractError as exc:
        # Field names only - the violation message never echoes values.
        logger.warning(
            "AI service contract violation (reason=invalid_response, detail=%s)",
            str(exc),
        )
        raise AIServiceError("invalid_response")

    return data
