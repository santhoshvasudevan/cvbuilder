"""Typed error taxonomy and sanitization for provider errors (requirements.md LLM-012).

Never let a raw provider exception message or response body reach a log line or a stored DB
field unsanitized -- provider errors can echo request/response content, which may include
candidate data. See also `docs/ENGINEERING_RULES.md` Section G.
"""

from __future__ import annotations

import dataclasses
import enum
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


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

# Case-insensitive parameter/field names that must never survive sanitization, wherever they
# appear -- a URL query string, a bare "key=value"-shaped fragment copied into an exception
# message, or a "Name: value" header-shaped fragment (V2-D043). This is a defence-in-depth
# backstop: adapters are also expected to never place a credential in a URL/exception-visible
# string in the first place (see llm_provider.adapters.gemini), but this function must still be
# safe on its own if one ever does, intentionally or by a future regression.
_SENSITIVE_PARAM_NAMES = frozenset(
    {
        "key",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "auth",
        "authorization",
        "password",
        "secret",
        "signature",
    }
)

# Matches a URL anywhere in free text (with or without a scheme -- exception messages from
# `requests`/`urllib3` frequently render only the path+query, e.g. "url: /v1/x?key=...").
_URL_PATTERN = re.compile(r"(?:https?://\S+|/\S*\?\S+)", re.IGNORECASE)

# Matches "<sensitive-name>[=:] <value>" outside of a recognizable URL -- e.g. a stray
# "api_key=..." fragment, or a "password: ..." fragment in free-form diagnostic text. The value
# group optionally swallows a leading "Bearer "/"Basic " auth scheme first (so
# "Authorization: Bearer <token>" redacts as one unit, rather than leaving "Bearer" behind for
# _AUTH_SCHEME_PATTERN to (potentially) miss because this pattern already consumed it).
_INLINE_PARAM_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(re.escape(name) for name in _SENSITIVE_PARAM_NAMES) + r")"
    r"(\s*[:=]\s*)((?:(?:bearer|basic)\s+)?[^\s&'\")\]}]+)"
)

# Matches a standalone "Bearer <token>" / "Basic <token>" auth-scheme credential not preceded by
# one of the sensitive param names above (e.g. no literal "Authorization:" in the text).
_AUTH_SCHEME_PATTERN = re.compile(r"(?i)\b(bearer|basic)\s+([A-Za-z0-9\-_.~+/=]+)")

# Matches URL user-info -- an https://<name>:<secret>@host-shaped prefix.
_URL_USERINFO_PATTERN = re.compile(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@")

# Plain-word marker -- deliberately has no "[" / "{" so it can never be re-matched (and mangled)
# by `_BODY_LIKE_PATTERN` below, and no URL-unsafe characters so it survives `urlencode` as-is.
_REDACTED = "REDACTED"


def _redact_query_string(query: str) -> str:
    if not query:
        return query
    pairs = parse_qsl(query, keep_blank_values=True)
    if not pairs:
        # Not a well-formed "a=b&c=d" query string (e.g. a bare "?token" or garbage) -- redact
        # it wholesale rather than risk echoing it unredacted.
        return _REDACTED if any(name in query.lower() for name in _SENSITIVE_PARAM_NAMES) else query
    redacted = [
        (name, _REDACTED if name.lower() in _SENSITIVE_PARAM_NAMES else value) for name, value in pairs
    ]
    return urlencode(redacted)


def _redact_url(match: re.Match) -> str:
    url = match.group(0)
    # Regex greediness can pull in trailing prose punctuation (")", closing quote, etc.) that is
    # not actually part of the URL -- strip it back off before parsing, then reattach untouched.
    trailing = ""
    while url and url[-1] in ").,;:'\"":
        trailing = url[-1] + trailing
        url = url[:-1]
    try:
        parts = urlsplit(url)
    except ValueError:
        return match.group(0)
    query = _redact_query_string(parts.query)
    rebuilt = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    return rebuilt + trailing


def sanitize_error_message(raw: str) -> str:
    """Redact anything resembling a credential, then strip anything resembling a raw JSON/dict
    request or response body, then truncate (LLM-012, docs/ENGINEERING_RULES.md Section G).

    Defence in depth (V2-D043): redacts sensitive URL/query-string parameters, URL-embedded
    user-info, and "Bearer <token>"-shaped fragments *before* any body-shaped or length
    truncation happens, so a credential can never survive by hiding past the truncation cutoff or
    inside a body-like fragment this function otherwise leaves alone.
    """
    if not raw:
        return ""
    collapsed = re.sub(r"\s+", " ", raw).strip()
    collapsed = _URL_USERINFO_PATTERN.sub(rf"\1{_REDACTED}@", collapsed)
    collapsed = _URL_PATTERN.sub(_redact_url, collapsed)
    collapsed = _INLINE_PARAM_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", collapsed)
    collapsed = _AUTH_SCHEME_PATTERN.sub(rf"\1 {_REDACTED}", collapsed)
    collapsed = _BODY_LIKE_PATTERN.sub("[redacted body]", collapsed)
    if len(collapsed) > _MAX_SANITIZED_MESSAGE_LENGTH:
        collapsed = collapsed[:_MAX_SANITIZED_MESSAGE_LENGTH] + "…[truncated]"
    return collapsed


# Exception class names (walked via each exception's MRO, so subclasses are matched without
# importing `requests` here) mapped to a short, safe, provider-agnostic description. Order
# matters: dict iteration below follows MRO order, most-specific class first.
_NETWORK_EXCEPTION_HINTS: dict[str, str] = {
    "SSLError": "TLS/SSL error contacting the provider.",
    "ConnectTimeout": "Timed out establishing a connection to the provider.",
    "ReadTimeout": "Timed out waiting for the provider's response.",
    "Timeout": "Timed out contacting the provider.",
    "ConnectionError": "Connection error contacting the provider.",
}


def classify_network_exception(exc: Exception) -> str:
    """A short, safe, provider-agnostic description of a network-level failure -- deliberately
    never echoes the raw exception text. `requests`/`urllib3` connection-level exceptions
    stringify with the full request URL (and therefore any URL-embedded credential -- see
    llm_provider.adapters.gemini's history, V2-D043); the caller's `LLMErrorCategory`
    (TIMEOUT/PROVIDER_INTERNAL) already carries the retryability signal, this only adds a short
    human-readable classification on top, never operationally-necessary raw detail.
    """
    for cls in type(exc).__mro__:
        hint = _NETWORK_EXCEPTION_HINTS.get(cls.__name__)
        if hint:
            return hint
    return "Network error contacting the provider."


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

    @classmethod
    def from_network_exception(
        cls,
        category: LLMErrorCategory,
        exc: Exception,
        *,
        partial_output_received: bool = False,
    ) -> "NormalizedLLMError":
        """Use for `requests.RequestException`/`requests.Timeout` at an adapter's HTTP call
        boundary specifically (V2-D043): unlike `from_exception`, this never passes the raw
        exception text through `sanitize_error_message`'s redaction -- it uses
        `classify_network_exception` instead, so a credential embedded in the request URL (or any
        other exception-visible request detail) is never even a candidate for the message,
        regardless of how well the redaction regexes keep up with real-world exception shapes.
        """
        return cls(
            category=category,
            message=classify_network_exception(exc),
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


class TemperatureOutOfRangeError(ConfigurationError):
    def __init__(self, temperature: float, minimum: float, maximum: float):
        super().__init__(
            f"temperature={temperature!r} is outside the permitted range [{minimum}, {maximum}]."
        )


class UnsupportedTemperatureError(ConfigurationError):
    def __init__(self, model_label: str):
        super().__init__(f"LLMModel '{model_label}' does not support a temperature parameter.")


class TemperatureReasoningConflictError(ConfigurationError):
    def __init__(self, model_label: str, reasoning_level: str):
        super().__init__(
            f"LLMModel '{model_label}' does not accept a temperature value combined with "
            f"reasoning_level='{reasoning_level}'."
        )
