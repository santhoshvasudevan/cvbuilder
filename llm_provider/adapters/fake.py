"""Deterministic, no-network adapter so downstream milestones (and this app's own tests) can
be exercised without live API keys (docs/IMPLEMENTATION_PLAN.md M2).
"""

from __future__ import annotations

from ..types import NormalizedLLMRequest, NormalizedLLMResult, TokenUsage
from .base import BaseLLMAdapter


class FakeAdapter(BaseLLMAdapter):
    def __init__(
        self,
        llm_model,
        retry_policy=None,
        fixed_response: dict | None = None,
        fixed_usage: TokenUsage | None = None,
    ):
        super().__init__(llm_model, retry_policy)
        self.fixed_response = fixed_response if fixed_response is not None else {}
        self.fixed_usage = fixed_usage or TokenUsage(
            input_tokens=10, cached_input_tokens=0, output_tokens=5, total_tokens=15
        )

    def _call_once(self, request: NormalizedLLMRequest) -> NormalizedLLMResult:
        return NormalizedLLMResult(content=dict(self.fixed_response), usage=self.fixed_usage)
