"""Normalized request/result types every provider adapter speaks (docs/ARCHITECTURE.md Section 12).

Deliberately framework/DB-independent (plain dataclasses + Pydantic only, no Django import) --
pipeline code (from M4 onward) only ever sees these normalized shapes, never a raw provider
response object, and never imports a provider SDK (LLM-001).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic import BaseModel

from .errors import NormalizedLLMError

# Matches llm_provider.models.ReasoningLevel.NONE's value. Not imported directly to keep this
# module Django-independent; llm_provider.validation enforces the real membership check against
# the canonical enum before any call is made.
DEFAULT_REASONING_LEVEL = "NONE"


@dataclasses.dataclass
class NormalizedLLMRequest:
    """Provider-neutral request contract (LLM-007). `output_schema` is the single canonical
    Pydantic schema source (docs/ARCHITECTURE.md Section 12); provider-specific translation
    happens only in `llm_provider.schema_translation` and each adapter's `translate_schema`.
    """

    stage: str
    messages: list[dict[str, str]]
    output_schema: type[BaseModel]
    reasoning_level: str = DEFAULT_REASONING_LEVEL
    temperature: float | None = None
    max_output_tokens: int | None = None


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
    finish_reason: str = ""
    resolved_model_identifier: str = ""

    @property
    def is_error(self) -> bool:
        return self.error is not None
