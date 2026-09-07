"""M6's own bounded context builder (audit hardening, 2026-09-03; hybrid-context correction,
D-035, 2026-09-06; pinned-evidence-identity correction, D-037, 2026-09-07) -- Agent Builder must
never reload or send the entire CandidateMemory, must not even re-run M5's own retrieval query
against the full eligible pool, and (D-037) must never consult *live* mutable state to decide what
evidence exists for an already-created `FitAssessment`.

D-037 root cause and fix: D-036's first version of this function called
`retrieve.get_active_candidate_memory()` and queried `CareerEngagement.objects.filter(
approval_status=APPROVED)`/live `ClaimEngagementMapping.status` fresh, every time it ran -- so the
same `FitAssessment` could feed Agent Builder different evidence depending on *when* M6 happened to
run, including after a different `CandidateMemory` revision had since activated, or after an
engagement's approval/mapping state had since changed. This function now reads only two things off
`fit_assessment` itself: `based_on_candidate_memory` (the exact revision M5 pinned) and
`baseline_chronology_manifest` (the exact evidence roster M5 computed and persisted at that same
moment, `candidate_matching.services.baseline_chronology.build_baseline_manifest`) -- both
validated (`baseline_chronology.validate_manifest`) and reconstructed
(`baseline_chronology.reconstruct_retrieved_claims`) without a single live query against
`CareerEngagement.approval_status` or `ClaimEngagementMapping.status`. A `FitAssessment` that
predates this correction (no pinned identity, e.g. the real `FitAssessment` id 9) is refused
outright (`LegacyFitAssessmentManifestError`) rather than falling back to the old live-query
behavior -- see `docs/DECISIONS.md` D-037.

What is still safe to resolve fresh, and why (see `baseline_chronology.py`'s own module docstring
for the full rationale): `MemoryClaim.canonical_text_en`/`claim_type`/`subject_scope`, because a
`CandidateMemory` revision's claim content is permanently frozen the moment it first becomes
ACTIVE; and `CareerEngagement`'s own display fields (title/organisation/location/dates), because
that record is a deliberately live, operator-editable registry, never revision-scoped, whose
current field values are meant to apply to every future render (unchanged from before this
correction -- see `resume_builder.rendering.markdown`/`candidate_memory.services.
static_profile_boundary`). Only *which* engagements/claims are in scope is pinned.

Rules: recomputed fresh, but against the *pinned* `based_on_candidate_memory` instance -- never
`get_active_candidate_memory()` -- via the same bounded/deduplicated/capped selection M5 uses
(`candidate_matching.services.rule_selection.select_bounded_rules`). This remains safe to recompute
rather than pin because `CandidateRule` is a `_RevisionScopedModel`: its content is frozen with its
owning revision exactly like `MemoryClaim`'s, so recomputing against the same pinned revision is
deterministic and always reproduces the exact same result -- no manifest entry is needed for it.

Bounded, predictable size: the merged claim set (job-relevant + anchors + language) is still capped
by construction -- `MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT` per engagement, all language claims (typically
few) -- and this function still runs a partial, early size check against
`MAX_ESTIMATED_REQUEST_TOKENS` (`RetrievalBudgetExceededError`) as a cheap early signal; the
authoritative, complete-request check now lives in `services/generate.py::generate_resume_content`
(D-037 Phase F), which sees the fully assembled request (system prompt, JRA text, requirement
explanations, and the output schema included) right before the provider call.
"""

from __future__ import annotations

from candidate_matching.models import FitAssessment
from candidate_matching.services.baseline_chronology import (
    InvalidBaselineManifestError,
    LegacyFitAssessmentManifestError,
    reconstruct_retrieved_claims,
    validate_manifest,
)
from candidate_matching.services.retrieval_limits import (
    MAX_ESTIMATED_REQUEST_TOKENS,
    RetrievalBudgetExceededError,
    estimate_tokens,
)
from candidate_matching.services.retrieve import RetrievalContext, RetrievedEngagement
from candidate_matching.services.rule_selection import select_bounded_rules
from candidate_memory.models import CareerEngagement

__all__ = [
    "InvalidBaselineManifestError",
    "LegacyFitAssessmentManifestError",
    "RetrievalBudgetExceededError",
    "build_builder_context",
]


def build_builder_context(fit_assessment: FitAssessment) -> RetrievalContext:
    if fit_assessment.based_on_candidate_memory_id is None or not fit_assessment.baseline_chronology_manifest:
        raise LegacyFitAssessmentManifestError(
            f"FitAssessment {fit_assessment.pk} has no pinned CandidateMemory identity/baseline "
            "chronology manifest (D-037) -- this is a pre-correction legacy row (e.g. the real "
            "FitAssessment id 9). There is no way to safely reconstruct which CandidateMemory "
            "revision or engagement/mapping state it actually used, and this is never guessed "
            "from incidental data such as an MC-<revision>-* claim-id prefix. Run Agent Candidate "
            "again (a fresh, versioned M5 run) for this application to obtain a FitAssessment with "
            "a real pinned identity and manifest before Agent Builder can run."
        )

    candidate_memory = fit_assessment.based_on_candidate_memory
    manifest = fit_assessment.baseline_chronology_manifest
    validate_manifest(manifest, candidate_memory_id=candidate_memory.pk)

    retrieved_claims = reconstruct_retrieved_claims(manifest, candidate_memory.pk)
    retrieved_claims.sort(key=lambda c: c.claim_id)

    approved_engagement_ids = manifest["approved_engagement_ids"]
    engagements_by_id = {
        engagement.engagement_id: engagement
        for engagement in CareerEngagement.objects.filter(engagement_id__in=approved_engagement_ids)
    }
    missing_engagement_ids = sorted(set(approved_engagement_ids) - engagements_by_id.keys())
    if missing_engagement_ids:
        raise InvalidBaselineManifestError(
            f"baseline_chronology_manifest references CareerEngagement id(s) that no longer exist: "
            f"{missing_engagement_ids}."
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
        for engagement in (engagements_by_id[eid] for eid in approved_engagement_ids)
    ]
    retrieved_engagements.sort(key=lambda e: e.engagement_id)

    requirement_texts = list(fit_assessment.based_on_jra.requirements.values_list("text", flat=True))
    rule_result = select_bounded_rules(candidate_memory, requirement_texts)
    rules = rule_result.selected

    context_text_len = sum(len(c.text) for c in retrieved_claims)
    context_text_len += sum(len(r.text) for r in rules)
    context_text_len += sum(
        len(e.approved_role_title) + len(e.displayed_organization) for e in retrieved_engagements
    )
    estimated_tokens = estimate_tokens(" " * context_text_len)
    if estimated_tokens > MAX_ESTIMATED_REQUEST_TOKENS:
        raise RetrievalBudgetExceededError(
            f"Estimated Agent Builder context size ({estimated_tokens} tokens -- a partial, "
            "early estimate covering only claim/rule/engagement text, not the full assembled "
            f"request) exceeds MAX_ESTIMATED_REQUEST_TOKENS={MAX_ESTIMATED_REQUEST_TOKENS} even "
            "after the D-035 baseline chronology's own per-engagement anchor cap was applied -- "
            "lower MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT or raise the token budget deliberately; this "
            "is never silently truncated. (The authoritative, complete-request check runs in "
            "services/generate.py right before the provider call.)"
        )

    return RetrievalContext(
        candidate_memory_id=candidate_memory.pk,
        claims=retrieved_claims,
        engagements=retrieved_engagements,
        rules=rules,
        engagements_without_eligible_evidence=tuple(manifest["engagements_with_no_eligible_evidence"]),
        pinned_language_claim_ids=tuple(manifest["language_claim_ids"]),
    )
