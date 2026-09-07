"""D-037 Phase F: the authoritative full-request budget check in
`services/generate.py::generate_resume_content` -- counts the complete assembled request (system
prompt, JRA text, requirement explanations, all evidence, and the output schema), runs after
`build_request` and before any provider HTTP call, and never truncates evidence to fit.
"""

from __future__ import annotations

from unittest import mock

from django.test import TestCase

from candidate_matching.models import FitAssessment
from candidate_matching.services.baseline_chronology import build_manifest_for_job_relevant_claim_ids
from candidate_matching.services.retrieval_limits import (
    MAX_ESTIMATED_REQUEST_TOKENS,
    RetrievalBudgetExceededError,
)
from candidate_memory.models import CandidateMemory, ClaimEngagementMapping
from job_applications.models import JobApplication
from job_intake.models import JobRequirementAnalysis

from ..services.build import ResumeBuilderError, build_resume_draft
from ..services.context import build_builder_context
from ..services.generate import generate_resume_content
from .factories import (
    freeze_revision,
    make_engagement,
    make_fake_stage_assignment,
    make_narrative_claim,
    make_revision,
    scripted_generation,
    valid_generation_response,
)


def _make_jra(**kwargs) -> JobRequirementAnalysis:
    application = JobApplication.objects.create()
    defaults = dict(
        job_application=application, version=1,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="x" * 25, extracted_text="x" * 25,
        extracted_text_sha256="0" * 64, posting_language="en",
        employer="Globex Corporation", role_title="Senior Backend Engineer",
    )
    defaults.update(kwargs)
    return JobRequirementAnalysis.objects.create(**defaults)


class _FakeRequirementAssessment:
    def __init__(self, requirement_id, disposition, explanation):
        self.requirement_id = requirement_id
        self.disposition = disposition
        self.explanation = explanation


class FullRequestBudgetTests(TestCase):
    def _pinned_context(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service end to end.")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        manifest = build_manifest_for_job_relevant_claim_ids(rev, [engagement], [claim.claim_id])
        fit_assessment = FitAssessment(
            based_on_jra=_make_jra(), based_on_candidate_memory=rev,
            retrieved_claim_ids=[claim.claim_id], retrieved_engagement_ids=[engagement.engagement_id],
            baseline_chronology_manifest=manifest,
        )
        return build_builder_context(fit_assessment), fit_assessment

    def test_a_normal_small_request_passes(self):
        retrieval, fit_assessment = self._pinned_context()
        make_fake_stage_assignment()
        assessments = [
            _FakeRequirementAssessment("JR-001", "MATCH", "Directly owned an equivalent service.")
        ]
        response = valid_generation_response(retrieval.engagement_ids[0], retrieval.claim_ids[0])
        with scripted_generation(response):
            result = generate_resume_content(fit_assessment.based_on_jra, assessments, retrieval)
        self.assertFalse(result.is_error)

    def test_large_requirement_explanations_are_counted_and_fail_before_http(self):
        """The pre-D-037 estimator (`services/context.py`'s own partial check) counted only claim/
        rule/engagement text -- never JRA role/employer or RequirementAssessment explanations. A
        single, very large explanation must now be enough to trip the authoritative full-request
        check on its own, even though the pinned evidence itself is tiny."""
        retrieval, fit_assessment = self._pinned_context()
        make_fake_stage_assignment()
        huge_explanation = "x" * (MAX_ESTIMATED_REQUEST_TOKENS * 5)
        assessments = [_FakeRequirementAssessment("JR-001", "MATCH", huge_explanation)]

        with mock.patch("resume_builder.services.generate.get_adapter_for_stage") as mock_get_adapter:
            with self.assertRaises(RetrievalBudgetExceededError):
                generate_resume_content(fit_assessment.based_on_jra, assessments, retrieval)
            # The provider was never even asked for an adapter/model -- proves this fails before
            # any HTTP call could be attempted, not merely before a hypothetical network error.
            mock_get_adapter.return_value.generate.assert_not_called()

    def test_over_budget_request_never_drops_evidence_it_simply_refuses(self):
        retrieval, fit_assessment = self._pinned_context()
        make_fake_stage_assignment()
        huge_explanation = "x" * (MAX_ESTIMATED_REQUEST_TOKENS * 5)
        assessments = [_FakeRequirementAssessment("JR-001", "MATCH", huge_explanation)]
        original_claim_ids = list(retrieval.claim_ids)

        with self.assertRaises(RetrievalBudgetExceededError):
            generate_resume_content(fit_assessment.based_on_jra, assessments, retrieval)

        # retrieval itself (what would have been sent) is untouched -- nothing was mutated/trimmed.
        self.assertEqual(retrieval.claim_ids, original_claim_ids)

    def test_build_resume_draft_surfaces_the_budget_error_as_resume_builder_error(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service end to end.")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        application = JobApplication.objects.create(pipeline_phase=JobApplication.PipelinePhase.NEW)
        jra = _make_jra(job_application=application)
        from job_intake.models import JobRequirement

        huge_text = "Own the payments service end to end. " + "x" * (MAX_ESTIMATED_REQUEST_TOKENS * 5)
        JobRequirement.objects.create(
            job_requirement_analysis=jra, requirement_id="JR-001", order=1, category="MANDATORY",
            text=huge_text,
        )
        application.advance_to_analysis(jra=jra)
        manifest = build_manifest_for_job_relevant_claim_ids(rev, [engagement], [claim.claim_id])
        fit_assessment = FitAssessment.objects.create(
            job_application=application, version=1, based_on_jra=jra, based_on_candidate_memory=rev,
            retrieved_claim_ids=[claim.claim_id], retrieved_engagement_ids=[engagement.engagement_id],
            baseline_chronology_manifest=manifest,
        )
        from candidate_matching.models import RequirementAssessment

        RequirementAssessment.objects.create(
            fit_assessment=fit_assessment, requirement_id="JR-001",
            disposition=RequirementAssessment.Disposition.MATCH,
            supporting_memory_claim_ids=[claim.claim_id],
            explanation=huge_text,
        )
        application.record_fit_assessment(fit_assessment)
        application.approve_gate1()

        make_fake_stage_assignment()
        with self.assertRaises(ResumeBuilderError):
            build_resume_draft(application)
        application.refresh_from_db()
        self.assertIsNone(application.current_resume_draft)
