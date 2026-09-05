"""Typed error taxonomy and sanitization for provider errors (requirements.md Sec 9.5 / LLM-009).

Never let a raw provider exception message or response body reach a log line or a stored DB
field unsanitized -- provider errors can echo request/response content, which may include
candidate data (NFR-003).
"""

from __future__ import annotations

import dataclasses
import enum
import re


class LLMErrorCategory(str, enum.Enum):
    CONFIGURATION = "CONFIGURATION"
    AUTH = "AUTH"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    SCHEMA_VALIDATION = "SCHEMA_VALIDATION"
    PROVIDER_INTERNAL = "PROVIDER_INTERNAL"


# Retry only on transient failures (LLM-008) -- never on configuration, auth, or
# schema-validation failures, since retrying those just repeats the same failure.
TRANSIENT_ERROR_CATEGORIES = frozenset(
    {
        LLMErrorCategory.RATE_LIMIT,
        LLMErrorCategory.TIMEOUT,
        LLMErrorCategory.PROVIDER_INTERNAL,
    }
)

_MAX_SANITIZED_MESSAGE_LENGTH = 200
_BODY_LIKE_PATTERN = re.compile(r"[{\[].*[}\]]", re.DOTALL)


def sanitize_error_message(raw: str) -> str:
    """Strip anything resembling a raw JSON/dict request or response body, then truncate.

    This does not try to enumerate every way a provider exception could leak request/response
    content -- it collapses whitespace and redacts anything body-shaped, which is the pattern
    most provider client libraries use when they embed the raw payload in an exception's
    string representation.
    """
    if not raw:
        return ""
    collapsed = re.sub(r"\s+", " ", raw).strip()
    collapsed = _BODY_LIKE_PATTERN.sub("[redacted body]", collapsed)
    if len(collapsed) > _MAX_SANITIZED_MESSAGE_LENGTH:
        collapsed = collapsed[:_MAX_SANITIZED_MESSAGE_LENGTH] + "…[truncated]"
    return collapsed


@dataclasses.dataclass
class NormalizedLLMError:
    category: LLMErrorCategory
    message: str
    partial_output_received: bool = False
    # Sanitized, typed rate-limit diagnostic metadata (2026-09-05, OpenRouter 429 diagnostics) --
    # populated only for RATE_LIMIT errors where the provider adapter could safely extract it from
    # documented, non-secret response headers/metadata. Never the raw response body or headers
    # wholesale -- only a small, fixed set of keys (see `adapters/openrouter.py`'s
    # `parse_openrouter_rate_limit`): `retry_after_seconds`, `limit`, `remaining`, `reset`,
    # `source` (`"upstream"`/`"unknown"`), `upstream_provider` (a provider label, when named).
    # `None` for every non-RATE_LIMIT error and for a RATE_LIMIT error where no such metadata was
    # present in the response.
    rate_limit_diagnostics: dict | None = None

    @classmethod
    def from_exception(
        cls,
        category: LLMErrorCategory,
        exc: Exception,
        *,
        partial_output_received: bool = False,
    ) -> "NormalizedLLMError":
        return cls(
            category=category,
            message=sanitize_error_message(str(exc)),
            partial_output_received=partial_output_received,
        )
