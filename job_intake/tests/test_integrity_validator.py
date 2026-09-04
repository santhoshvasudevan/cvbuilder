"""Unit tests for the deterministic integrity validator (2026-09-04 AJ hardening, D-022;
course-corrected 2026-09-04, D-023).

Product-owner boundary (D-023): deterministic code protects objective integrity (at least one
requirement exists, no exact duplicates, verifiable provenance) -- never semantic correctness
(whether a classification, a screening risk, or an implied expectation is the *right* judgment
call). These tests deliberately include a case proving legitimate posting wording is never
rejected merely for its words (`test_quoted_no_experience_necessary_wording_is_never_rejected`)."""

from __future__ import annotations

from django.test import SimpleTestCase

from ..schemas import AgentJobberAnalysis
from ..validators.integrity import find_integrity_violations

# A long, realistic posting -- no length threshold applies to the zero-requirements check anymore,
# but this is still useful as a realistic provenance source for several tests.
LONG_POSTING = (
    "Senior Generative AI Solutions Architect at a large technology company. You will design "
    "and implement generative AI architectures that orchestrate complex workflows across "
    "enterprise systems. Experience with cloud-based machine learning platforms is required. "
    "Experience with agentic AI systems is a plus. You will mentor team members and contribute "
    "to knowledge sharing within the organization. No experience necessary with legacy mainframe "
    "systems."
)

SHORT_POSTING = "Odd job, apply within."


def _analysis(**overrides) -> AgentJobberAnalysis:
    payload = {
        "employer": "Example Corp",
        "role_title": "Senior Generative AI Solutions Architect",
        "posting_language": "en",
        "requirements": [],
        "screening_risks": [],
    }
    payload.update(overrides)
    return AgentJobberAnalysis.model_validate(payload)


def _requirement(category: str, text: str, source_context: str = "") -> dict:
    return {"category": category, "text": text, "source_context": source_context}


def _risk(text: str, source_context: str) -> dict:
    return {"text": text, "source_context": source_context}


class ZeroRequirementsAlwaysRejectedTests(SimpleTestCase):
    """No posting-length threshold: a zero-requirement response is rejected regardless of how
    long or short the posting was (D-023 explicitly removed the D-022 substantive-length gate)."""

    def test_zero_requirements_on_a_long_posting_is_rejected(self):
        violations = find_integrity_violations(_analysis(), LONG_POSTING)
        self.assertTrue(any("Zero requirements" in v for v in violations))

    def test_zero_requirements_on_a_short_posting_is_also_rejected(self):
        violations = find_integrity_violations(_analysis(), SHORT_POSTING)
        self.assertTrue(any("Zero requirements" in v for v in violations))


class ValidOneRequirementOutputIsAcceptedTests(SimpleTestCase):
    def test_a_single_well_supported_requirement_has_no_violations(self):
        analysis = _analysis(
            requirements=[
                _requirement("MANDATORY", "Odd job", "Odd job, apply within."),
            ]
        )
        violations = find_integrity_violations(analysis, SHORT_POSTING)
        self.assertEqual(violations, [])


class ProvenanceTests(SimpleTestCase):
    def test_exact_provenance_is_accepted(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "MANDATORY",
                    "Experience with cloud-based machine learning platforms",
                    "Experience with cloud-based machine learning platforms is required.",
                )
            ]
        )
        violations = find_integrity_violations(analysis, LONG_POSTING)
        self.assertEqual(violations, [])

    def test_missing_provenance_is_rejected(self):
        analysis = _analysis(
            requirements=[
                _requirement("MANDATORY", "Experience with cloud-based machine learning platforms", "")
            ]
        )
        violations = find_integrity_violations(analysis, LONG_POSTING)
        self.assertTrue(any("unsupported requirement text" in v for v in violations))

    def test_provenance_not_actually_in_the_posting_is_rejected(self):
        analysis = _analysis(
            requirements=[
                _requirement("MANDATORY", "Experience with Kubernetes", "Kubernetes expertise is required.")
            ]
        )
        violations = find_integrity_violations(analysis, LONG_POSTING)
        self.assertTrue(any("unsupported requirement text" in v for v in violations))

    def test_screening_risk_without_provenance_is_rejected(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "MANDATORY",
                    "Experience with cloud-based machine learning platforms",
                    "Experience with cloud-based machine learning platforms is required.",
                )
            ],
            screening_risks=[_risk("Some risk.", "a quotation nowhere in the posting")],
        )
        violations = find_integrity_violations(analysis, LONG_POSTING)
        self.assertTrue(any("unsupported risk text" in v for v in violations))

    def test_implied_expectation_is_exempt_from_provenance(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "MANDATORY",
                    "Experience with cloud-based machine learning platforms",
                    "Experience with cloud-based machine learning platforms is required.",
                ),
                _requirement("IMPLIED_EXPECTATION", "Senior-level autonomy expected", ""),
            ]
        )
        violations = find_integrity_violations(analysis, LONG_POSTING)
        self.assertEqual(violations, [])

    def test_ats_signal_is_exempt_from_provenance(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "MANDATORY",
                    "Experience with cloud-based machine learning platforms",
                    "Experience with cloud-based machine learning platforms is required.",
                ),
                _requirement("ATS_SIGNAL", "generative AI", ""),
            ]
        )
        violations = find_integrity_violations(analysis, LONG_POSTING)
        self.assertEqual(violations, [])


class DuplicateRequirementTests(SimpleTestCase):
    def test_exact_duplicate_requirement_is_rejected(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "MANDATORY",
                    "Experience with cloud-based machine learning platforms",
                    "Experience with cloud-based machine learning platforms is required.",
                ),
                _requirement(
                    "MANDATORY",
                    "  Experience With Cloud-Based Machine Learning Platforms  ",
                    "Experience with cloud-based machine learning platforms is required.",
                ),
            ]
        )
        violations = find_integrity_violations(analysis, LONG_POSTING)
        self.assertTrue(any("duplicates requirement" in v for v in violations))

    def test_same_text_in_different_categories_is_not_a_duplicate(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "RESPONSIBILITY",
                    "Design generative AI architectures",
                    "design and implement generative AI architectures",
                ),
                _requirement("ATS_SIGNAL", "Design generative AI architectures", ""),
            ]
        )
        violations = find_integrity_violations(analysis, LONG_POSTING)
        self.assertFalse(any("duplicates requirement" in v for v in violations))


class WordingIsNeverLexicallyRejectedTests(SimpleTestCase):
    """D-023's central correction: deterministic code must never reject content because of what
    its words are -- only the AJ LLM and the operator judge meaning. `GAP_LANGUAGE_MARKERS`-style
    phrase blocking was removed entirely; these tests prove wording that would have tripped it no
    longer has any effect on validity, as long as it has real provenance."""

    def test_quoted_no_experience_necessary_wording_is_never_rejected(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "PREFERRED",
                    "No experience necessary with legacy mainframe systems",
                    "No experience necessary with legacy mainframe systems.",
                )
            ]
        )
        violations = find_integrity_violations(analysis, LONG_POSTING)
        self.assertEqual(violations, [])

    def test_a_screening_risk_phrased_as_a_candidate_gap_is_never_rejected_for_its_wording(self):
        """Deliberately reproduces phrasing D-022's removed GAP_LANGUAGE_MARKERS check would have
        blocked -- with real provenance, it is now accepted; classifying whether this framing is
        appropriate is the operator's job at Gate 1, not this validator's."""
        analysis = _analysis(
            requirements=[
                _requirement(
                    "MANDATORY",
                    "Experience with cloud-based machine learning platforms",
                    "Experience with cloud-based machine learning platforms is required.",
                )
            ],
            screening_risks=[
                _risk(
                    "No proven ability to design and implement generative AI architectures.",
                    "design and implement generative AI architectures",
                )
            ],
        )
        violations = find_integrity_violations(analysis, LONG_POSTING)
        self.assertEqual(violations, [])

    def test_numerous_screening_risks_with_zero_requirements_is_rejected_only_for_zero_requirements(self):
        """The D-022 heuristic that inferred misclassification from "risks present, requirements
        absent" is removed. The response is still rejected here -- but only because requirements is
        empty, the one objective rule that applies, not because of the risks' content or count."""
        analysis = _analysis(
            requirements=[],
            screening_risks=[
                _risk("Lack of hands-on experience with generative AI services.", "generative AI"),
                _risk("No proven ability to design generative AI architectures.", "generative AI"),
            ],
        )
        violations = find_integrity_violations(analysis, LONG_POSTING)
        self.assertTrue(any("Zero requirements" in v for v in violations))
        self.assertFalse(any("candidate-judgment" in v for v in violations))
        self.assertFalse(any("misclassified" in v for v in violations))
