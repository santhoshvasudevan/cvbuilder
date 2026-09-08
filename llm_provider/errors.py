"""Typed error taxonomy and sanitization for provider errors (requirements.md LLM-012).

Never let a raw provider exception message or response body reach a log line or a stored DB
field unsanitized -- provider errors can echo request/response content, which may include
candidate data. See also `docs/ENGINEERING_RULES.md` Section G.
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


# Retry only on transient failures -- never on configuration, auth, or schema-validation
# failures, since retrying those just repeats the same failure (no invisible fallback: this
# retries the *same* provider/model, never a different one).
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
    """Strip anything resembling a raw JSON/dict request or response body, then truncate."""
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


class ConfigurationError(Exception):
    """Base class for every pre-flight ('fails before HTTP') configuration validation error --
    see `llm_provider.validation`. Raised by routing/validation code, before any adapter is
    constructed or any HTTP call is attempted.
    """

    category = LLMErrorCategory.CONFIGURATION


class InactiveProviderError(ConfigurationError):
    def __init__(self, provider_name: str):
        super().__init__(f"LLMProvider '{provider_name}' is not enabled.")


class InactiveModelError(ConfigurationError):
    def __init__(self, model_label: str):
        super().__init__(f"LLMModel '{model_label}' is not enabled.")


class MissingCredentialConfigurationError(ConfigurationError):
    def __init__(self, provider_name: str):
        super().__init__(
            f"LLMProvider '{provider_name}' has no credential_env_variable configured."
        )


class MissingCredentialValueError(ConfigurationError):
    def __init__(self, provider_name: str, env_var: str):
        super().__init__(
            f"LLMProvider '{provider_name}' references environment variable '{env_var}', but it "
            "is not set in the current environment."
        )


class UnsupportedStructuredOutputError(ConfigurationError):
    def __init__(self, model_label: str):
        super().__init__(f"LLMModel '{model_label}' does not support structured output.")


class UnsupportedReasoningLevelError(ConfigurationError):
    def __init__(self, model_label: str, requested_level: str):
        super().__init__(
            f"Reasoning level '{requested_level}' is not supported by LLMModel '{model_label}'."
        )


class StageBudgetExceededError(ConfigurationError):
    def __init__(self, model_label: str, requested: int, capability: int):
        super().__init__(
            f"Requested max_output_tokens={requested} exceeds LLMModel '{model_label}' "
            f"capability of {capability}."
        )
