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

D-037 pinned-evidence-identity correction (2026-09-07): this module moved from `resume_builder` to
`candidate_matching` because its computation is now an **M5-time** concern, not an M6-time one.
D-036's first implementation of this module computed the baseline chronology *fresh, at M6
(Agent Builder) execution time*, against whatever `CandidateMemory` revision happened to be
`ACTIVE` and whatever `CareerEngagement.approval_status`/`ClaimEngagementMapping.status` happened
to hold at that moment -- an independent audit found this reintroduced exactly the kind of
freshness/snapshot defect D-006 exists to prevent (a `FitAssessment` is supposed to be a frozen,
reproducible identity; re-deriving its evidence from live mutable state at M6 time means the same
`FitAssessment` can silently produce different Agent Builder input depending on *when* M6 happens
to run, including after a wholly different `CandidateMemory` revision has since activated).

The fix: `build_engagement_anchors`/`compute_language_evidence`/`build_baseline_chronology` below
are now called exactly once, at `candidate_matching.services.fit_assessment.build_fit_assessment`'s
own creation boundary (M5), against the exact `CandidateMemory` revision and `CareerEngagement`/
`ClaimEngagementMapping` state at that precise moment. `build_baseline_manifest` (below) turns that
one-time computation into a plain-JSON, schema-versioned manifest persisted atomically onto the new
`FitAssessment.baseline_chronology_manifest` field (`candidate_matching.models.FitAssessment`).
`resume_builder.services.context.build_builder_context` (M6) never calls any function in this
module again for an already-created `FitAssessment` -- it only reads and validates
(`validate_manifest`, `reconstruct_retrieved_claims`) the persisted manifest, so a later
`CandidateMemory` activation or `CareerEngagement`/`ClaimEngagementMapping` change can never alter
what an existing `FitAssessment` feeds to Agent Builder. See `docs/DECISIONS.md` D-037.
"""

from __future__ import annotations

import dataclasses

from candidate_memory.models import CandidateMemory, CareerEngagement, ClaimEngagementMapping, MemoryClaim
from candidate_memory.services.engagement_mapping import STATIC_ENGAGEMENT_CLAIM_TYPES

from .retrieve import (
    RETRIEVAL_REASON_ENGAGEMENT_ANCHOR,
    RETRIEVAL_REASON_JOB_RELEVANT,
    RETRIEVAL_REASON_LANGUAGE_EVIDENCE,
    RetrievedClaim,
)

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


# --- D-037 pinned-evidence-identity manifest -------------------------------------------------
#
# The manifest is a plain, JSON-serializable dict persisted verbatim onto
# `FitAssessment.baseline_chronology_manifest` (a JSONField, mirroring the existing
# `FitAssessment.retrieval_manifest`/`candidate_matching.services.bounded_retrieval.
# RetrievalManifest` convention already established for M5 audit records -- chosen over a set of
# normalized child rows because this content is a write-once, read-only reconstruction record
# never queried by its own SQL predicates, and a JSONField on the same already-append-only
# `FitAssessment` row is persisted atomically for free by virtue of being part of the same INSERT;
# normalized child rows would need their own immutability guard mirroring `_RevisionScopedModel`/
# `FitAssessment.save()` for no query benefit here). See `docs/DECISIONS.md` D-037 for the full
# schema rationale, and `resume_builder.services.context.build_builder_context` for the read side.
#
# What is deliberately NOT stored in the manifest: `MemoryClaim.canonical_text_en`/`claim_type`/
# `subject_scope`, and `CareerEngagement`'s own display fields (title/organisation/location/dates).
# Both are safe to re-resolve fresh by ID at M6 time without reintroducing the freshness defect:
# a `CandidateMemory` revision's claim *content* is permanently frozen the moment it first becomes
# ACTIVE (`candidate_memory.models._RevisionScopedModel`/`CandidateMemory.save()` -- no further
# mutation is ever possible, on pain of `RevisionNotEditableError`), so re-fetching a claim's text
# by `(candidate_memory_id, claim_id)` after pinning `based_on_candidate_memory` always returns
# exactly what M5 saw. `CareerEngagement` is deliberately a live, operator-editable registry (its
# own docstring: "admin-editable registry, not per-build state", never revision-scoped) whose
# display fields (a typo fix, an added location) are *meant* to apply to every future render,
# exactly like `resume_builder.rendering.markdown.render_engagement_header`/
# `candidate_memory.services.static_profile_boundary.resolve_approved_engagement` already resolve
# them fresh today; only *which engagements are in scope at all* and *which claims are their
# anchors* -- both governed by the separately-mutable `CareerEngagement.approval_status`/
# `ClaimEngagementMapping.status`, neither of which is revision-scoped/frozen -- are the pieces
# that must be pinned, because those are exactly what could otherwise drift out from under an
# already-created `FitAssessment`.

MANIFEST_SCHEMA_VERSION = 1
ALGORITHM_VERSION = "baseline_chronology.v1"

_REQUIRED_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "algorithm_version",
        "candidate_memory_id",
        "approved_engagement_ids",
        "engagements_with_no_eligible_evidence",
        "anchor_claim_ids_by_engagement",
        "language_claim_ids",
        "claim_inclusion_reasons",
        "claim_approved_engagement_ids",
    }
)

_KNOWN_RETRIEVAL_REASONS = frozenset(
    {
        RETRIEVAL_REASON_JOB_RELEVANT,
        RETRIEVAL_REASON_ENGAGEMENT_ANCHOR,
        RETRIEVAL_REASON_LANGUAGE_EVIDENCE,
    }
)


class InvalidBaselineManifestError(Exception):
    """Raised for a missing, malformed, cross-revision, or internally inconsistent
    `baseline_chronology_manifest` -- always fail-closed (D-037), never a best-effort partial
    reconstruction."""


class LegacyFitAssessmentManifestError(Exception):
    """Raised when `resume_builder.services.context.build_builder_context` is asked to build
    Agent Builder's context from a `FitAssessment` that predates D-037 (no
    `based_on_candidate_memory`, no `baseline_chronology_manifest`) -- e.g. the real
    `FitAssessment` id 9 for `JobApplication` 9. There is no way to reconstruct which
    `CandidateMemory` revision or mapping state that legacy row actually used (its own
    `retrieved_claim_ids` happening to carry an `MC-7-*` prefix is never treated as proof of
    which revision produced them in production code -- a coincidental prefix is not a recorded
    identity). The only correct remedy is a fresh, versioned M5 run for the owning
    `JobApplication`, which produces a new `FitAssessment` with a real pinned identity and
    manifest; this error exists so that path is the only way forward, never a silent guess.
    """


def build_baseline_manifest(
    *,
    candidate_memory: CandidateMemory,
    approved_engagements: list[CareerEngagement],
    baseline: BaselineChronologyResult,
    merged_claims: list[RetrievedClaim],
) -> dict:
    """The one, canonical manifest-construction function -- called from exactly one production
    call site (`candidate_matching.services.fit_assessment.build_fit_assessment`, M5) at the
    moment a new `FitAssessment` is created, and from test factories that need an equivalent
    pinned fixture without exercising the full LLM-scripted M5 pipeline.

    `merged_claims` must already be the *complete* evidence roster for the new `FitAssessment`
    (job-relevant claims tagged `RETRIEVAL_REASON_JOB_RELEVANT` merged with `baseline.all_claims`,
    via `merge_retrieved_claims`) -- every claim_id in it becomes a `claim_inclusion_reasons`/
    `claim_approved_engagement_ids` entry, which is what lets M6 reconstruct the exact evidence
    set without ever re-deriving job-relevance or re-querying mapping state.
    """
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "candidate_memory_id": candidate_memory.pk,
        "approved_engagement_ids": sorted(e.engagement_id for e in approved_engagements),
        "engagements_with_no_eligible_evidence": sorted(baseline.engagements_with_no_eligible_evidence),
        "anchor_claim_ids_by_engagement": {
            engagement_id: sorted(claim.claim_id for claim in claims)
            for engagement_id, claims in baseline.anchor_claims_by_engagement.items()
        },
        "language_claim_ids": sorted(claim.claim_id for claim in baseline.language_claims),
        "claim_inclusion_reasons": {
            claim.claim_id: sorted(claim.retrieval_reasons) for claim in merged_claims
        },
        "claim_approved_engagement_ids": {
            claim.claim_id: sorted(claim.approved_engagement_ids) for claim in merged_claims
        },
    }


def build_manifest_for_job_relevant_claim_ids(
    candidate_memory: CandidateMemory,
    approved_engagements: list[CareerEngagement],
    job_relevant_claim_ids: list[str] = (),
) -> dict:
    """Test/fixture convenience: builds the same manifest `build_fit_assessment` would, given only
    a plain list of already-selected job-relevant claim_ids (re-deriving their current
    `approved_engagement_ids` from the database) rather than a pre-built `RetrievedClaim` list --
    never used by the production M5 path itself, which already has real `RetrievedClaim` objects
    on hand from `bounded_retrieval.build_bounded_context` and calls `build_baseline_manifest`
    directly."""
    engagement_by_pk = {engagement.pk: engagement for engagement in approved_engagements}
    job_relevant_claims: list[RetrievedClaim] = []
    if job_relevant_claim_ids:
        claims = MemoryClaim.objects.filter(
            candidate_memory=candidate_memory, claim_id__in=list(job_relevant_claim_ids)
        ).prefetch_related("engagement_mappings")
        for claim in claims:
            approved_engagement_ids = tuple(
                sorted(
                    engagement_by_pk[mapping.career_engagement_id].engagement_id
                    for mapping in claim.engagement_mappings.all()
                    if mapping.status == mapping.Status.APPROVED
                    and mapping.career_engagement_id in engagement_by_pk
                )
            )
            job_relevant_claims.append(
                _retrieved_claim(
                    claim,
                    approved_engagement_ids=approved_engagement_ids,
                    reason=RETRIEVAL_REASON_JOB_RELEVANT,
                )
            )
    baseline = build_baseline_chronology(candidate_memory.pk, approved_engagements)
    merged = merge_retrieved_claims(job_relevant_claims, baseline.all_claims)
    return build_baseline_manifest(
        candidate_memory=candidate_memory,
        approved_engagements=approved_engagements,
        baseline=baseline,
        merged_claims=merged,
    )


def validate_manifest(manifest: dict, *, candidate_memory_id: int) -> None:
    """Fail-closed structural/consistency validation (D-037) -- run by
    `resume_builder.services.context.build_builder_context` before any of the manifest's content
    is trusted. Never attempts a partial/best-effort reconstruction from a manifest that fails
    this check."""
    if not isinstance(manifest, dict) or not manifest:
        raise InvalidBaselineManifestError("baseline_chronology_manifest is missing or empty.")

    missing_keys = _REQUIRED_MANIFEST_KEYS - manifest.keys()
    if missing_keys:
        raise InvalidBaselineManifestError(
            f"baseline_chronology_manifest is missing required key(s): {sorted(missing_keys)}."
        )

    if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise InvalidBaselineManifestError(
            f"baseline_chronology_manifest schema_version {manifest['schema_version']!r} is not "
            f"the one supported version ({MANIFEST_SCHEMA_VERSION!r})."
        )

    if manifest["candidate_memory_id"] != candidate_memory_id:
        raise InvalidBaselineManifestError(
            f"baseline_chronology_manifest.candidate_memory_id ({manifest['candidate_memory_id']!r}) "
            f"does not match FitAssessment.based_on_candidate_memory_id ({candidate_memory_id!r}) -- "
            "internally inconsistent manifest."
        )

    approved_engagement_ids = manifest["approved_engagement_ids"]
    no_evidence_ids = manifest["engagements_with_no_eligible_evidence"]
    anchors_by_engagement = manifest["anchor_claim_ids_by_engagement"]
    if not isinstance(approved_engagement_ids, list) or not isinstance(no_evidence_ids, list):
        raise InvalidBaselineManifestError(
            "baseline_chronology_manifest engagement fields must be lists."
        )
    if not set(no_evidence_ids) <= set(approved_engagement_ids):
        raise InvalidBaselineManifestError(
            "baseline_chronology_manifest.engagements_with_no_eligible_evidence contains an "
            "engagement_id not present in approved_engagement_ids."
        )
    if not isinstance(anchors_by_engagement, dict) or not set(anchors_by_engagement) <= set(
        approved_engagement_ids
    ):
        raise InvalidBaselineManifestError(
            "baseline_chronology_manifest.anchor_claim_ids_by_engagement references an "
            "engagement_id not present in approved_engagement_ids."
        )

    claim_inclusion_reasons = manifest["claim_inclusion_reasons"]
    claim_approved_engagement_ids = manifest["claim_approved_engagement_ids"]
    if not isinstance(claim_inclusion_reasons, dict) or not isinstance(claim_approved_engagement_ids, dict):
        raise InvalidBaselineManifestError(
            "baseline_chronology_manifest claim fields must be objects keyed by claim_id."
        )
    if claim_inclusion_reasons.keys() != claim_approved_engagement_ids.keys():
        raise InvalidBaselineManifestError(
            "baseline_chronology_manifest.claim_inclusion_reasons and "
            "claim_approved_engagement_ids do not cover the same claim_id set."
        )
    for claim_id, reasons in claim_inclusion_reasons.items():
        if not reasons or not set(reasons) <= _KNOWN_RETRIEVAL_REASONS:
            raise InvalidBaselineManifestError(
                f"baseline_chronology_manifest.claim_inclusion_reasons[{claim_id!r}] has an empty "
                f"or unrecognized reason set: {reasons!r}."
            )

    anchor_claim_ids = {cid for ids in anchors_by_engagement.values() for cid in ids}
    language_claim_ids = set(manifest["language_claim_ids"])
    unlisted = (anchor_claim_ids | language_claim_ids) - claim_inclusion_reasons.keys()
    if unlisted:
        raise InvalidBaselineManifestError(
            f"baseline_chronology_manifest references claim_id(s) not present in "
            f"claim_inclusion_reasons: {sorted(unlisted)}."
        )


def reconstruct_retrieved_claims(manifest: dict, candidate_memory_id: int) -> list[RetrievedClaim]:
    """Rebuilds the exact `RetrievedClaim` list a validated manifest describes -- claim text/type/
    scope re-fetched fresh (safe: frozen content, see module docstring), every other field (which
    claim_ids are in scope, why, and which engagements they are approved for) taken only from the
    pinned manifest, never a live query. Fails closed (`InvalidBaselineManifestError`) if any
    claim_id the manifest lists does not resolve to a CONFIRMED, resume-eligible, narrative claim
    on exactly `candidate_memory_id` -- this is what rejects a manifest referencing another
    revision's claim_ids, and what catches manifest/data corruption rather than silently dropping
    the claim."""
    claim_ids = sorted(manifest["claim_inclusion_reasons"].keys())
    if not claim_ids:
        return []

    found = {
        claim.claim_id: claim
        for claim in _eligible_narrative_claims(candidate_memory_id).filter(claim_id__in=claim_ids)
    }
    missing = sorted(set(claim_ids) - found.keys())
    if missing:
        raise InvalidBaselineManifestError(
            f"baseline_chronology_manifest references claim_id(s) that do not resolve to a "
            f"confirmed, resume-eligible, narrative claim on CandidateMemory {candidate_memory_id}: "
            f"{missing} -- this rejects both a cross-revision claim_id and a manifest/data "
            "integrity problem; it is never silently dropped."
        )

    reasons_by_id = manifest["claim_inclusion_reasons"]
    engagements_by_id = manifest["claim_approved_engagement_ids"]
    return [
        RetrievedClaim(
            claim_id=claim_id,
            text=found[claim_id].canonical_text_en,
            claim_type=found[claim_id].claim_type,
            subject_scope=found[claim_id].subject_scope,
            approved_engagement_ids=tuple(engagements_by_id.get(claim_id, [])),
            retrieval_reasons=tuple(reasons_by_id[claim_id]),
        )
        for claim_id in claim_ids
    ]
