from __future__ import annotations

from django.test import TestCase

from candidate_matching.models import FitAssessment
from job_intake.models import JobRequirementAnalysis
from resume_builder.models import ResumeDraft

from ..models import GateNotReadyError, InvalidPhaseTransitionError, JobApplication, StaleAssessmentError


def _make_jra(application: JobApplication, version: int = 1) -> JobRequirementAnalysis:
    return JobRequirementAnalysis.objects.create(
        job_application=application, version=version,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="posting text", extracted_text="posting text",
        extracted_text_sha256="0" * 64, posting_language="en",
    )


def _prepared_application() -> JobApplication:
    application = JobApplication.objects.create()
    jra = _make_jra(application)
    application.advance_to_analysis(jra=jra)
    fit_assessment = FitAssessment.objects.create(job_application=application, version=1, based_on_jra=jra)
    application.record_fit_assessment(fit_assessment)
    application.approve_gate1()
    return application


class RecordResumeDraftTests(TestCase):
    def test_sets_current_resume_draft(self):
        application = _prepared_application()
        draft = ResumeDraft.objects.create(
            job_application=application, version=1,
            based_on_fit_assessment=application.current_fit_assessment,
            recommended_title="Engineer", rendered_markdown="# Engineer\n",
        )
        application.record_resume_draft(draft)
        application.refresh_from_db()
        self.assertEqual(application.current_resume_draft_id, draft.pk)


class ApproveGate2Tests(TestCase):
    def test_raises_without_a_resume_draft(self):
        application = _prepared_application()
        with self.assertRaises(GateNotReadyError):
            application.approve_gate2()

    def test_raises_when_not_in_preparation_phase(self):
        application = JobApplication.objects.create()
        with self.assertRaises(InvalidPhaseTransitionError):
            application.approve_gate2()

    def test_raises_when_draft_is_stale(self):
        application = _prepared_application()
        draft = ResumeDraft.objects.create(
            job_application=application, version=1,
            based_on_fit_assessment=application.current_fit_assessment,
            recommended_title="Engineer", rendered_markdown="# Engineer\n",
        )
        application.record_resume_draft(draft)

        # A new FitAssessment lands without a corresponding new ResumeDraft.
        new_fit_assessment = FitAssessment.objects.create(
            job_application=application, version=2, based_on_jra=application.current_jra
        )
        application.record_fit_assessment(new_fit_assessment)

        with self.assertRaises(StaleAssessmentError):
            application.approve_gate2()

    def test_approving_confirms_the_draft_and_advances_to_ready(self):
        application = _prepared_application()
        draft = ResumeDraft.objects.create(
            job_application=application, version=1,
            based_on_fit_assessment=application.current_fit_assessment,
            recommended_title="Engineer", rendered_markdown="# Engineer\n",
        )
        application.record_resume_draft(draft)

        application.approve_gate2()

        application.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.READY)
        self.assertIsNotNone(draft.confirmed_at)

    def test_cannot_approve_gate2_twice(self):
        application = _prepared_application()
        draft = ResumeDraft.objects.create(
            job_application=application, version=1,
            based_on_fit_assessment=application.current_fit_assessment,
            recommended_title="Engineer", rendered_markdown="# Engineer\n",
        )
        application.record_resume_draft(draft)
        application.approve_gate2()

        with self.assertRaises(InvalidPhaseTransitionError):
            application.approve_gate2()
