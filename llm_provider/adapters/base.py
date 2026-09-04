"""Shared call path every provider adapter goes through (docs/ARCHITECTURE.md Sec 3).

Concrete adapters implement `_call_once` only, returning raw (not-yet-validated) dict content
on success. `generate()` uniformly handles retry, re-validation against the canonical Pydantic
schema (D-005), latency measurement, and audit logging (LLM-010) -- so no adapter subclass has
to remember to do any of that itself.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from pydantic import ValidationError

from ..errors import LLMErrorCategory, NormalizedLLMError, sanitize_error_message
from ..models import LLMCallLog, LLMModel
from ..retry import RetryPolicy, execute_with_retry
from ..types import NormalizedLLMRequest, NormalizedLLMResult, TokenUsage

# The one canonical fallback used when neither a stage-specific budget
# (`StageModelAssignment.max_output_tokens`) nor a model capability (`LLMModel.max_output_tokens`)
# is configured (2026-09-04, stage-specific token budgets). Every pipeline service must obtain its
# effective request budget from `adapter.effective_max_output_tokens` (set here, and possibly
# overridden by `get_adapter_for_stage` -- see `llm_provider/adapters/__init__.py`) rather than
# hard-coding its own fallback constant.
DEFAULT_MAX_OUTPUT_TOKENS = 4096


class BaseLLMAdapter(ABC):
    def __init__(self, llm_model: LLMModel, retry_policy: RetryPolicy | None = None):
        self.llm_model = llm_model
        self.retry_policy = retry_policy or RetryPolicy()
        # Sensible per-model default, available even when an adapter is constructed directly
        # (e.g. `FakeAdapter(model, ...)` in tests) rather than through `get_adapter_for_stage` --
        # `get_adapter_for_stage` overrides this afterward only when the stage's own
        # `StageModelAssignment.max_output_tokens` is explicitly configured.
        self.effective_max_output_tokens = llm_model.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS

    @abstractmethod
    def _call_once(self, request: NormalizedLLMRequest) -> NormalizedLLMResult:
        """Perform exactly one provider call attempt.

        On success, `result.content` must be the *raw* parsed dict from the provider response
        -- not yet validated against `request.output_schema`. Validation happens once,
        uniformly, in `generate()` below.
        """
        raise NotImplementedError

    def generate(self, request: NormalizedLLMRequest) -> NormalizedLLMResult:
        start = time.monotonic()
        result = execute_with_retry(lambda: self._call_once(request), policy=self.retry_policy)
        result.latency_ms = (time.monotonic() - start) * 1000

        if not result.is_error and isinstance(result.content, dict):
            # D-005: "the returned provider result is validated again against the canonical
            # Pydantic model before becoming trusted application data."
            try:
                validated = request.output_schema.model_validate(result.content)
            except ValidationError as exc:
                result = NormalizedLLMResult(
                    error=NormalizedLLMError(
                        category=LLMErrorCategory.SCHEMA_VALIDATION,
                        message=sanitize_error_message(str(exc)),
                    ),
                    retry_count=result.retry_count,
                    latency_ms=result.latency_ms,
                )
            else:
                result.content = validated

        self._write_call_log(request, result)
        return result

    def _write_call_log(self, request: NormalizedLLMRequest, result: NormalizedLLMResult) -> None:
        usage = result.usage or TokenUsage()
        LLMCallLog.objects.create(
            provider=self.llm_model.provider,
            model=self.llm_model,
            stage=request.stage,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            latency_ms=int(result.latency_ms) if result.latency_ms is not None else None,
            retry_count=result.retry_count,
            error_category=result.error.category.value if result.error else "",
            error_message=result.error.message if result.error else "",
        )
