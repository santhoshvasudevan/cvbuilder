"""Gate-1 feedback re-run targeting Agent Jobber (M5): `rerun_analysis` creates a new, append-only
JobRequirementAnalysis version and repoints JobApplication.current_jra without touching
pipeline_phase (see reviews/services.py's caller and job_applications.models.record_jra)."""

from __future__ import annotations

from django.test import TestCase

from llm_provider.models import LLMCallLog

from ..models import JobRequirementAnalysis
from ..services.intake import IntakeValidationError, rerun_analysis, run_intake
from .factories import DEFAULT_POSTING_TEXT, scripted_analysis, valid_analysis_response


class RerunAnalysisTests(TestCase):
    def _resolve(self):
        from ..services.intake import resolve_posting_source

        return resolve_posting_source(url="", pasted_text=DEFAULT_POSTING_TEXT)

    def _make_application(self):
        with scripted_analysis(valid_analysis_response()):
            return run_intake(self._resolve())

    def test_raises_without_an_existing_jra(self):
        from job_applications.models import JobApplication

        application = JobApplication.objects.create()
        with self.assertRaises(IntakeValidationError):
            rerun_analysis(application)

    def test_reruns_against_the_same_original_input_by_default(self):
        application = self._make_application()
        original_input = application.current_jra.original_input

        with scripted_analysis(valid_analysis_response(role_title="Revised Title")):
            jra = rerun_analysis(application)

        self.assertEqual(jra.version, 2)
        self.assertEqual(jra.original_input, original_input)
        self.assertEqual(jra.role_title, "Revised Title")

    def test_repoints_current_jra_without_touching_pipeline_phase(self):
        application = self._make_application()
        phase_before = application.pipeline_phase

        with scripted_analysis(valid_analysis_response()):
            jra = rerun_analysis(application)

        application.refresh_from_db()
        self.assertEqual(application.current_jra_id, jra.pk)
        self.assertEqual(application.pipeline_phase, phase_before)

    def test_previous_version_remains_on_record(self):
        application = self._make_application()
        first_version_id = application.current_jra_id

        with scripted_analysis(valid_analysis_response()):
            rerun_analysis(application)

        self.assertTrue(JobRequirementAnalysis.objects.filter(pk=first_version_id).exists())
        self.assertEqual(application.job_requirement_analyses.count(), 2)

    def test_rerun_with_new_pasted_text_uses_it(self):
        application = self._make_application()

        # requirements/screening_risks cleared: this short, non-substantive new posting text
        # shares no vocabulary with the default fixture's source_context quotations, and the
        # test only cares about original_input/employer routing, not extraction quality.
        with scripted_analysis(
            valid_analysis_response(employer="New Employer Inc", requirements=[], screening_risks=[])
        ):
            jra = rerun_analysis(application, pasted_text="A completely different posting text.")

        self.assertEqual(jra.original_input, "A completely different posting text.")
        self.assertEqual(jra.employer, "New Employer Inc")

    def test_failed_rerun_still_logs_the_llm_call(self):
        application = self._make_application()
        before = LLMCallLog.objects.count()

        with scripted_analysis({"employer": "x"}):  # missing required fields -> schema validation error
            with self.assertRaises(Exception):
                rerun_analysis(application)

        self.assertEqual(LLMCallLog.objects.count(), before + 1)
        self.assertEqual(application.job_requirement_analyses.count(), 1)
