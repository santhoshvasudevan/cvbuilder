"""Deterministic, reviewable mapping between `MemoryClaim`s and operator-approved
`CareerEngagement` records (D-019). No source re-extraction, no LLM call, and no fuzzy/embedding
similarity -- a mapping is only ever proposed on an exact, normalized identity match between a
claim's `legal_employer`/`client_organization` (or, failing that, its `subject_scope`) and an
`APPROVED` engagement's own `legal_employer`/`client_organization`. Anything else -- no match, or
more than one candidate match -- is left unresolved for the operator, never guessed.

Proposing, approving, or rejecting a mapping never edits the `MemoryClaim` row itself (see
`ClaimEngagementMapping`'s docstring), so this is safe to run against claims belonging to an
`ACTIVE` `CandidateMemory` revision.
"""

from __future__ import annotations

import dataclasses

from django.utils import timezone

from ..models import CandidateMemory, CareerEngagement, ClaimEngagementMapping, MemoryClaim

_EMPLOYMENT_CLAIM_TYPES = ("employment_dates", "employment_location")


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _claim_identity_key(claim: MemoryClaim) -> tuple[str, tuple[str, str] | str] | None:
    """This claim's identity, tagged by how precise it is:
    - `("pair", (legal_employer, client_organization))`, both normalized, when the claim has
      either field populated -- matched only against an engagement with the exact same pair;
    - `("scope", name)`, a single normalized name, when falling back to `subject_scope` (its
      `organization:` prefix stripped) because both structured fields are blank, since many claims
      predate those fields existing -- matched against either of an engagement's own names (a
      scope-only claim cannot distinguish "legal employer" from "client").
    Returns `None` when there is nothing usable at all."""
    if claim.legal_employer or claim.client_organization:
        return ("pair", (_normalize(claim.legal_employer), _normalize(claim.client_organization)))
    scope = claim.subject_scope or ""
    if scope.lower().startswith("organization:"):
        scope = scope.split(":", 1)[1]
    scope = _normalize(scope)
    if not scope:
        return None
    return ("scope", scope)


def _engagement_pair_key(engagement: CareerEngagement) -> tuple[str, str]:
    return (_normalize(engagement.legal_employer), _normalize(engagement.client_organization))


def _engagement_scope_names(engagement: CareerEngagement) -> set[str]:
    return {
        name
        for name in (_normalize(engagement.legal_employer), _normalize(engagement.client_organization))
        if name
    }


@dataclasses.dataclass
class MappingProposalSummary:
    claims_considered: int
    proposed: int
    already_mapped: int
    ambiguous: int
    unresolved: int


def propose_claim_engagement_mappings(candidate_memory: CandidateMemory) -> MappingProposalSummary:
    """Proposes a `ClaimEngagementMapping` (`status=PROPOSED`) for every `CONFIRMED`
    employment_dates/employment_location claim on `candidate_memory` whose normalized identity key
    exactly matches exactly one `APPROVED` `CareerEngagement`. Idempotent: a `(claim, engagement)`
    pair that already has a mapping row, of any status, is counted as `already_mapped` and left
    untouched rather than duplicated or re-proposed."""
    approved_engagements = list(
        CareerEngagement.objects.filter(approval_status=CareerEngagement.ApprovalStatus.APPROVED)
    )
    by_pair: dict[tuple[str, str], list[CareerEngagement]] = {}
    by_scope_name: dict[str, list[CareerEngagement]] = {}
    for engagement in approved_engagements:
        by_pair.setdefault(_engagement_pair_key(engagement), []).append(engagement)
        for name in _engagement_scope_names(engagement):
            by_scope_name.setdefault(name, []).append(engagement)

    claims = candidate_memory.claims.filter(
        claim_type__in=_EMPLOYMENT_CLAIM_TYPES,
        confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
    )

    considered = proposed = already_mapped = ambiguous = unresolved = 0
    for claim in claims:
        considered += 1
        identity = _claim_identity_key(claim)
        if identity is None:
            unresolved += 1
            continue
        kind, key = identity
        matches = by_pair.get(key, []) if kind == "pair" else by_scope_name.get(key, [])
        if not matches:
            unresolved += 1
            continue
        if len(matches) > 1:
            ambiguous += 1
            continue
        engagement = matches[0]
        _, created = ClaimEngagementMapping.objects.get_or_create(
            memory_claim=claim,
            career_engagement=engagement,
            defaults={"proposed_reason": "exact normalized legal_employer/client_organization match"},
        )
        if created:
            proposed += 1
        else:
            already_mapped += 1

    return MappingProposalSummary(
        claims_considered=considered,
        proposed=proposed,
        already_mapped=already_mapped,
        ambiguous=ambiguous,
        unresolved=unresolved,
    )


def approve_mapping(mapping: ClaimEngagementMapping) -> ClaimEngagementMapping:
    mapping.status = ClaimEngagementMapping.Status.APPROVED
    mapping.reviewed_at = timezone.now()
    mapping.save()
    return mapping


def reject_mapping(mapping: ClaimEngagementMapping) -> ClaimEngagementMapping:
    mapping.status = ClaimEngagementMapping.Status.REJECTED
    mapping.reviewed_at = timezone.now()
    mapping.save()
    return mapping
