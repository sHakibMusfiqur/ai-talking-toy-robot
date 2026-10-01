import os
from dataclasses import dataclass
from typing import Mapping

from retry import RetryPolicy

COMPONENTS = ("stt", "llm", "tts")

DEFAULT_PROVIDER = "mock"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRY_COUNT = 1
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5


@dataclass(frozen=True)
class ProviderSettings:
    component: str
    provider: str
    model: str | None
    endpoint: str | None
    timeout_seconds: float
    retry_count: int
    retry_backoff_seconds: float

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            timeout_seconds=self.timeout_seconds,
            retry_count=self.retry_count,
            backoff_seconds=self.retry_backoff_seconds,
        )


def _number(raw: str | None, key: str, cast, minimum: float | None = None):
    if raw is None or not raw.strip():
        return None
    try:
        value = cast(raw.strip())
    except ValueError:
        # Key name only - never echo raw environment values.
        raise ValueError(f"invalid numeric configuration for {key}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{key} must be >= {minimum}")
    return value


def load_provider_settings(
    component: str, env: Mapping[str, str] | None = None
) -> ProviderSettings:
 
    component = component.lower()
    if component not in COMPONENTS:
        raise ValueError(f"unknown provider component: {component}")
    env = os.environ if env is None else env
    prefix = f"AI_{component.upper()}"

    provider = (env.get(f"{prefix}_PROVIDER") or "").strip() or DEFAULT_PROVIDER
    model = (env.get(f"{prefix}_MODEL") or "").strip() or None
    endpoint = (env.get(f"{prefix}_ENDPOINT") or "").strip() or None

    timeout = _number(env.get(f"{prefix}_TIMEOUT_SECONDS"), f"{prefix}_TIMEOUT_SECONDS", float, 0)
    if timeout is None:
        timeout = DEFAULT_TIMEOUT_SECONDS
    retry_count = _number(env.get(f"{prefix}_RETRY_COUNT"), f"{prefix}_RETRY_COUNT", int, 0)
    if retry_count is None:
        retry_count = DEFAULT_RETRY_COUNT
    backoff = _number(
        env.get(f"{prefix}_RETRY_BACKOFF_SECONDS"), f"{prefix}_RETRY_BACKOFF_SECONDS", float, 0
    )
    if backoff is None:
        backoff = DEFAULT_RETRY_BACKOFF_SECONDS

    return ProviderSettings(
        component=component,
        provider=provider,
        model=model,
        endpoint=endpoint,
        timeout_seconds=timeout,
        retry_count=retry_count,
        retry_backoff_seconds=backoff,
    )
