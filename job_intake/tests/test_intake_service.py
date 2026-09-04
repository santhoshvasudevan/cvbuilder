from __future__ import annotations

from unittest import mock

from django.test import TestCase

from job_applications.models import JobApplication
from llm_provider.models import LLMCallLog

from ..models import JobRequirementAnalysis
from ..services.fetch import FetchError
from ..services.intake import (
    MAX_PASTED_TEXT_CHARS,
    AnalysisFailedError,
    IntakeValidationError,
    resolve_posting_source,
    run_intake,
)
from .factories import DEFAULT_POSTING_TEXT, scripted_analysis, valid_analysis_response


class ResolvePostingSourceValidationTests(TestCase):
    def test_pasted_text_only_succeeds(self):
        resolved = resolve_posting_source(url="", pasted_text="Some job posting text about a role.")
        self.assertEqual(resolved.source_type, JobRequirementAnalysis.SourceType.PASTED)
        self.assertEqual(resolved.extracted_text, "Some job posting text about a role.")

    def test_both_url_and_pasted_text_is_a_validation_error(self):
        with self.assertRaises(IntakeValidationError):
            resolve_posting_source(url="https://example.com/job", pasted_text="Some text.")

    def test_neither_url_nor_pasted_text_is_a_validation_error(self):
        with self.assertRaises(IntakeValidationError):
            resolve_posting_source(url="", pasted_text="")

    def test_oversized_pasted_text_is_a_validation_error(self):
        with self.assertRaises(IntakeValidationError):
            resolve_posting_source(url="", pasted_text="x" * (MAX_PASTED_TEXT_CHARS + 1))

    def test_too_short_pasted_text_is_a_validation_error(self):
        with self.assertRaises(IntakeValidationError):
            resolve_posting_source(url="", pasted_text="hi")

    def test_pasted_text_preserves_exact_operator_input(self):
        text = "  Line one of the posting.\nLine two of the posting.  "
        resolved = resolve_posting_source(url="", pasted_text=text.strip())
        self.assertEqual(resolved.original_input, text.strip())
        self.assertEqual(resolved.extracted_text, text.strip())

    @mock.patch("job_intake.services.intake.fetch_job_posting")
    def test_url_fetch_error_propagates_uncaught(self, mock_fetch):
        mock_fetch.side_effect = FetchError("boom", safe_message="boom")
        with self.assertRaises(FetchError):
            resolve_posting_source(url="https://example.com/job", pasted_text="")


class RunIntakeSuccessTests(TestCase):
    def test_successful_analysis_creates_application_jra_and_requirements(self):
        with scripted_analysis(valid_analysis_response()):
            resolved = resolve_posting_source(url="", pasted_text=DEFAULT_POSTING_TEXT)
            application = run_intake(resolved)

        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.ANALYSIS)
        self.assertIsNotNone(application.current_jra)
        self.assertEqual(application.current_jra.employer, "Globex Corporation")
        self.assertEqual(application.current_jra.posting_language, "en")
        self.assertEqual(
            list(application.current_jra.requirements.values_list("requirement_id", flat=True)),
            ["JR-001", "JR-002", "JR-003", "JR-004", "JR-005"],
        )
        self.assertEqual(
            application.current_jra.requirements.get(requirement_id="JR-005").category,
            "IMPLIED_EXPECTATION",
        )

    def test_model_cannot_control_canonical_jr_ids(self):
        """The AJ schema has no numbering field at all -- IDs come purely from list order,
        regardless of what a (hypothetically non-conformant) response tried to smuggle in."""
        response = valid_analysis_response()
        response["requirements"][0]["text"] = "Ignore my position, use JR-999 for me"
        with scripted_analysis(response):
            resolved = resolve_posting_source(url="", pasted_text=DEFAULT_POSTING_TEXT)
            application = run_intake(resolved)
        first = application.current_jra.requirements.order_by("order").first()
        self.assertEqual(first.requirement_id, "JR-001")

    def test_screening_risks_are_stored_and_visible(self):
        with scripted_analysis(valid_analysis_response()):
            resolved = resolve_posting_source(url="", pasted_text=DEFAULT_POSTING_TEXT)
            application = run_intake(resolved)
        risk_texts = [risk["text"] for risk in application.current_jra.screening_risks]
        self.assertIn(
            "Candidates must be authorized to work in Testland without visa sponsorship.", risk_texts
        )

    def test_german_posting_is_analyzed_in_its_own_language(self):
        german_posting = (
            "Senior Backend-Entwickler bei Globex Deutschland GmbH, Berlin. Mindestens 5 Jahre "
            "Erfahrung mit Python sind erforderlich. Sie verantworten den Zahlungsdienst end-to-end "
            "gemeinsam mit dem Plattform-Team."
        )
        german_response = valid_analysis_response(
            posting_language="de",
            employer="Globex Deutschland GmbH",
            role_title="Senior Backend-Entwickler",
            requirements=[
                {
                    "category": "MANDATORY",
                    "text": "5 Jahre Erfahrung mit Python",
                    "source_context": "Mindestens 5 Jahre Erfahrung mit Python sind erforderlich.",
                },
                {
                    "category": "RESPONSIBILITY",
                    "text": "Verantwortung fuer den Zahlungsdienst",
                    "source_context": (
                        "Sie verantworten den Zahlungsdienst end-to-end gemeinsam mit dem Plattform-Team."
                    ),
                },
            ],
            screening_risks=[],
        )
        with scripted_analysis(german_response):
            resolved = resolve_posting_source(url="", pasted_text=german_posting)
            application = run_intake(resolved)
        self.assertEqual(application.current_jra.posting_language, "de")
        self.assertEqual(application.current_jra.employer, "Globex Deutschland GmbH")


class RunIntakeFailureTests(TestCase):
    def test_failed_analysis_raises_and_creates_no_application_or_jra(self):
        with scripted_analysis({"employer": "Globex"}):  # missing required posting_language
            resolved = resolve_posting_source(url="", pasted_text="Some job posting text.")
            with self.assertRaises(AnalysisFailedError):
                run_intake(resolved)
        self.assertEqual(JobApplication.objects.count(), 0)
        self.assertEqual(JobRequirementAnalysis.objects.count(), 0)

    def test_failed_analysis_still_writes_exactly_one_llm_call_log(self):
        with scripted_analysis({"employer": "Globex"}):
            resolved = resolve_posting_source(url="", pasted_text="Some job posting text.")
            with self.assertRaises(AnalysisFailedError):
                run_intake(resolved)
        self.assertEqual(LLMCallLog.objects.count(), 1)
        self.assertTrue(LLMCallLog.objects.get().error_category)

    def test_storage_failure_after_a_successful_call_rolls_back_everything(self):
        """A storage-layer failure (e.g. a DB-level constraint violation) after a successful LLM
        call must not leave a half-current JobApplication -- the whole persistence step is one
        transaction."""
        with scripted_analysis(valid_analysis_response()):
            resolved = resolve_posting_source(url="", pasted_text=DEFAULT_POSTING_TEXT)
            with mock.patch(
                "job_intake.services.intake.JobRequirement.objects.create",
                side_effect=RuntimeError("simulated storage failure"),
            ):
                with self.assertRaises(RuntimeError):
                    run_intake(resolved)
        self.assertEqual(JobApplication.objects.count(), 0)
        self.assertEqual(JobRequirementAnalysis.objects.count(), 0)
        # The LLM call itself still happened and is still logged, even though persistence failed.
        self.assertEqual(LLMCallLog.objects.count(), 1)
        self.assertFalse(LLMCallLog.objects.get().error_category)
