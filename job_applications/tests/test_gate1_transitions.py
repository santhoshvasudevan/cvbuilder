from __future__ import annotations

from django.test import TestCase

from candidate_matching.models import FitAssessment
from job_intake.models import JobRequirementAnalysis

from ..models import GateNotReadyError, JobApplication, StaleAssessmentError


def _make_jra(application: JobApplication, version: int = 1) -> JobRequirementAnalysis:
    return JobRequirementAnalysis.objects.create(
        job_application=application,
        version=version,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="posting text",
        extracted_text="posting text",
        extracted_text_sha256="0" * 64,
        posting_language="en",
    )


class RecordJraTests(TestCase):
    def test_repoints_current_jra_without_changing_phase(self):
        application = JobApplication.objects.create()
        jra1 = _make_jra(application)
        application.advance_to_analysis(jra=jra1)
        jra2 = _make_jra(application, version=2)

        application.record_jra(jra2)
        application.refresh_from_db()
        self.assertEqual(application.current_jra_id, jra2.pk)
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.ANALYSIS)


class RecordFitAssessmentTests(TestCase):
    def test_sets_current_fit_assessment(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        fit_assessment = FitAssessment.objects.create(
            job_application=application, version=1, based_on_jra=jra
        )

        application.record_fit_assessment(fit_assessment)
        application.refresh_from_db()
        self.assertEqual(application.current_fit_assessment_id, fit_assessment.pk)


class ApproveGate1Tests(TestCase):
    def _application_with_fit_assessment(self, *, based_on_jra=None):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        fit_assessment = FitAssessment.objects.create(
            job_application=application, version=1, based_on_jra=based_on_jra or jra
        )
        application.record_fit_assessment(fit_assessment)
        return application

    def test_raises_without_a_fit_assessment(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        with self.assertRaises(GateNotReadyError):
            application.approve_gate1()

    def test_raises_when_fit_assessment_is_stale(self):
        application = JobApplication.objects.create()
        jra1 = _make_jra(application)
        application.advance_to_analysis(jra=jra1)
        fit_assessment = FitAssessment.objects.create(
            job_application=application, version=1, based_on_jra=jra1
        )
        application.record_fit_assessment(fit_assessment)

        jra2 = _make_jra(application, version=2)
        application.record_jra(jra2)

        with self.assertRaises(StaleAssessmentError):
            application.approve_gate1()

    def test_approves_and_advances_to_preparation(self):
        application = self._application_with_fit_assessment()
        application.approve_gate1()
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.PREPARATION)

    def test_approving_twice_is_an_idempotent_no_op(self):
        application = self._application_with_fit_assessment()
        application.approve_gate1()
        application.approve_gate1()  # must not raise
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.PREPARATION)

    def test_cannot_approve_when_a_newer_stale_assessment_supersedes_the_approved_one(self):
        application = self._application_with_fit_assessment()
        application.approve_gate1()

        jra2 = _make_jra(application, version=2)
        application.record_jra(jra2)

        with self.assertRaises(StaleAssessmentError):
            application.approve_gate1()
