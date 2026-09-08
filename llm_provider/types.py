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
    # Explicit, typed, provider-agnostic request-level options (not a free-form escape hatch) --
    # each adapter translates the ones it understands into its own provider-specific fields and
    # silently ignores the rest (e.g. Gemini's `thinkingConfig.thinkingBudget` already uses
    # `reasoning_effort`, not `reasoning_enabled`; OpenAI currently reads neither). `None` always
    # means "say nothing, let the provider/model use its own default" -- only an explicit
    # True/False or a set float ever reaches a request body.
    reasoning_enabled: bool | None = None
    top_p: float | None = None
    # Optional caller-supplied correlation/workflow identifier (2026-09-07, requested-vs-resolved
    # audit), carried through verbatim to LLMCallLog.correlation_id when set. `None` (the default)
    # means no pipeline call site currently supplies one -- this is additive audit plumbing, not a
    # requirement for existing callers to change.
    correlation_id: str | None = None


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
    # Requested-vs-resolved model auditing (2026-09-07, OpenRouter Free Router migration): a
    # virtual router such as OpenRouter's `openrouter/free` accepts one requested model id but may
    # route the call to a different underlying model per request. `resolved_model`/`finish_reason`
    # are read only from the provider's own response body when present -- never guessed, never
    # substituted for the requested `LLMModel.model_id` (which the caller/registry/LLMCallLog.model
    # FK already record exactly). Both stay `None` for an error result or when the provider's
    # response shape doesn't report them.
    resolved_model: str | None = None
    finish_reason: str | None = None
    # The primary key of the LLMCallLog row `BaseLLMAdapter._write_call_log` wrote for this call
    # (2026-09-08, M5 staged workflow) -- set unconditionally by `generate()` for both success and
    # error results, since a call log row is always written either way. Lets a caller (e.g.
    # `candidate_matching.services.staged_run.execute_stage`) link its own persisted record to the
    # exact audit row without a separate, racy lookup query.
    call_log_id: int | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None
