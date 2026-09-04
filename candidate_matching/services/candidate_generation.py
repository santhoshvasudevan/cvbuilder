"""Per-JobRequirement candidate generation (audit hardening, 2026-09-03; rarity-aware scoring
correction 2026-09-04) -- step 3 of the bounded retrieval pipeline: deterministic BM25/phrase/
acronym scoring (`services/lexical_relevance.py`) narrows the deduplicated eligible pool down to a
bounded candidate list *per requirement*, before the D-015 ranking step ever runs.

Never silently gives a requirement zero candidates just because scoring found no overlap: if fewer
than `MIN_CANDIDATES_PER_REQUIREMENT` claims score above zero, the list is deterministically
backfilled (by ascending `claim_id`, never randomly) up to that floor -- a genuinely relevant claim
phrased with no shared vocabulary still gets a fair chance to be judged by the ranking step, rather
than being silently excluded by a keyword miss.
"""

from __future__ import annotations

import dataclasses

from .dedup import DedupedClaim
from .lexical_relevance import (
    CorpusStats,
    build_corpus_stats,
    normalize_terms,
    score_relevance,
    tokenize_ordered,
)
from .retrieval_limits import MAX_CANDIDATES_PER_REQUIREMENT, MIN_CANDIDATES_PER_REQUIREMENT


@dataclasses.dataclass(frozen=True)
class RequirementCandidates:
    requirement_id: str
    candidates: list[DedupedClaim]


def _build_stats(deduped_claims: list[DedupedClaim]) -> CorpusStats:
    return build_corpus_stats([tokenize_ordered(claim.text) for claim in deduped_claims])


def generate_candidates_for_requirement(
    requirement_text: str,
    deduped_claims: list[DedupedClaim],
    *,
    stats: CorpusStats | None = None,
) -> list[DedupedClaim]:
    """`stats` (corpus-wide term statistics) is computed fresh from `deduped_claims` if not
    supplied -- `generate_candidates` below computes it once and shares it across every
    requirement in the same job, since they all score against the same claim pool."""
    if stats is None:
        stats = _build_stats(deduped_claims)

    requirement_terms = normalize_terms(tokenize_ordered(requirement_text), stats)
    scored = []
    for claim in deduped_claims:
        claim_terms = normalize_terms(tokenize_ordered(claim.text), stats)
        claim_term_frequency: dict[str, int] = {}
        for term in claim_terms:
            claim_term_frequency[term] = claim_term_frequency.get(term, 0) + 1
        score = score_relevance(
            requirement_text, requirement_terms, claim.text, claim_terms, claim_term_frequency, stats
        )
        scored.append((score, claim))
    scored.sort(key=lambda pair: (-pair[0], pair[1].claim_id))

    positive = [claim for score, claim in scored if score > 0]
    selected = positive[:MAX_CANDIDATES_PER_REQUIREMENT]
    if len(selected) < MIN_CANDIDATES_PER_REQUIREMENT:
        selected_ids = {claim.claim_id for claim in selected}
        for _score, claim in scored:
            if len(selected) >= MIN_CANDIDATES_PER_REQUIREMENT:
                break
            if claim.claim_id not in selected_ids:
                selected.append(claim)
                selected_ids.add(claim.claim_id)
    return selected


def generate_candidates(
    requirements: list[dict], deduped_claims: list[DedupedClaim]
) -> list[RequirementCandidates]:
    """`requirements` is the same shape used throughout `candidate_matching` -- dicts with at
    least `requirement_id`/`text`. Corpus statistics are computed once here and shared across
    every requirement, rather than recomputed per requirement."""
    stats = _build_stats(deduped_claims)
    return [
        RequirementCandidates(
            requirement_id=requirement["requirement_id"],
            candidates=generate_candidates_for_requirement(requirement["text"], deduped_claims, stats=stats),
        )
        for requirement in requirements
    ]


def union_candidate_pool(per_requirement: list[RequirementCandidates]) -> list[DedupedClaim]:
    """The deduplicated union of every requirement's own candidate list, deterministically
    ordered by `claim_id` -- this is the bounded pool actually sent to the D-015 ranking step."""
    by_id: dict[str, DedupedClaim] = {}
    for group in per_requirement:
        for claim in group.candidates:
            by_id.setdefault(claim.claim_id, claim)
    return [by_id[claim_id] for claim_id in sorted(by_id)]
