"""The eligibility layer of retrieval from the ACTIVE CandidateMemory for Agent Candidate/Agent
Builder (M5/M6, requirements.md Sec 6, D-015's runtime-context boundaries, D-019).

This module only ever applies structural eligibility filters -- it is deliberately NOT the bounded
context sent to a provider by itself (audit hardening, 2026-09-03): `services/bounded_retrieval.py`
is what turns this eligible pool into the actual, capped, ranked context a request carries. Never
send the output of `retrieve_context()` directly to an LLM.

Eligibility, applied here:
- CONFIRMED, resume_eligible, narrative (non-static-type) MemoryClaims only;
- a claim with an APPROVED ClaimEngagementMapping is tagged with the *complete* set of engagements
  it is approved for (a claim can legitimately be approved for more than one -- see D-019
  refinement, audit hardening 2026-09-03: never assume a single nullable engagement);
- a claim with only PROPOSED/REJECTED mappings (no APPROVED one) is excluded outright -- an
  engagement-specific claim never falls back to being treated as global;
- a claim with no mapping at all is included with an empty `approved_engagement_ids` tuple
  ("global");
- every APPROVED CareerEngagement and every CandidateRule on the revision.

Never the full CandidateMemory, a source document, or the reference snapshot.
"""

from __future__ import annotations

import dataclasses

from candidate_memory.models import CandidateMemory, CandidateRule, CareerEngagement, MemoryClaim
from candidate_memory.services.engagement_mapping import STATIC_ENGAGEMENT_CLAIM_TYPES


class NoActiveCandidateMemoryError(Exception):
    pass


# D-035 hybrid-context correction (2026-09-06): why a claim reached Agent Builder's context, never
# mutually exclusive -- a claim can carry more than one reason (e.g. an engagement anchor that
# AC_RANK also happened to select). `retrieval_reasons` is purely provenance/inspection metadata;
# it plays no role in eligibility or no-fabrication checks, which continue to key only on
# claim_id/approved_engagement_ids exactly as before this correction.
RETRIEVAL_REASON_JOB_RELEVANT = "JOB_RELEVANT"
RETRIEVAL_REASON_ENGAGEMENT_ANCHOR = "ENGAGEMENT_ANCHOR"
RETRIEVAL_REASON_LANGUAGE_EVIDENCE = "LANGUAGE_EVIDENCE"


@dataclasses.dataclass(frozen=True)
class RetrievedClaim:
    claim_id: str
    text: str
    claim_type: str
    subject_scope: str
    approved_engagement_ids: tuple[str, ...] = ()
    duplicate_group_key: str = ""
    retrieval_reasons: tuple[str, ...] = ()

    @property
    def is_global(self) -> bool:
        return not self.approved_engagement_ids


@dataclasses.dataclass(frozen=True)
class RetrievedEngagement:
    engagement_id: str
    approved_role_title: str
    displayed_organization: str
    location: str
    is_current: bool
    duration_months: int | None


@dataclasses.dataclass(frozen=True)
class RetrievedRule:
    rule_id: int
    rule_type: str
    text: str
    scope: str


@dataclasses.dataclass(frozen=True)
class RetrievalContext:
    candidate_memory_id: int
    claims: list[RetrievedClaim]
    engagements: list[RetrievedEngagement]
    rules: list[RetrievedRule]
    # D-035 hybrid-context correction: engagement_ids (from `engagements` above, so always a
    # subset of it) with zero eligible baseline-anchor claims -- an explicit diagnostic for Agent
    # Builder/the renderer, never a reason to fabricate or to drop the engagement. Always empty for
    # M5 (Agent Candidate) contexts, which have no notion of baseline anchors.
    engagements_without_eligible_evidence: tuple[str, ...] = ()

    @property
    def claim_ids(self) -> list[str]:
        return [claim.claim_id for claim in self.claims]

    @property
    def engagement_ids(self) -> list[str]:
        return [engagement.engagement_id for engagement in self.engagements]


def get_active_candidate_memory() -> CandidateMemory:
    try:
        return CandidateMemory.objects.get(status=CandidateMemory.Status.ACTIVE)
    except CandidateMemory.DoesNotExist as exc:
        raise NoActiveCandidateMemoryError(
            "No ACTIVE CandidateMemory revision exists -- Agent Candidate cannot run until one "
            "is activated."
        ) from exc


def retrieve_eligible_pool(candidate_memory: CandidateMemory) -> RetrievalContext:
    """The full structurally-eligible pool -- still not bounded/relevance-filtered. Callers must
    go through `services/bounded_retrieval.py` before this reaches a provider request."""
    approved_engagements = list(
        CareerEngagement.objects.filter(approval_status=CareerEngagement.ApprovalStatus.APPROVED)
    )
    engagement_by_pk = {engagement.pk: engagement for engagement in approved_engagements}

    eligible_claims = (
        candidate_memory.claims.filter(
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
            resume_eligible=True,
        )
        .exclude(claim_type__in=STATIC_ENGAGEMENT_CLAIM_TYPES)
        .prefetch_related("engagement_mappings")
    )

    retrieved_claims: list[RetrievedClaim] = []
    for claim in eligible_claims:
        mappings = list(claim.engagement_mappings.all())
        approved_engagement_ids = tuple(
            sorted(
                engagement_by_pk[mapping.career_engagement_id].engagement_id
                for mapping in mappings
                if mapping.status == mapping.Status.APPROVED
                and mapping.career_engagement_id in engagement_by_pk
            )
        )
        has_any_mapping = bool(mappings)
        if has_any_mapping and not approved_engagement_ids:
            # Engagement-specific but only PROPOSED/REJECTED -- not yet operator-approved for
            # placement, so it stays excluded from this run rather than guessed.
            continue
        retrieved_claims.append(
            RetrievedClaim(
                claim_id=claim.claim_id,
                text=claim.canonical_text_en,
                claim_type=claim.claim_type,
                subject_scope=claim.subject_scope,
                duplicate_group_key=claim.duplicate_group_key,
                approved_engagement_ids=approved_engagement_ids,
            )
        )

    retrieved_engagements = [
        RetrievedEngagement(
            engagement_id=engagement.engagement_id,
            approved_role_title=engagement.approved_role_title,
            displayed_organization=engagement.displayed_organization,
            location=engagement.location,
            is_current=engagement.is_current,
            duration_months=engagement.duration_months(),
        )
        for engagement in approved_engagements
    ]

    retrieved_rules = [
        RetrievedRule(rule_id=rule.pk, rule_type=rule.rule_type, text=rule.text, scope=rule.scope)
        for rule in CandidateRule.objects.filter(candidate_memory=candidate_memory)
    ]

    return RetrievalContext(
        candidate_memory_id=candidate_memory.pk,
        claims=retrieved_claims,
        engagements=retrieved_engagements,
        rules=retrieved_rules,
    )


# Backward-compatible alias -- pre-hardening callers/tests used this name for what is now the
# unbounded eligibility pool. Prefer `retrieve_eligible_pool` in new code; both names refer to the
# same function.
retrieve_context = retrieve_eligible_pool
