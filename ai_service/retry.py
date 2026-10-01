import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from errors import ProviderError

T = TypeVar("T")

# Failures worth retrying: declared provider failures and neutral timeouts.
RETRYABLE_EXCEPTIONS = (ProviderError, TimeoutError)


@dataclass(frozen=True)
class RetryPolicy:
    """Provider-neutral retry configuration."""

    timeout_seconds: float = 30.0
    retry_count: int = 1  # retries after the first attempt (SoT: retry once)
    backoff_seconds: float = 0.5

    def backoff_for(self, retry_number: int) -> float:
        """Exponential backoff: 1st retry = base, 2nd = base*2, ..."""
        return self.backoff_seconds * (2 ** (retry_number - 1))


DEFAULT_RETRY_POLICY = RetryPolicy()


def run_with_retry(
    operation: Callable[[], T],
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], None] = time.sleep,
) -> T:

    retries_used = 0
    while True:
        try:
            return operation()
        except RETRYABLE_EXCEPTIONS:
            if retries_used >= policy.retry_count:
                raise
            retries_used += 1
            sleep(policy.backoff_for(retries_used))
