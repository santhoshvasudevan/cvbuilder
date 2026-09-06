from __future__ import annotations

from django.test import TestCase

from candidate_matching.models import FitAssessment
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


class BuildBuilderContextTests(TestCase):
    def test_only_includes_claims_the_fit_assessment_actually_selected(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        selected = make_narrative_claim(rev, canonical_text_en="Selected claim.")
        not_selected = make_narrative_claim(rev, canonical_text_en="Not selected claim.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        fit_assessment = FitAssessment(
            based_on_jra=_make_jra(), retrieved_claim_ids=[selected.claim_id], retrieved_engagement_ids=[]
        )
        context = build_builder_context(fit_assessment)
        self.assertEqual(context.claim_ids, [selected.claim_id])
        self.assertNotIn(not_selected.claim_id, context.claim_ids)

    def test_excludes_a_claim_that_was_retired_since_the_fit_assessment_ran(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        claim = make_narrative_claim(rev, canonical_text_en="Will be retired.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        # Simulate retirement after the FitAssessment already selected it, via a direct bulk
        # update (the same "known, accepted bypass" every _RevisionScopedModel guard already
        # documents) -- this is a read-only test of build_builder_context's own re-verification,
        # not a claim that retiring an ACTIVE revision's claim is a normal supported action.
        MemoryClaim.objects.filter(pk=claim.pk).update(
            confirmation_status=MemoryClaim.ConfirmationStatus.RETIRED
        )

        fit_assessment = FitAssessment(
            based_on_jra=_make_jra(), retrieved_claim_ids=[claim.claim_id], retrieved_engagement_ids=[]
        )
        context = build_builder_context(fit_assessment)
        self.assertEqual(context.claims, [])

    def test_tags_a_claim_with_its_approved_engagement(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="Engagement-mapped claim.")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        fit_assessment = FitAssessment(
            based_on_jra=_make_jra(),
            retrieved_claim_ids=[claim.claim_id], retrieved_engagement_ids=[engagement.engagement_id]
        )
        context = build_builder_context(fit_assessment)
        self.assertEqual(context.claims[0].approved_engagement_ids, (engagement.engagement_id,))
        self.assertEqual(context.engagement_ids, [engagement.engagement_id])

    def test_engagement_no_longer_approved_is_excluded(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="x")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        engagement.approval_status = engagement.ApprovalStatus.REJECTED
        engagement.save(update_fields=["approval_status"])

        fit_assessment = FitAssessment(
            based_on_jra=_make_jra(),
            retrieved_claim_ids=[claim.claim_id], retrieved_engagement_ids=[engagement.engagement_id]
        )
        context = build_builder_context(fit_assessment)
        self.assertEqual(context.engagements, [])
        self.assertEqual(context.claims[0].approved_engagement_ids, ())

    def test_empty_retrieved_claim_ids_produces_empty_context(self):
        fit_assessment = FitAssessment(retrieved_claim_ids=[], retrieved_engagement_ids=[])
        context = build_builder_context(fit_assessment)
        self.assertEqual(context.claims, [])
        self.assertEqual(context.rules, [])
