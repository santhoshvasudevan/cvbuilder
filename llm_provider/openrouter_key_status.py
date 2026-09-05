"""Explicit, operator-triggered OpenRouter key-status lookup (2026-09-05, D-029).

`GET /api/v1/key` reports non-secret, documented quota/limit fields for the calling API key --
never the key value itself. This is deliberately a **separate, standalone** service, never wired
into the inference retry loop (`llm_provider/retry.py`) or any adapter's `_call_once` -- it is only
ever invoked by an explicit operator action (the "Refresh OpenRouter status" admin view action).

Deliberately does **not** use `/api/v1/credits` -- that endpoint requires a separate OpenRouter
*management* key, which this application never requests or stores (only the existing inference
credential, referenced by `LLMProvider.credential_env_var`, is ever used here).
"""

from __future__ import annotations

import dataclasses
import os

import requests

from .errors import sanitize_error_message
from .models import LLMProvider

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Short and bounded -- this is a lightweight metadata lookup, never a generation call, so it must
# never be allowed to hang anywhere near as long as an inference request's own timeout budget.
_KEY_STATUS_TIMEOUT = (5, 10)

_MAX_LABEL_LENGTH = 200
_MAX_RESET_LENGTH = 50
_MAX_ERROR_MESSAGE_LENGTH = 300


@dataclasses.dataclass
class OpenRouterKeyStatusResult:
    """Result of one key-status lookup attempt. `error_message` is always sanitized and is the
    only text ever safe to show the operator or persist -- never a raw exception string or
    response body. The credential value itself never appears on this object."""

    success: bool
    label: str | None = None
    is_free_tier: bool | None = None
    limit: float | None = None
    limit_remaining: float | None = None
    limit_reset: str | None = None
    usage: float | None = None
    usage_daily: float | None = None
    usage_weekly: float | None = None
    usage_monthly: float | None = None
    error_message: str = ""


def _safe_str(value: object, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped[:max_length] if stripped else None


def _safe_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _safe_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def fetch_openrouter_key_status(provider: LLMProvider) -> OpenRouterKeyStatusResult:
    """Perform exactly one `GET /key` call against `provider`'s configured OpenRouter credential.
    Never raises for an expected failure mode (missing credential, timeout, network error,
    non-2xx status, malformed payload) -- every such case returns
    `OpenRouterKeyStatusResult(success=False, error_message=<sanitized>)` instead, so a caller
    (the admin "Refresh" action) never has to guess which exceptions are possible."""
    if provider.provider_type != LLMProvider.ProviderType.OPENROUTER:
        return OpenRouterKeyStatusResult(
            success=False, error_message="This provider is not an OpenRouter provider."
        )

    api_key = os.environ.get(provider.credential_env_var, "") if provider.credential_env_var else ""
    if not api_key:
        return OpenRouterKeyStatusResult(
            success=False,
            error_message=(
                f"No credential found in environment variable "
                f"'{provider.credential_env_var or '(not configured)'}'."
            ),
        )

    base_url = provider.base_url or DEFAULT_BASE_URL
    try:
        response = requests.get(
            f"{base_url}/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_KEY_STATUS_TIMEOUT,
        )
    except requests.Timeout:
        return OpenRouterKeyStatusResult(
            success=False, error_message="Timed out contacting the OpenRouter key-status endpoint."
        )
    except requests.RequestException as exc:
        return OpenRouterKeyStatusResult(
            success=False, error_message=sanitize_error_message(str(exc))[:_MAX_ERROR_MESSAGE_LENGTH]
        )

    if response.status_code in (401, 403):
        return OpenRouterKeyStatusResult(success=False, error_message="Authentication failed.")
    if response.status_code >= 400:
        return OpenRouterKeyStatusResult(
            success=False,
            error_message=f"OpenRouter key-status endpoint returned HTTP {response.status_code}.",
        )

    try:
        payload = response.json()
    except ValueError:
        return OpenRouterKeyStatusResult(
            success=False,
            error_message="Malformed (non-JSON) response from the OpenRouter key-status endpoint.",
        )

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return OpenRouterKeyStatusResult(
            success=False,
            error_message="Unexpected response shape from the OpenRouter key-status endpoint.",
        )

    return OpenRouterKeyStatusResult(
        success=True,
        label=_safe_str(data.get("label"), _MAX_LABEL_LENGTH),
        is_free_tier=_safe_bool(data.get("is_free_tier")),
        limit=_safe_number(data.get("limit")),
        limit_remaining=_safe_number(data.get("limit_remaining")),
        limit_reset=_safe_str(data.get("limit_reset"), _MAX_RESET_LENGTH),
        usage=_safe_number(data.get("usage")),
        usage_daily=_safe_number(data.get("usage_daily")),
        usage_weekly=_safe_number(data.get("usage_weekly")),
        usage_monthly=_safe_number(data.get("usage_monthly")),
    )
