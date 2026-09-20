"""Redaction used before any provider-derived data reaches durable logs."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"
SENSITIVE_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "private_key",
        "cookie",
        "key",
        "signature",
        "database_url",
        "pgpassword",
    }
)
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|credential|password|client[_-]?secret|secret|"
    r"database[_-]?url|pgpassword|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|token|private[_-]?key|cookie)"
    r"[\"']?\s*[:=]\s*([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_URL_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?key|key|token|access_token|refresh_token|id_token|secret|signature)=)"
    r"[^&#\s]+"
)


def _sensitive_name(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    return normalized in SENSITIVE_NAMES or normalized.endswith(
        ("_token", "_secret", "_password", "_api_key", "_private_key")
    )


def redact_text(value: str, *, max_length: int = 4000) -> str:
    collapsed = value.replace("\x00", "")
    collapsed = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}={REDACTED}", collapsed)
    collapsed = _BEARER.sub(lambda match: f"{match.group(1)} {REDACTED}", collapsed)
    collapsed = _URL_SECRET.sub(lambda match: f"{match.group(1)}{REDACTED}", collapsed)
    if len(collapsed) > max_length:
        return collapsed[:max_length] + "…[TRUNCATED]"
    return collapsed


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[key] = REDACTED if _sensitive_name(str(key)) else redact(item)
        return result
    return value
