"""The D-035 hybrid-context correction: a deterministic, zero-LLM baseline layer supplying Agent
Builder with career-chronology completeness independently of AC_RANK's job-relevance selection.

Root cause (D-035, `docs/DECISIONS.md`): M5's bounded retrieval treats job-relevance ranking as the
*sole* source of which `MemoryClaim`s reach Agent Builder. `CareerEngagement` eligibility itself was
never relevance-filtered (every `APPROVED` engagement already reaches `FitAssessment.
retrieved_engagement_ids`), but the narrative claims that would populate an engagement's bullets are
entirely subject to one job posting's specific requirements, with no engagement-balance guarantee.
A posting that never phrases a requirement in a way that scores a given engagement's (or language
evidence's) claims into the selected set reliably omits them, no matter how much confirmed, eligible
evidence exists.

This module supplies two deterministic, always-included layers on top of (never instead of) M5's
relevance-ranked selection:

1. **Engagement anchor claims** -- for every currently `APPROVED` `CareerEngagement`, a small, fixed
   number of its own confirmed, resume-eligible, narrative (non-static) claims, selected by a
   documented deterministic rule (see `_anchor_sort_key`), regardless of whether AC_RANK selected
   them. Only claims genuinely mapped to that engagement via an `APPROVED`
   `ClaimEngagementMapping` are ever used -- a global (unmapped) claim is never promoted into an
   engagement-specific anchor (see module docstring of
   `candidate_memory.services.engagement_mapping`).
2. **Confirmed language evidence** -- every confirmed, resume-eligible `claim_type ==
   "language_proficiency"` claim on the given `CandidateMemory`, included unconditionally. Language
   claims are almost always global career-level facts (D-035's own diagnosis: the one German claim
   only reached AC_RANK's pool at all via an unrelated floor-backfill), so relevance ranking is
   structurally unlikely to select them without this deterministic inclusion.

Both layers only ever *add* `RetrievedClaim`s that pass the exact same eligibility filter M5/M6
already enforce (`CONFIRMED`, `resume_eligible`, non-static claim_type, on the given
`CandidateMemory`) -- this is additive coverage, never a relaxation of any eligibility rule, and
never a new no-fabrication exemption: every claim added here still must exist, be confirmed, and be
attached by claim_id exactly like any AC_RANK-selected claim, so `validators/no_fabrication.py`
needs no change to enforce it.

An engagement with zero eligible anchor claims is not an error -- it is recorded in
`engagements_with_no_eligible_evidence` so the caller (and, ultimately, the rendered resume) can
show an explicit diagnostic rather than either fabricating content or silently omitting the
engagement (see `rendering/markdown.py`).
"""

from __future__ import annotations

import dataclasses

from candidate_matching.services.retrieve import (
    RETRIEVAL_REASON_ENGAGEMENT_ANCHOR,
    RETRIEVAL_REASON_LANGUAGE_EVIDENCE,
    RetrievedClaim,
)
from candidate_memory.models import CareerEngagement, ClaimEngagementMapping, MemoryClaim
from candidate_memory.services.engagement_mapping import STATIC_ENGAGEMENT_CLAIM_TYPES

# Deliberately small and fixed -- this is an "anchor" (enough to prove the engagement had real,
# substantive narrative content), not a replacement for AC_RANK's own tailored selection. Keeping
# this small across (in practice) a handful of approved engagements is what keeps the added context
# bounded and predictable (see `MAX_ESTIMATED_REQUEST_TOKENS` check in `services/context.py`).
MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT = 3

LANGUAGE_CLAIM_TYPE = "language_proficiency"

# Deterministic anchor tie-break rule (documented, not incidental): prefer a claim whose
# `experience_level` reflects more substantive, higher-signal work -- ownership/leadership over mere
# awareness -- and, among equally-ranked claims, always break ties on `claim_id` ascending. A claim
# with no `experience_level` at all ranks below every named level but is never excluded outright.
# This ordering exists purely to pick *which* claims are the anchors when an engagement has more
# than `MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT` eligible candidates -- it never affects whether an
# engagement is included (every APPROVED engagement always is) and never affects AC_RANK's own
# job-relevance selection.
_EXPERIENCE_LEVEL_RANK: dict[str | None, int] = {
    MemoryClaim.ExperienceLevel.LEADERSHIP: 6,
    MemoryClaim.ExperienceLevel.ARCHITECTURE_OWNERSHIP: 5,
    MemoryClaim.ExperienceLevel.PRODUCTION_OPERATION: 4,
    MemoryClaim.ExperienceLevel.PROFESSIONAL_DELIVERY: 3,
    MemoryClaim.ExperienceLevel.PROTOTYPE: 2,
    MemoryClaim.ExperienceLevel.LEARNING: 1,
    MemoryClaim.ExperienceLevel.AWARENESS: 0,
}


def _anchor_sort_key(claim: MemoryClaim) -> tuple[int, str]:
    rank = _EXPERIENCE_LEVEL_RANK.get(claim.experience_level, -1)
    return (-rank, claim.claim_id)


@dataclasses.dataclass(frozen=True)
class BaselineChronologyResult:
    # engagement_id -> its deterministically-selected anchor claims (may be empty).
    anchor_claims_by_engagement: dict[str, list[RetrievedClaim]]
    # Every currently APPROVED engagement_id with zero eligible anchor claims -- an explicit
    # diagnostic condition, surfaced to the caller/renderer, never fabricated around.
    engagements_with_no_eligible_evidence: list[str]
    language_claims: list[RetrievedClaim]

    @property
    def all_claims(self) -> list[RetrievedClaim]:
        claims: list[RetrievedClaim] = []
        for group in self.anchor_claims_by_engagement.values():
            claims.extend(group)
        claims.extend(self.language_claims)
        return claims


def _eligible_narrative_claims(candidate_memory_id: int):
    return MemoryClaim.objects.filter(
        candidate_memory_id=candidate_memory_id,
        confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        resume_eligible=True,
    ).exclude(claim_type__in=STATIC_ENGAGEMENT_CLAIM_TYPES)


def _retrieved_claim(
    claim: MemoryClaim,
    *,
    approved_engagement_ids: tuple[str, ...],
    reason: str,
) -> RetrievedClaim:
    return RetrievedClaim(
        claim_id=claim.claim_id,
        text=claim.canonical_text_en,
        claim_type=claim.claim_type,
        subject_scope=claim.subject_scope,
        approved_engagement_ids=approved_engagement_ids,
        retrieval_reasons=(reason,),
    )


def compute_engagement_anchors(
    candidate_memory_id: int, approved_engagements: list[CareerEngagement]
) -> tuple[dict[str, list[RetrievedClaim]], list[str]]:
    """Deterministically selects up to `MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT` anchor claims for every
    engagement in `approved_engagements`, independent of any job-specific relevance ranking.

    Only claims with an `APPROVED` `ClaimEngagementMapping` to that specific engagement are ever
    considered -- a global claim is never promoted into being one engagement's anchor (this would
    silently misattribute it). A claim's reported `approved_engagement_ids` reflects its *complete*
    set of approved mappings (it may legitimately be approved for more than one engagement), never
    just the one engagement being iterated.
    """
    engagement_by_pk = {engagement.pk: engagement for engagement in approved_engagements}
    anchor_ids_by_engagement: dict[str, list[str]] = {}
    no_evidence: list[str] = []
    all_anchor_claim_ids: set[str] = set()

    for engagement in approved_engagements:
        mapped_claims = list(
            _eligible_narrative_claims(candidate_memory_id)
            .filter(
                engagement_mappings__career_engagement=engagement,
                engagement_mappings__status=ClaimEngagementMapping.Status.APPROVED,
            )
            .distinct()
        )
        mapped_claims.sort(key=_anchor_sort_key)
        chosen = mapped_claims[:MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT]
        chosen_ids = sorted(claim.claim_id for claim in chosen)
        anchor_ids_by_engagement[engagement.engagement_id] = chosen_ids
        if not chosen_ids:
            no_evidence.append(engagement.engagement_id)
        all_anchor_claim_ids.update(chosen_ids)

    if not all_anchor_claim_ids:
        return {eid: [] for eid in anchor_ids_by_engagement}, sorted(no_evidence)

    claims_by_id: dict[str, MemoryClaim] = {
        claim.claim_id: claim
        for claim in MemoryClaim.objects.filter(
            candidate_memory_id=candidate_memory_id, claim_id__in=all_anchor_claim_ids
        ).prefetch_related("engagement_mappings")
    }

    retrieved_by_id: dict[str, RetrievedClaim] = {}
    for claim_id, claim in claims_by_id.items():
        approved_engagement_ids = tuple(
            sorted(
                engagement_by_pk[mapping.career_engagement_id].engagement_id
                for mapping in claim.engagement_mappings.all()
                if mapping.status == mapping.Status.APPROVED
                and mapping.career_engagement_id in engagement_by_pk
            )
        )
        retrieved_by_id[claim_id] = _retrieved_claim(
            claim, approved_engagement_ids=approved_engagement_ids,
            reason=RETRIEVAL_REASON_ENGAGEMENT_ANCHOR,
        )

    anchor_claims_by_engagement = {
        engagement_id: [
            retrieved_by_id[claim_id] for claim_id in claim_ids if claim_id in retrieved_by_id
        ]
        for engagement_id, claim_ids in anchor_ids_by_engagement.items()
    }
    return anchor_claims_by_engagement, sorted(no_evidence)


def compute_language_evidence(
    candidate_memory_id: int, approved_engagements: list[CareerEngagement]
) -> list[RetrievedClaim]:
    """Every confirmed, resume-eligible `language_proficiency` claim on this `CandidateMemory`,
    included unconditionally (D-035) -- ordered by claim_id for reproducibility. A language claim
    that happens to carry an `APPROVED` engagement mapping keeps that mapping (rare, but a claim's
    real approved mappings are never discarded); most are global career-level facts."""
    engagement_by_pk = {engagement.pk: engagement for engagement in approved_engagements}
    claims = (
        _eligible_narrative_claims(candidate_memory_id)
        .filter(claim_type=LANGUAGE_CLAIM_TYPE)
        .prefetch_related("engagement_mappings")
        .order_by("claim_id")
    )
    result: list[RetrievedClaim] = []
    for claim in claims:
        approved_engagement_ids = tuple(
            sorted(
                engagement_by_pk[mapping.career_engagement_id].engagement_id
                for mapping in claim.engagement_mappings.all()
                if mapping.status == mapping.Status.APPROVED
                and mapping.career_engagement_id in engagement_by_pk
            )
        )
        result.append(
            _retrieved_claim(
                claim, approved_engagement_ids=approved_engagement_ids,
                reason=RETRIEVAL_REASON_LANGUAGE_EVIDENCE,
            )
        )
    return result


def build_baseline_chronology(
    candidate_memory_id: int, approved_engagements: list[CareerEngagement]
) -> BaselineChronologyResult:
    anchor_claims_by_engagement, no_evidence = compute_engagement_anchors(
        candidate_memory_id, approved_engagements
    )
    language_claims = compute_language_evidence(candidate_memory_id, approved_engagements)
    return BaselineChronologyResult(
        anchor_claims_by_engagement=anchor_claims_by_engagement,
        engagements_with_no_eligible_evidence=no_evidence,
        language_claims=language_claims,
    )


def merge_retrieved_claims(*groups: list[RetrievedClaim]) -> list[RetrievedClaim]:
    """Unions claims across sources (job-relevant, engagement-anchor, language) by `claim_id`,
    never duplicating a claim that reached the context through more than one path -- instead its
    `retrieval_reasons` and `approved_engagement_ids` are unioned so every reason it's present
    remains inspectable. Returned sorted by claim_id for deterministic ordering."""
    merged: dict[str, RetrievedClaim] = {}
    for group in groups:
        for claim in group:
            existing = merged.get(claim.claim_id)
            if existing is None:
                merged[claim.claim_id] = claim
                continue
            merged[claim.claim_id] = dataclasses.replace(
                existing,
                retrieval_reasons=tuple(
                    sorted(set(existing.retrieval_reasons) | set(claim.retrieval_reasons))
                ),
                approved_engagement_ids=tuple(
                    sorted(set(existing.approved_engagement_ids) | set(claim.approved_engagement_ids))
                ),
            )
    return [merged[claim_id] for claim_id in sorted(merged)]
