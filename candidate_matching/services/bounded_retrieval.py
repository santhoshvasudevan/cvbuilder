"""The full bounded retrieval pipeline (audit hardening, 2026-09-03; D-015): eligibility ->
deduplication -> per-requirement candidate generation -> the D-015 relevance-ranking step ->
capped final selection -> an inspectable manifest. This is what `services/fit_assessment.py`
(M5) actually calls -- never `retrieve.retrieve_eligible_pool()` directly for a provider-bound
context.

Fails closed, never falls back to the full CandidateMemory: a ranking-adapter error, a malformed/
truncated ranking response, or a token budget still exceeded after every count cap has already
been applied all raise an exception here rather than silently widening the context sent to a
later stage.
"""

from __future__ import annotations

import dataclasses

from candidate_memory.models import CandidateMemory
from llm_provider.models import StageModelAssignment

from .candidate_generation import RequirementCandidates, generate_candidates, union_candidate_pool
from .dedup import DedupedClaim, deduplicate_claims
from .normalization_limits import (
    MAX_DIAGNOSTIC_TERMS,
    MAX_EQUIVALENTS,
    MAX_NORMALIZATION_ITEMS,
    MAX_PRESERVED_TERMS,
)
from .normalize import build_search_text, expand_requirements_for_search
from .rank import rank_relevance
from .retrieval_limits import (
    MAX_CANDIDATES_PER_REQUIREMENT,
    MAX_ESTIMATED_REQUEST_TOKENS,
    MAX_RANKING_CANDIDATES,
    MAX_RULES,
    MAX_SELECTED_CLAIMS,
    MIN_CANDIDATES_PER_REQUIREMENT,
    RetrievalBudgetExceededError,
    estimate_tokens,
)
from .retrieve import RetrievalContext, RetrievedClaim, retrieve_eligible_pool
from .rule_selection import select_bounded_rules


class RankingFailedError(Exception):
    """The D-015 ranking step failed (provider error) or returned malformed/unusable output.
    Raised instead of ever falling back to sending the unbounded eligible pool."""


@dataclasses.dataclass(frozen=True)
class RetrievalManifest:
    eligible_count: int
    duplicate_count: int
    candidate_pool_count: int
    selected_count: int
    excluded_counts: dict[str, int]
    per_requirement_selected_claim_ids: dict[str, list[str]]
    rules_selected_count: int
    rules_duplicate_count: int
    rules_excluded_positioning_count: int
    estimated_request_tokens: int
    caps: dict[str, int]
    # AC_NORMALIZE (2026-09-04 recall repair, D-015/D-020): the bounded requirement-normalization
    # stage's output per requirement, inspectable but never used as evidence -- original_text and
    # canonical_english_text plus the three bounded term lists that fed candidate_generation.py's
    # scoring alongside (never in place of) the requirement's own text.
    requirement_normalization: dict[str, dict]

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _cap_ranking_pool(
    candidate_pool: list[DedupedClaim], per_requirement: list[RequirementCandidates]
) -> tuple[list[DedupedClaim], int]:
    if len(candidate_pool) <= MAX_RANKING_CANDIDATES:
        return candidate_pool, 0
    frequency: dict[str, int] = {}
    for group in per_requirement:
        for claim in group.candidates:
            frequency[claim.claim_id] = frequency.get(claim.claim_id, 0) + 1
    ranked = sorted(candidate_pool, key=lambda c: (-frequency.get(c.claim_id, 0), c.claim_id))
    kept = ranked[:MAX_RANKING_CANDIDATES]
    kept.sort(key=lambda c: c.claim_id)
    return kept, len(candidate_pool) - len(kept)


def _cap_selected(
    selected_by_requirement: dict[str, list[str]],
) -> tuple[set[str], int]:
    frequency: dict[str, int] = {}
    for ids in selected_by_requirement.values():
        for claim_id in ids:
            frequency[claim_id] = frequency.get(claim_id, 0) + 1
    all_ids = sorted(frequency)
    if len(all_ids) <= MAX_SELECTED_CLAIMS:
        return set(all_ids), 0
    ranked = sorted(all_ids, key=lambda cid: (-frequency[cid], cid))
    kept = set(ranked[:MAX_SELECTED_CLAIMS])
    return kept, len(all_ids) - len(kept)


def build_bounded_context(
    candidate_memory: CandidateMemory,
    requirements: list[dict],
    *,
    posting_language: str = "en",
    requested_models: dict[str, int] | None = None,
    requested_reasoning_efforts: dict[str, str] | None = None,
    correlation_id: str | None = None,
) -> tuple[RetrievalContext, RetrievalManifest]:
    """`requested_models`/`requested_reasoning_efforts`, when given, are per-run operator override
    maps keyed by `StageModelAssignment.Stage` value (2026-09-07, per-run model selection; paid
    GPT-5.4 model defaults) -- only `AC_NORMALIZE`/`AC_RANK` keys are consulted here (the two
    LLM-backed stages this function itself calls); an absent or `None` key resolves through that
    stage's configured `StageModelAssignment` (model and `default_reasoning_effort` respectively),
    exactly as before. Never persisted as a new stage default."""
    requested_models = requested_models or {}
    requested_reasoning_efforts = requested_reasoning_efforts or {}
    eligible = retrieve_eligible_pool(candidate_memory)
    deduped = deduplicate_claims(eligible.claims)
    duplicate_count = len(eligible.claims) - len(deduped)

    excluded_counts: dict[str, int] = {}
    normalization_manifest: dict[str, dict] = {}

    if not requirements:
        per_requirement: list[RequirementCandidates] = []
        candidate_pool: list[DedupedClaim] = []
        selected_by_requirement: dict[str, list[str]] = {}
    else:
        # AC_NORMALIZE receives only requirement_id/text plus the shared posting language --
        # never CandidateMemory claims, engagements, or any candidate/employment data (see
        # `normalize.py` module docstring for the boundary this enforces).
        normalization_requirements = [
            {"requirement_id": r["requirement_id"], "text": r["text"]} for r in requirements
        ]
        normalization_by_id = expand_requirements_for_search(
            normalization_requirements,
            posting_language=posting_language,
            requested_model_id=requested_models.get(StageModelAssignment.Stage.AC_NORMALIZE),
            requested_reasoning_effort=requested_reasoning_efforts.get(
                StageModelAssignment.Stage.AC_NORMALIZE
            ),
            correlation_id=correlation_id,
        )

        search_requirements = []
        for requirement in requirements:
            requirement_id = requirement["requirement_id"]
            normalization = normalization_by_id[requirement_id]
            search_requirements.append(
                {
                    "requirement_id": requirement_id,
                    "text": build_search_text(requirement["text"], normalization),
                }
            )
            normalization_manifest[requirement_id] = {
                "original_text": requirement["text"],
                "canonical_english_text": normalization.canonical_english_text,
                "diagnostic_terms": list(normalization.diagnostic_terms),
                "equivalents": list(normalization.equivalents),
                "preserved_technical_terms": list(normalization.preserved_technical_terms),
                "source_language": normalization.source_language,
            }

        per_requirement = generate_candidates(search_requirements, deduped)
        raw_pool = union_candidate_pool(per_requirement)
        candidate_pool, pool_cap_excluded = _cap_ranking_pool(raw_pool, per_requirement)
        if pool_cap_excluded:
            excluded_counts["ranking_pool_cap"] = pool_cap_excluded
        candidate_pool_ids = {claim.claim_id for claim in candidate_pool}

        ranking_result = rank_relevance(
            candidate_pool,
            requirements,
            requested_model_id=requested_models.get(StageModelAssignment.Stage.AC_RANK),
            requested_reasoning_effort=requested_reasoning_efforts.get(StageModelAssignment.Stage.AC_RANK),
            correlation_id=correlation_id,
        )
        if ranking_result.is_error:
            raise RankingFailedError(
                f"D-015 relevance-ranking step failed: {ranking_result.error.message}"
            )
        ranking_output = ranking_result.content
        if ranking_output is None or not hasattr(ranking_output, "rankings"):
            raise RankingFailedError("D-015 relevance-ranking step returned unusable output.")

        ranking_by_requirement = {item.requirement_id: item for item in ranking_output.rankings}
        selected_by_requirement = {}
        unknown_id_count = 0
        missing_requirement_count = 0
        for requirement in requirements:
            requirement_id = requirement["requirement_id"]
            item = ranking_by_requirement.get(requirement_id)
            if item is None:
                # Explicit empty result -- the ranking step simply never addressed this
                # requirement. Never treated as "everything is relevant" nor silently omitted
                # from the manifest.
                missing_requirement_count += 1
                selected_by_requirement[requirement_id] = []
                continue
            valid_ids = [cid for cid in item.relevant_claim_ids if cid in candidate_pool_ids]
            unknown_id_count += len(item.relevant_claim_ids) - len(valid_ids)
            selected_by_requirement[requirement_id] = valid_ids
        if unknown_id_count:
            excluded_counts["ranking_fabricated_ids"] = unknown_id_count
        if missing_requirement_count:
            excluded_counts["ranking_missing_requirement"] = missing_requirement_count

    selected_ids, selected_cap_excluded = _cap_selected(selected_by_requirement)
    if selected_cap_excluded:
        excluded_counts["selected_claims_cap"] = selected_cap_excluded

    by_id = {claim.claim_id: claim for claim in deduped}
    final_claims = [
        RetrievedClaim(
            claim_id=claim.claim_id,
            text=claim.text,
            claim_type=claim.claim_type,
            subject_scope=claim.subject_scope,
            duplicate_group_key="",
            approved_engagement_ids=claim.approved_engagement_ids,
        )
        for claim_id in sorted(selected_ids)
        if (claim := by_id.get(claim_id)) is not None
    ]

    rule_result = select_bounded_rules(candidate_memory, [r["text"] for r in requirements])

    context_text_len = sum(len(c.text) for c in final_claims)
    context_text_len += sum(len(r.text) for r in rule_result.selected)
    context_text_len += sum(
        len(e.approved_role_title) + len(e.displayed_organization) for e in eligible.engagements
    )
    estimated_tokens = estimate_tokens(" " * context_text_len)

    if estimated_tokens > MAX_ESTIMATED_REQUEST_TOKENS:
        raise RetrievalBudgetExceededError(
            f"Estimated request size ({estimated_tokens} tokens) exceeds "
            f"MAX_ESTIMATED_REQUEST_TOKENS={MAX_ESTIMATED_REQUEST_TOKENS} even after all count "
            "caps were applied -- lower MAX_SELECTED_CLAIMS/MAX_RULES or raise the token budget "
            "deliberately; this is never silently truncated."
        )

    manifest = RetrievalManifest(
        eligible_count=len(eligible.claims),
        duplicate_count=duplicate_count,
        candidate_pool_count=len(candidate_pool) if requirements else 0,
        selected_count=len(final_claims),
        excluded_counts=excluded_counts,
        per_requirement_selected_claim_ids=selected_by_requirement,
        rules_selected_count=len(rule_result.selected),
        rules_duplicate_count=rule_result.duplicate_count,
        rules_excluded_positioning_count=rule_result.excluded_positioning_count,
        estimated_request_tokens=estimated_tokens,
        caps={
            "max_candidates_per_requirement": MAX_CANDIDATES_PER_REQUIREMENT,
            "min_candidates_per_requirement": MIN_CANDIDATES_PER_REQUIREMENT,
            "max_ranking_candidates": MAX_RANKING_CANDIDATES,
            "max_selected_claims": MAX_SELECTED_CLAIMS,
            "max_rules": MAX_RULES,
            "max_estimated_request_tokens": MAX_ESTIMATED_REQUEST_TOKENS,
            "max_normalization_items": MAX_NORMALIZATION_ITEMS,
            "max_diagnostic_terms": MAX_DIAGNOSTIC_TERMS,
            "max_equivalents": MAX_EQUIVALENTS,
            "max_preserved_terms": MAX_PRESERVED_TERMS,
        },
        requirement_normalization=normalization_manifest,
    )

    final_context = RetrievalContext(
        candidate_memory_id=candidate_memory.pk,
        claims=final_claims,
        engagements=eligible.engagements,
        rules=rule_result.selected,
    )
    return final_context, manifest
