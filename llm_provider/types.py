"""Normalized request/result types every provider adapter speaks (docs/ARCHITECTURE.md Sec 3.1/3.2).

Pipeline code (the four agent apps, from Milestone M3 onward) only ever sees these normalized
shapes -- it never sees a raw provider response object and never imports a provider SDK.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic import BaseModel

from .errors import NormalizedLLMError


@dataclasses.dataclass
class NormalizedLLMRequest:
    stage: str
    messages: list[dict[str, str]]
    output_schema: type[BaseModel]
    temperature: float = 0.0
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None


@dataclasses.dataclass
class TokenUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclasses.dataclass
class NormalizedLLMResult:
    """Either `content` (once validated, a real instance of the request's `output_schema`;
    before validation, a raw dict) or `error` is set -- never both, never neither."""

    content: BaseModel | dict[str, Any] | None = None
    usage: TokenUsage | None = None
    error: NormalizedLLMError | None = None
    latency_ms: float | None = None
    retry_count: int = 0

    @property
    def is_error(self) -> bool:
        return self.error is not None
