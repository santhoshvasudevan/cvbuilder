"""Integration-level coverage of `run_intake`'s integrity gate (2026-09-04 AJ hardening, D-022;
course-corrected 2026-09-04, D-023): a schema-valid but integrity-rejected `AgentJobberAnalysis`
must never create a JobApplication, JobRequirementAnalysis, or JobRequirement, and must never
advance the pipeline -- while the underlying (successful) LLM call is still logged, without any
raw posting content. These tests cover *objective* behavior only -- no test here asserts that any
particular wording is correct or incorrect, per D-023."""

from __future__ import annotations

from django.test import TestCase

from job_applications.models import JobApplication
from llm_provider.models import LLMCallLog

from ..models import JobRequirement, JobRequirementAnalysis
from ..services.intake import AnalysisIntegrityError, resolve_posting_source, run_intake
from .factories import DEFAULT_POSTING_TEXT, scripted_analysis, valid_analysis_response

# Mirrors the real JobApplication id=9 failure: a substantive posting, a schema-valid response,
# and zero requirements. Under D-023's minimal rules this is rejected for exactly one objective
# reason -- zero requirements -- regardless of what the (also present, real) screening risks say.
SUBSTANTIVE_POSTING = (
    "Senior Generative AI Solutions Architect. You will design and implement generative AI "
    "architectures that orchestrate complex workflows across enterprise systems. Experience "
    "with cloud-based machine learning platforms is required. You will mentor team members and "
    "contribute to knowledge sharing within the organization."
)


def _observed_failure_shape_response() -> dict:
    return {
        "employer": "",
        "role_title": "Senior Generative AI Solutions Architect",
        "posting_language": "English",
        "requirements": [],
        "screening_risks": [
            {
                "text": "Lack of hands-on experience with cloud generative AI services.",
                "source_context": "generative AI architectures",
            },
            {
                "text": "No proven ability to design and implement generative AI architectures "
                "for enterprise workloads.",
                "source_context": "design and implement generative AI",
            },
        ],
    }


class ZeroRequirementsAlwaysRejectedTests(TestCase):
    def test_exact_observed_failure_shape_raises_and_creates_nothing(self):
        with scripted_analysis(_observed_failure_shape_response()):
            resolved = resolve_posting_source(url="", pasted_text=SUBSTANTIVE_POSTING)
            with self.assertRaises(AnalysisIntegrityError):
                run_intake(resolved)

        self.assertEqual(JobApplication.objects.count(), 0)
        self.assertEqual(JobRequirementAnalysis.objects.count(), 0)
        self.assertEqual(JobRequirement.objects.count(), 0)

    def test_zero_requirements_on_a_short_posting_is_also_rejected(self):
        with scripted_analysis(
            {
                "employer": "",
                "role_title": "",
                "posting_language": "en",
                "requirements": [],
                "screening_risks": [],
            }
        ):
            resolved = resolve_posting_source(url="", pasted_text="A short, real one-line posting.")
            with self.assertRaises(AnalysisIntegrityError):
                run_intake(resolved)
        self.assertEqual(JobApplication.objects.count(), 0)

    def test_no_pipeline_phase_advancement_occurs_on_rejection(self):
        with scripted_analysis(_observed_failure_shape_response()):
            resolved = resolve_posting_source(url="", pasted_text=SUBSTANTIVE_POSTING)
            with self.assertRaises(AnalysisIntegrityError):
                run_intake(resolved)
        self.assertEqual(JobApplication.objects.count(), 0)


class AtomicFailureRetainsSanitizedCallLogTests(TestCase):
    def test_underlying_llm_call_is_still_logged_without_raw_posting_content(self):
        with scripted_analysis(_observed_failure_shape_response()):
            resolved = resolve_posting_source(url="", pasted_text=SUBSTANTIVE_POSTING)
            with self.assertRaises(AnalysisIntegrityError):
                run_intake(resolved)

        self.assertEqual(LLMCallLog.objects.count(), 1)
        log = LLMCallLog.objects.get()
        # The underlying provider call itself succeeded (schema-valid) -- only the integrity gate
        # rejected it, entirely in application code after the call, so the log carries no
        # error_category from the provider's own perspective.
        self.assertFalse(log.error_category)
        for field in log._meta.fields:
            value = getattr(log, field.name)
            if isinstance(value, str):
                self.assertNotIn("Senior Generative AI Solutions Architect", value)
                self.assertNotIn("mentor team members", value)


class ValidOneRequirementOutputIsAcceptedTests(TestCase):
    def test_a_single_well_supported_requirement_is_persisted(self):
        with scripted_analysis(
            {
                "employer": "Example Corp",
                "role_title": "Backend Engineer",
                "posting_language": "en",
                "requirements": [
                    {
                        "category": "MANDATORY",
                        "text": "5+ years of Python experience",
                        "source_context": "Must have 5+ years of Python experience.",
                    }
                ],
                "screening_risks": [],
            }
        ):
            resolved = resolve_posting_source(
                url="", pasted_text="Backend role. Must have 5+ years of Python experience."
            )
            application = run_intake(resolved)
        self.assertEqual(application.current_jra.requirements.count(), 1)

    def test_valid_mandatory_preferred_extraction_still_persists_normally(self):
        with scripted_analysis(valid_analysis_response()):
            resolved = resolve_posting_source(url="", pasted_text=DEFAULT_POSTING_TEXT)
            application = run_intake(resolved)
        self.assertEqual(application.current_jra.requirements.count(), 5)


class ProvenanceIntegrationTests(TestCase):
    def test_valid_provenance_is_accepted(self):
        with scripted_analysis(valid_analysis_response()):
            resolved = resolve_posting_source(url="", pasted_text=DEFAULT_POSTING_TEXT)
            application = run_intake(resolved)
        self.assertTrue(application.current_jra.requirements.exists())

    def test_invalid_provenance_is_rejected(self):
        response = valid_analysis_response()
        response["requirements"][0]["source_context"] = "This sentence appears nowhere in the posting."
        with scripted_analysis(response):
            resolved = resolve_posting_source(url="", pasted_text=DEFAULT_POSTING_TEXT)
            with self.assertRaises(AnalysisIntegrityError):
                run_intake(resolved)
        self.assertEqual(JobRequirementAnalysis.objects.count(), 0)


class DuplicateRequirementIsRejectedTests(TestCase):
    def test_exact_duplicate_requirement_from_the_same_statement_is_rejected(self):
        response = valid_analysis_response()
        duplicate = dict(response["requirements"][0])
        response["requirements"] = [response["requirements"][0], duplicate]
        with scripted_analysis(response):
            resolved = resolve_posting_source(url="", pasted_text=DEFAULT_POSTING_TEXT)
            with self.assertRaises(AnalysisIntegrityError):
                run_intake(resolved)
        self.assertEqual(JobRequirementAnalysis.objects.count(), 0)


class LegitimateWordingIsNeverRejectedTests(TestCase):
    """D-023's central correction, exercised through the full `run_intake` path: quoted posting
    language that would have tripped D-022's removed GAP_LANGUAGE_MARKERS check must persist
    normally as long as it has real provenance."""

    def test_no_experience_necessary_wording_persists_normally(self):
        posting = "Junior role. No experience necessary with legacy mainframe systems."
        with scripted_analysis(
            {
                "employer": "",
                "role_title": "",
                "posting_language": "en",
                "requirements": [
                    {
                        "category": "PREFERRED",
                        "text": "No experience necessary with legacy mainframe systems",
                        "source_context": "No experience necessary with legacy mainframe systems.",
                    }
                ],
                "screening_risks": [],
            }
        ):
            resolved = resolve_posting_source(url="", pasted_text=posting)
            application = run_intake(resolved)
        self.assertEqual(application.current_jra.requirements.count(), 1)
