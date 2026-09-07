"""D-037 pinned-evidence-identity correction: `build_builder_context` now reads only
`FitAssessment.based_on_candidate_memory`/`baseline_chronology_manifest` -- never a live
`CandidateMemory`/`CareerEngagement.approval_status`/`ClaimEngagementMapping.status` query. These
tests build a real, valid manifest via `candidate_matching.services.baseline_chronology.
build_manifest_for_job_relevant_claim_ids` (the same construction the corrected M5 boundary uses)
on an in-memory `FitAssessment`, exactly like the pre-D-037 tests built one with plain
`retrieved_claim_ids`/`retrieved_engagement_ids` -- the manifest is what's new, not the overall
test shape.
"""

from __future__ import annotations

from django.test import TestCase

from candidate_matching.models import FitAssessment
from candidate_matching.services.baseline_chronology import (
    InvalidBaselineManifestError,
    LegacyFitAssessmentManifestError,
    build_manifest_for_job_relevant_claim_ids,
)
from candidate_memory.models import CandidateMemory, ClaimEngagementMapping, MemoryClaim
from job_intake.models import JobRequirementAnalysis

from ..services.context import build_builder_context
from .factories import freeze_revision, make_engagement, make_narrative_claim, make_revision


def _make_jra() -> JobRequirementAnalysis:
    from job_applications.models import JobApplication

    application = JobApplication.objects.create()
    return JobRequirementAnalysis.objects.create(
        job_application=application, version=1,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="x" * 25, extracted_text="x" * 25,
        extracted_text_sha256="0" * 64, posting_language="en",
    )


def _pinned_fit_assessment(candidate_memory, approved_engagements=(), job_relevant_claim_ids=()):
    manifest = build_manifest_for_job_relevant_claim_ids(
        candidate_memory, list(approved_engagements), list(job_relevant_claim_ids)
    )
    return FitAssessment(
        based_on_jra=_make_jra(),
        based_on_candidate_memory=candidate_memory,
        retrieved_claim_ids=list(job_relevant_claim_ids),
        retrieved_engagement_ids=[e.engagement_id for e in approved_engagements],
        baseline_chronology_manifest=manifest,
    )


class BuildBuilderContextTests(TestCase):
    def test_only_includes_claims_the_fit_assessment_actually_selected(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        selected = make_narrative_claim(rev, canonical_text_en="Selected claim.")
        not_selected = make_narrative_claim(rev, canonical_text_en="Not selected claim.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        fit_assessment = _pinned_fit_assessment(rev, job_relevant_claim_ids=[selected.claim_id])
        context = build_builder_context(fit_assessment)
        self.assertEqual(context.claim_ids, [selected.claim_id])
        self.assertNotIn(not_selected.claim_id, context.claim_ids)

    def test_retiring_a_pinned_claim_via_a_bulk_bypass_fails_closed(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        claim = make_narrative_claim(rev, canonical_text_en="Will be retired.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        fit_assessment = _pinned_fit_assessment(rev, job_relevant_claim_ids=[claim.claim_id])

        # Simulate corruption/drift after the manifest was already pinned, via a direct bulk
        # update (the same "known, accepted bypass" every _RevisionScopedModel guard already
        # documents) -- this is a read-only test of build_builder_context's own fail-closed
        # reconstruction, not a claim that retiring an ACTIVE revision's claim is normal.
        MemoryClaim.objects.filter(pk=claim.pk).update(
            confirmation_status=MemoryClaim.ConfirmationStatus.RETIRED
        )

        # D-037: a manifest claim_id that no longer resolves is a fail-closed data-integrity
        # error, never a silent exclusion -- the pre-D-037 behavior here was to quietly return an
        # empty context, which is exactly the kind of silent evidence loss this correction rules
        # out.
        with self.assertRaises(InvalidBaselineManifestError):
            build_builder_context(fit_assessment)

    def test_tags_a_claim_with_its_approved_engagement(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="Engagement-mapped claim.")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        fit_assessment = _pinned_fit_assessment(
            rev, approved_engagements=[engagement], job_relevant_claim_ids=[claim.claim_id]
        )
        context = build_builder_context(fit_assessment)
        self.assertEqual(context.claims[0].approved_engagement_ids, (engagement.engagement_id,))
        self.assertEqual(context.engagement_ids, [engagement.engagement_id])

    def test_engagement_rejected_before_manifest_creation_is_excluded_from_the_roster(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="x")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        engagement.approval_status = engagement.ApprovalStatus.REJECTED
        engagement.save(update_fields=["approval_status"])

        # Manifest built *after* rejection -- build_manifest_for_job_relevant_claim_ids is only
        # ever given approved_engagements that are actually approved at that moment (mirroring
        # the real M5 boundary), so a rejected engagement never enters the roster in the first
        # place.
        fit_assessment = _pinned_fit_assessment(
            rev, approved_engagements=[], job_relevant_claim_ids=[claim.claim_id]
        )
        context = build_builder_context(fit_assessment)
        self.assertEqual(context.engagements, [])
        self.assertEqual(context.claims[0].approved_engagement_ids, ())

    def test_engagement_rejected_after_manifest_creation_still_appears_pinned(self):
        """D-037's own corrective proof: the pre-correction behavior filtered engagements live at
        M6 time, so a later rejection silently removed an engagement (and its evidence
        attribution) from an *already-created* FitAssessment's Agent Builder context. The pinned
        roster must not drift: an engagement approved when the manifest was created keeps
        appearing, and a claim's approved_engagement_ids keeps reflecting the mapping as it stood
        at that time, even after the engagement is later rejected."""
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="x")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        fit_assessment = _pinned_fit_assessment(
            rev, approved_engagements=[engagement], job_relevant_claim_ids=[claim.claim_id]
        )

        engagement.approval_status = engagement.ApprovalStatus.REJECTED
        engagement.save(update_fields=["approval_status"])

        context = build_builder_context(fit_assessment)
        self.assertEqual(context.engagement_ids, [engagement.engagement_id])
        self.assertEqual(context.claims[0].approved_engagement_ids, (engagement.engagement_id,))

    def test_mapping_status_change_after_manifest_creation_does_not_alter_reconstruction(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="x")
        mapping = ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        fit_assessment = _pinned_fit_assessment(
            rev, approved_engagements=[engagement], job_relevant_claim_ids=[claim.claim_id]
        )

        mapping.status = ClaimEngagementMapping.Status.REJECTED
        mapping.save(update_fields=["status"])

        context = build_builder_context(fit_assessment)
        # Pinned at manifest-creation time: still approved for this engagement, regardless of the
        # mapping's current live status.
        self.assertEqual(context.claims[0].approved_engagement_ids, (engagement.engagement_id,))

    def test_empty_manifest_produces_empty_context(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        fit_assessment = _pinned_fit_assessment(rev)
        context = build_builder_context(fit_assessment)
        self.assertEqual(context.claims, [])
        self.assertEqual(context.rules, [])
        self.assertEqual(context.engagements, [])

    def test_legacy_fit_assessment_with_no_pinned_identity_fails_closed(self):
        fit_assessment = FitAssessment(
            based_on_jra=_make_jra(), retrieved_claim_ids=[], retrieved_engagement_ids=[]
        )
        with self.assertRaises(LegacyFitAssessmentManifestError):
            build_builder_context(fit_assessment)
