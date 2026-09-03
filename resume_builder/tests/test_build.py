from __future__ import annotations

from django.test import TestCase

from candidate_matching.services.retrieve import NoActiveCandidateMemoryError
from job_applications.models import JobApplication

from ..models import ResumeElement
from ..services.build import ResumeBuilderError, build_resume_draft
from .factories import make_ready_for_gate2_application, scripted_generation, valid_generation_response


class BuildResumeDraftTests(TestCase):
    def test_raises_without_a_fit_assessment(self):
        application = JobApplication.objects.create()
        with self.assertRaises(ResumeBuilderError):
            build_resume_draft(application)

    def test_raises_when_gate1_not_yet_approved(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        # Force back to ANALYSIS to simulate Gate 1 never having been approved.
        application.pipeline_phase = JobApplication.PipelinePhase.ANALYSIS
        application.save(update_fields=["pipeline_phase"])
        with self.assertRaises(ResumeBuilderError):
            build_resume_draft(application)

    def test_raises_without_an_active_candidate_memory(self):
        from candidate_memory.models import CandidateMemory

        application, claim_id, engagement_id = make_ready_for_gate2_application()
        CandidateMemory.objects.update(status=CandidateMemory.Status.SUPERSEDED)
        with self.assertRaises(NoActiveCandidateMemoryError):
            build_resume_draft(application)

    def test_successful_build_creates_draft_and_elements(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        with scripted_generation(valid_generation_response(engagement_id, claim_id)):
            draft = build_resume_draft(application)

        self.assertEqual(draft.version, 1)
        self.assertEqual(draft.recommended_title, "Senior Backend Engineer")
        self.assertIn("Senior Backend Engineer", draft.rendered_markdown)
        self.assertIn("Owned the payments service end to end.", draft.rendered_markdown)
        self.assertTrue(draft.elements.filter(section=ResumeElement.Section.EXPERIENCE_BULLET).exists())
        application.refresh_from_db()
        self.assertEqual(application.current_resume_draft_id, draft.pk)

    def test_records_exactly_which_claims_and_engagements_were_retrieved(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        with scripted_generation(valid_generation_response(engagement_id, claim_id)):
            draft = build_resume_draft(application)
        self.assertEqual(draft.retrieved_claim_ids, [claim_id])
        self.assertEqual(draft.retrieved_engagement_ids, [engagement_id])

    def test_fabricated_evidence_aborts_the_whole_build_no_draft_persisted(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        bad_response = valid_generation_response(
            engagement_id,
            claim_id,
            summary_elements=[
                {
                    "text": "Fabricated.",
                    "supporting_memory_claim_ids": ["MC-fabricated"],
                    "matched_job_requirement_ids": [],
                }
            ],
        )
        with scripted_generation(bad_response):
            with self.assertRaises(ResumeBuilderError):
                build_resume_draft(application)

        application.refresh_from_db()
        self.assertIsNone(application.current_resume_draft)
        self.assertEqual(application.resume_drafts.count(), 0)

    def test_rerun_creates_a_new_version(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        with scripted_generation(valid_generation_response(engagement_id, claim_id)):
            first = build_resume_draft(application)
            second = build_resume_draft(application)

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        application.refresh_from_db()
        self.assertEqual(application.current_resume_draft_id, second.pk)

    def test_stale_fit_assessment_blocks_build(self):
        application, claim_id, engagement_id = make_ready_for_gate2_application()
        with scripted_generation(valid_generation_response(engagement_id, claim_id)):
            build_resume_draft(application)

        # Simulate a new JRA version landing without a corresponding new FitAssessment.
        from job_intake.models import JobRequirementAnalysis

        new_jra = JobRequirementAnalysis.objects.create(
            job_application=application,
            version=2,
            source_type=JobRequirementAnalysis.SourceType.PASTED,
            original_input="A pasted job posting about a role, revised.",
            extracted_text="A pasted job posting about a role, revised.",
            extracted_text_sha256="1" * 64,
            posting_language="en",
        )
        application.record_jra(new_jra)

        with self.assertRaises(ResumeBuilderError):
            build_resume_draft(application)
