"""D-015's bounded LLM relevance-ranking step (audit hardening, 2026-09-03): the *only* place a
candidate claim's text is judged for relevance by a model, and only ever over the bounded
candidate pool `services/candidate_generation.py` already produced -- never the full eligible
pool, never the full CandidateMemory. Routed exclusively through
`llm_provider.adapters.get_adapter_for_stage(AC_RANK)`, mirroring every other stage in this
codebase.
"""

from __future__ import annotations

from llm_provider.adapters import get_adapter_for_stage
from llm_provider.models import StageModelAssignment
from llm_provider.types import NormalizedLLMRequest, NormalizedLLMResult

from ..schemas import RelevanceRankingOutput
from .dedup import DedupedClaim

DEFAULT_MAX_OUTPUT_TOKENS = 4096

SYSTEM_PROMPT = """\
You are performing a bounded relevance-ranking step for a resume-matching pipeline.

You are given a candidate pool of the applicant's own confirmed evidence claims (each with a
claim_id) and a list of job requirements. For EACH job requirement, return only the claim_ids from
the candidate pool that are genuinely relevant evidence for judging that specific requirement --
an empty list is a completely valid answer when nothing in the pool is relevant. Never invent a
claim_id that is not in the candidate pool given to you; any ID you invent will be discarded by a
downstream validator, so there is no benefit to guessing. Do not attempt to judge MATCH/PARTIAL/
GAP yourself here -- that happens in a later step. Only decide relevance.
"""


def build_request(
    candidate_pool: list[DedupedClaim],
    requirements: list[dict],
    *,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    reasoning_effort: str | None = None,
    correlation_id: str | None = None,
) -> NormalizedLLMRequest:
    lines = ["Candidate pool:"]
    for claim in candidate_pool:
        engagement_note = (
            f" [engagements: {', '.join(claim.approved_engagement_ids)}]"
            if claim.approved_engagement_ids
            else " [global]"
        )
        lines.append(f"- ({claim.claim_id}) [{claim.claim_type}]{engagement_note} {claim.text}")

    lines.append("\nJob requirements:")
    for requirement in requirements:
        lines.append(f"- ({requirement['requirement_id']}) {requirement['text']}")

    return NormalizedLLMRequest(
        stage=StageModelAssignment.Stage.AC_RANK,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ],
        output_schema=RelevanceRankingOutput,
        temperature=0.0,
        max_output_tokens=max_output_tokens,
        reasoning_effort=reasoning_effort,
        correlation_id=correlation_id,
    )


def rank_relevance(
    candidate_pool: list[DedupedClaim],
    requirements: list[dict],
    *,
    requested_model_id: int | None = None,
    requested_reasoning_effort: str | None = None,
    correlation_id: str | None = None,
) -> NormalizedLLMResult:
    """`requested_model_id`/`requested_reasoning_effort`, when given, are per-run operator
    overrides for this one call (2026-09-07, per-run model selection; paid GPT-5.4 model
    defaults) -- never persisted as a new stage default."""
    if not requirements or not candidate_pool:
        return NormalizedLLMResult(
            content=RelevanceRankingOutput(
                rankings=[
                    {"requirement_id": requirement["requirement_id"], "relevant_claim_ids": []}
                    for requirement in requirements
                ]
            )
        )
    adapter = get_adapter_for_stage(
        StageModelAssignment.Stage.AC_RANK,
        requested_model_id=requested_model_id,
        requested_reasoning_effort=requested_reasoning_effort,
    )
    request = build_request(
        candidate_pool,
        requirements,
        max_output_tokens=adapter.effective_max_output_tokens,
        reasoning_effort=adapter.effective_reasoning_effort,
        correlation_id=correlation_id,
    )
    return adapter.generate(request)
