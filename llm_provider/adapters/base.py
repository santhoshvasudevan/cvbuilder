"""Shared call path every provider adapter goes through (docs/ARCHITECTURE.md Section 12).

Concrete adapters implement `_call_once` only, returning raw (not-yet-validated) dict content on
success. `generate()` uniformly handles pre-flight configuration validation (fails before any
HTTP attempt), retry, re-validation against the canonical Pydantic schema, latency measurement,
and audit logging -- so no adapter subclass has to remember to do any of that itself, and no
adapter can accidentally skip the "fails before HTTP" checks by being called directly.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from pydantic import ValidationError

from ..errors import ConfigurationError, LLMErrorCategory, NormalizedLLMError, sanitize_error_message
from ..models import LLMCallLog, LLMModel
from ..retry import RetryPolicy, execute_with_retry
from ..types import NormalizedLLMRequest, NormalizedLLMResult, TokenUsage
from ..validation import validate_call_configuration


class BaseLLMAdapter(ABC):
    def __init__(
        self,
        llm_model: LLMModel,
        retry_policy: RetryPolicy | None = None,
        *,
        stage_run=None,
    ):
        self.llm_model = llm_model
        self.retry_policy = retry_policy or RetryPolicy()
        # Optional job_applications.StageRun correlation (V2-D022) -- None for standalone/manual
        # smoke-test calls not tied to any StageRun.
        self.stage_run = stage_run

    @abstractmethod
    def _call_once(self, request: NormalizedLLMRequest) -> NormalizedLLMResult:
        """Perform exactly one provider call attempt.

        On success, `result.content` must be the *raw* parsed dict from the provider response --
        not yet validated against `request.output_schema`. Validation happens once, uniformly,
        in `generate()` below.
        """
        raise NotImplementedError

    def generate(self, request: NormalizedLLMRequest) -> NormalizedLLMResult:
        try:
            validate_call_configuration(
                provider=self.llm_model.provider,
                model=self.llm_model,
                reasoning_level=request.reasoning_level,
                max_output_tokens=request.max_output_tokens,
                temperature=request.temperature,
            )
        except ConfigurationError as exc:
            result = NormalizedLLMResult(
                error=NormalizedLLMError(category=LLMErrorCategory.CONFIGURATION, message=str(exc))
            )
            self._write_call_log(request, result)
            return result

        start = time.monotonic()
        result = execute_with_retry(lambda: self._call_once(request), policy=self.retry_policy)
        result.latency_ms = (time.monotonic() - start) * 1000

        if not result.is_error and isinstance(result.content, dict):
            # The returned provider result is validated again against the canonical Pydantic
            # model before becoming trusted application data.
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
            stage_run=self.stage_run,
            stage=request.stage,
            requested_provider=self.llm_model.provider,
            requested_model=self.llm_model,
            resolved_model_identifier=result.resolved_model_identifier,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            latency_ms=int(result.latency_ms) if result.latency_ms is not None else None,
            retry_count=result.retry_count,
            finish_reason=result.finish_reason,
            error_category=result.error.category.value if result.error else "",
            error_message=result.error.message if result.error else "",
        )
