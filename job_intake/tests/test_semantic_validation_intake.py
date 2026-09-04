"""Integration-level coverage of `run_intake`'s semantic sanity gate (2026-09-04 AJ hardening,
D-022): a schema-valid but semantically-rejected `AgentJobberAnalysis` must never create a
JobApplication, JobRequirementAnalysis, or JobRequirement, and must never advance the pipeline --
while the underlying (successful) LLM call is still logged, without any raw posting content."""

from __future__ import annotations

from django.test import TestCase

from job_applications.models import JobApplication
from llm_provider.models import LLMCallLog

from ..models import JobRequirement, JobRequirementAnalysis
from ..services.intake import SemanticValidationError, resolve_posting_source, run_intake
from .factories import DEFAULT_POSTING_TEXT, scripted_analysis, valid_analysis_response

# Mirrors the real JobApplication id=9 failure: a substantive posting, a schema-valid response,
# zero requirements, and several responsibilities/qualifications recast as candidate-gap
# screening risks.
SUBSTANTIVE_POSTING = (
    "Senior Generative AI Solutions Architect. You will design and implement generative AI "
    "architectures that orchestrate complex workflows across enterprise systems. Experience "
    "with cloud-based machine learning platforms is required. You will mentor team members and "
    "contribute to knowledge sharing within the organization. Lead thought leadership "
    "initiatives through public speaking and publishing technical content."
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
            {
                "text": "No track record of mentoring or knowledge-sharing activities.",
                "source_context": "mentor team members",
            },
            {
                "text": "Absence of thought-leadership evidence.",
                "source_context": "thought leadership initiatives",
            },
        ],
    }


class ExactObservedFailureShapeIsRejectedTests(TestCase):
    def test_zero_requirements_with_gap_style_risks_raises_and_creates_nothing(self):
        with scripted_analysis(_observed_failure_shape_response()):
            resolved = resolve_posting_source(url="", pasted_text=SUBSTANTIVE_POSTING)
            with self.assertRaises(SemanticValidationError):
                run_intake(resolved)

        self.assertEqual(JobApplication.objects.count(), 0)
        self.assertEqual(JobRequirementAnalysis.objects.count(), 0)
        self.assertEqual(JobRequirement.objects.count(), 0)

    def test_underlying_llm_call_is_still_logged_without_raw_posting_content(self):
        with scripted_analysis(_observed_failure_shape_response()):
            resolved = resolve_posting_source(url="", pasted_text=SUBSTANTIVE_POSTING)
            with self.assertRaises(SemanticValidationError):
                run_intake(resolved)

        self.assertEqual(LLMCallLog.objects.count(), 1)
        log = LLMCallLog.objects.get()
        # The underlying provider call itself succeeded (schema-valid) -- only the *semantic*
        # sanity check rejected it, which happens entirely in application code after the call, so
        # the log carries no error_category from the provider's own perspective.
        self.assertFalse(log.error_category)
        for field in log._meta.fields:
            value = getattr(log, field.name)
            if isinstance(value, str):
                self.assertNotIn("Senior Generative AI Solutions Architect", value)
                self.assertNotIn("mentor team members", value)

    def test_no_pipeline_phase_advancement_occurs(self):
        with scripted_analysis(_observed_failure_shape_response()):
            resolved = resolve_posting_source(url="", pasted_text=SUBSTANTIVE_POSTING)
            with self.assertRaises(SemanticValidationError):
                run_intake(resolved)
        self.assertEqual(JobApplication.objects.count(), 0)


class ValidAnalysisStillSucceedsTests(TestCase):
    def test_valid_mandatory_preferred_extraction_still_persists_normally(self):
        with scripted_analysis(valid_analysis_response()):
            resolved = resolve_posting_source(url="", pasted_text=DEFAULT_POSTING_TEXT)
            application = run_intake(resolved)
        self.assertEqual(application.current_jra.requirements.count(), 5)


class EmptyNonSubstantiveInputStillSucceedsTests(TestCase):
    def test_short_posting_with_zero_requirements_is_accepted(self):
        with scripted_analysis(
            {
                "employer": "",
                "role_title": "",
                "posting_language": "en",
                "requirements": [],
                "screening_risks": [],
            }
        ):
            resolved = resolve_posting_source(url="", pasted_text="Odd job, apply within, ask for Sam.")
            application = run_intake(resolved)
        self.assertEqual(application.current_jra.requirements.count(), 0)


class DuplicateRequirementIsRejectedTests(TestCase):
    def test_duplicate_requirement_from_the_same_statement_is_rejected(self):
        response = valid_analysis_response()
        duplicate = dict(response["requirements"][0])
        response["requirements"] = [response["requirements"][0], duplicate]
        with scripted_analysis(response):
            resolved = resolve_posting_source(url="", pasted_text=DEFAULT_POSTING_TEXT)
            with self.assertRaises(SemanticValidationError):
                run_intake(resolved)
        self.assertEqual(JobRequirementAnalysis.objects.count(), 0)


class UnsupportedProvenanceIsRejectedTests(TestCase):
    def test_requirement_with_a_quotation_not_in_the_posting_is_rejected(self):
        response = valid_analysis_response()
        response["requirements"][0]["source_context"] = "This sentence appears nowhere in the posting."
        with scripted_analysis(response):
            resolved = resolve_posting_source(url="", pasted_text=DEFAULT_POSTING_TEXT)
            with self.assertRaises(SemanticValidationError):
                run_intake(resolved)
        self.assertEqual(JobRequirementAnalysis.objects.count(), 0)
