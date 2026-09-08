"""Retry policy (requirements.md LLM-002..011 provider foundation).

Lives in the adapter layer, not in pipeline code. Retries only fire for errors classified as
transient, and only ever retry the *same* provider/model call -- this module never selects a
different provider or model (no invisible fallback; docs/IMPLEMENTATION_PLAN.md M2 scope). For
streaming calls: retry only if no output has streamed yet for that attempt.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Callable

from .errors import TRANSIENT_ERROR_CATEGORIES
from .types import NormalizedLLMResult


@dataclasses.dataclass
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 1.0


def is_retryable(result: NormalizedLLMResult) -> bool:
    if not result.is_error:
        return False
    if result.error.partial_output_received:
        # Never retry a call that partially streamed -- retrying now would duplicate or
        # garble output.
        return False
    return result.error.category in TRANSIENT_ERROR_CATEGORIES


def execute_with_retry(
    call_once: Callable[[], NormalizedLLMResult],
    policy: RetryPolicy | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> NormalizedLLMResult:
    """Call `call_once` up to `policy.max_attempts` times, stopping as soon as the result is not
    retryable. `result.retry_count` on the returned result is the number of retries actually
    performed (0 if the first attempt succeeded or failed non-retryably)."""
    policy = policy or RetryPolicy()
    attempt = 0
    result: NormalizedLLMResult
    while True:
        attempt += 1
        result = call_once()
        if not is_retryable(result) or attempt >= policy.max_attempts:
            result.retry_count = attempt - 1
            return result
        sleep_fn(policy.backoff_seconds * attempt)
