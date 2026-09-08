"""Deterministic, no-network adapter so this app's own tests (and future milestones' tests) can
be exercised without live API keys (docs/IMPLEMENTATION_PLAN.md M2 acceptance: "fake-adapter
deterministic tests pass"). Never makes an HTTP request.
"""

from __future__ import annotations

from ..types import NormalizedLLMRequest, NormalizedLLMResult, TokenUsage
from .base import BaseLLMAdapter


class FakeAdapter(BaseLLMAdapter):
    def __init__(
        self,
        llm_model,
        retry_policy=None,
        *,
        stage_run=None,
        fixed_response: dict | None = None,
        fixed_usage: TokenUsage | None = None,
        fixed_error: "NormalizedLLMResult | None" = None,
    ):
        super().__init__(llm_model, retry_policy, stage_run=stage_run)
        self.fixed_response = fixed_response if fixed_response is not None else {}
        self.fixed_usage = fixed_usage or TokenUsage(
            input_tokens=10, cached_input_tokens=0, output_tokens=5, total_tokens=15
        )
        self.fixed_error = fixed_error
        self.call_count = 0

    def _call_once(self, request: NormalizedLLMRequest) -> NormalizedLLMResult:
        self.call_count += 1
        if self.fixed_error is not None:
            return self.fixed_error
        return NormalizedLLMResult(
            content=dict(self.fixed_response), usage=self.fixed_usage, finish_reason="stop"
        )
