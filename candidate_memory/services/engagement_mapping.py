"""Deterministic, reviewable mapping between `MemoryClaim`s and operator-approved
`CareerEngagement` records (D-019, refined 2026-09-03).

`CareerEngagement` is the sole canonical source for employer/client identity, role title,
location, and employment dates -- `STATIC_ENGAGEMENT_CLAIM_TYPES` below is the definitive,
evidence-based (not guessed) list of `MemoryClaim.claim_type` values whose entire factual content
duplicates a field `CareerEngagement` already owns. A static claim never needs, and must never
receive, an engagement mapping: it never enters `propose_claim_engagement_mappings`'s
consideration, and `approve_mapping` refuses outright to approve one even if a stale row exists
(e.g. from before this boundary was refined). `ClaimEngagementMapping` exists only to link
**narrative** evidence -- responsibilities, achievements, projects, role-specific skills/technical
delivery -- to the engagement it should be placed under once a future M6 renders that engagement's
experience section; a global or career-level statement with no single-employer identity simply
never matches an engagement and is correctly left unmapped.

No source re-extraction, no LLM call, and no fuzzy/embedding similarity anywhere in this module --
a mapping is only ever proposed on an exact, normalized identity match between a claim's
`legal_employer`/`client_organization` (or, failing that, its `subject_scope`) and an `APPROVED`
engagement's own `legal_employer`/`client_organization`. Anything else -- no match, or more than
one candidate match -- is left unresolved for the operator, never guessed.

Proposing, approving, or rejecting a mapping never edits the `MemoryClaim` row itself (see
`ClaimEngagementMapping`'s docstring), so this is safe to run against claims belonging to an
`ACTIVE` `CandidateMemory` revision.
"""

from __future__ import annotations

import dataclasses

from django.utils import timezone

from ..models import CandidateMemory, CareerEngagement, ClaimEngagementMapping, MemoryClaim

# Evidence-based, not guessed: verified against the real activated CandidateMemory's own
# claim_type vocabulary (2026-09-03). Each one's entire informational content is now owned by a
# CareerEngagement field instead:
#   employment_dates       -> CareerEngagement.start_year/start_month/end_status/end_year/end_month
#   employment_location     -> CareerEngagement.location
#   position / position_title -> CareerEngagement.approved_role_title (+ legal_employer/
#                                client_organization identity embedded in the same claim's text)
# There is no separate claim_type carrying "employer/client identity" alone in this corpus's
# vocabulary -- that identity is expressed via the legal_employer/client_organization fields (or
# subject_scope) *on* these same four claim types, never as its own claim_type.
STATIC_ENGAGEMENT_CLAIM_TYPES = frozenset(
    {"employment_dates", "employment_location", "position", "position_title"}
)


class StaticClaimMappingError(Exception):
    """Raised when an operator (or an automated action) attempts to approve a
    `ClaimEngagementMapping` for a claim whose `claim_type` is in `STATIC_ENGAGEMENT_CLAIM_TYPES`.
    Such a claim's facts are already canonically owned by the mapping's own `career_engagement` --
    approving it as a narrative mapping would be redundant at best and contradictory at worst if
    the claim's own (now-superseded) text ever disagreed with the engagement record."""


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
    """Proposes a `ClaimEngagementMapping` (`status=PROPOSED`) for every `CONFIRMED`,
    `resume_eligible`, **narrative** claim on `candidate_memory` (i.e. `claim_type` **not** in
    `STATIC_ENGAGEMENT_CLAIM_TYPES`) whose normalized identity key exactly matches exactly one
    `APPROVED` `CareerEngagement`. A global/career-level claim's identity key never matches a real
    engagement's own legal_employer/client_organization, so it is correctly left unmapped rather
    than needing a separate claim_type check. Idempotent: a `(claim, engagement)` pair that already
    has a mapping row, of any status, is counted as `already_mapped` and left untouched rather than
    duplicated or re-proposed."""
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
        confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        resume_eligible=True,
    ).exclude(claim_type__in=STATIC_ENGAGEMENT_CLAIM_TYPES)

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
    """Refuses (`StaticClaimMappingError`, fail closed) to approve a mapping whose claim is a
    static engagement claim type -- see module docstring. This is the sole approval entry point;
    the admin's bulk approve action calls this per row rather than bulk-updating status directly,
    so the refusal is enforced through the real workflow, not just available as an unused check."""
    if mapping.memory_claim.claim_type in STATIC_ENGAGEMENT_CLAIM_TYPES:
        raise StaticClaimMappingError(
            f"{mapping.memory_claim.claim_id} is a static engagement claim "
            f"(claim_type={mapping.memory_claim.claim_type!r}); its facts are already owned by "
            f"CareerEngagement {mapping.career_engagement.engagement_id} and must never be "
            "approved as a narrative mapping. Reject it instead."
        )
    mapping.status = ClaimEngagementMapping.Status.APPROVED
    mapping.reviewed_at = timezone.now()
    mapping.save()
    return mapping


def reject_mapping(mapping: ClaimEngagementMapping) -> ClaimEngagementMapping:
    mapping.status = ClaimEngagementMapping.Status.REJECTED
    mapping.reviewed_at = timezone.now()
    mapping.save()
    return mapping


@dataclasses.dataclass
class MappingCleanupReport:
    to_reject: list[ClaimEngagementMapping]
    to_retain: list[ClaimEngagementMapping]


def find_mappings_needing_review(candidate_memory: CandidateMemory) -> MappingCleanupReport:
    """Read-only categorization of every existing `ClaimEngagementMapping` on `candidate_memory`
    against the refined static/narrative boundary -- makes no writes. `to_reject` holds every
    mapping whose claim is a static engagement claim type (regardless of its current status,
    including an already-`APPROVED` one from before this boundary was refined); `to_retain` holds
    every genuinely narrative mapping."""
    mappings = list(
        ClaimEngagementMapping.objects.filter(memory_claim__candidate_memory=candidate_memory)
        .select_related("memory_claim", "career_engagement")
    )
    to_reject = [m for m in mappings if m.memory_claim.claim_type in STATIC_ENGAGEMENT_CLAIM_TYPES]
    to_retain = [m for m in mappings if m.memory_claim.claim_type not in STATIC_ENGAGEMENT_CLAIM_TYPES]
    return MappingCleanupReport(to_reject=to_reject, to_retain=to_retain)


def approved_narrative_claim_ids_for_engagement(engagement: CareerEngagement) -> list[str]:
    """The planned M6 renderer's lookup: which narrative `MemoryClaim`s (via `APPROVED`
    `ClaimEngagementMapping` rows) should be placed as tailored bullets under this engagement's
    experience section. Never includes a static engagement claim -- `approve_mapping` refuses to
    approve one, so none can reach `APPROVED` status in the first place."""
    return list(
        ClaimEngagementMapping.objects.filter(
            career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        ).values_list("memory_claim__claim_id", flat=True)
    )
