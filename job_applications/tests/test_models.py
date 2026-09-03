from __future__ import annotations

from django.test import TestCase

from job_intake.models import JobRequirementAnalysis

from ..models import InvalidPhaseTransitionError, JobApplication


def _make_jra(application: JobApplication) -> JobRequirementAnalysis:
    return JobRequirementAnalysis.objects.create(
        job_application=application,
        version=1,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="posting text",
        extracted_text="posting text",
        extracted_text_sha256="0" * 64,
        posting_language="en",
    )


class JobApplicationDefaultsTests(TestCase):
    def test_new_application_starts_new_and_not_applied(self):
        application = JobApplication.objects.create()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.NEW)
        self.assertEqual(application.application_outcome, JobApplication.ApplicationOutcome.NOT_APPLIED)
        self.assertIsNone(application.current_jra)


class AdvanceToAnalysisTests(TestCase):
    def test_advances_from_new_to_analysis_and_sets_current_jra(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.ANALYSIS)
        self.assertEqual(application.current_jra_id, jra.pk)

    def test_rejects_advancing_when_not_in_new_phase(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)

        jra2 = JobRequirementAnalysis.objects.create(
            job_application=application, version=2,
            source_type=JobRequirementAnalysis.SourceType.PASTED,
            original_input="x", extracted_text="x", extracted_text_sha256="1" * 64,
            posting_language="en",
        )
        with self.assertRaises(InvalidPhaseTransitionError):
            application.advance_to_analysis(jra=jra2)

    def test_application_outcome_is_independent_of_pipeline_phase(self):
        application = JobApplication.objects.create(
            application_outcome=JobApplication.ApplicationOutcome.APPLIED
        )
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.ANALYSIS)
        self.assertEqual(application.application_outcome, JobApplication.ApplicationOutcome.APPLIED)
