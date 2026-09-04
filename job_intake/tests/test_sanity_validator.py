"""Unit tests for the deterministic semantic sanity validator (2026-09-04 AJ hardening, D-022),
added after the JobApplication id=9 live failure: a schema-valid `AgentJobberAnalysis` with
`requirements=[]` and fourteen `screening_risks` phrased as candidate-gap judgments for a
substantive posting that plainly had extractable responsibilities/qualifications."""

from __future__ import annotations

from django.test import SimpleTestCase
from pydantic import ValidationError

from ..schemas import AgentJobberAnalysis
from ..validators.sanity import SUBSTANTIVE_TEXT_MIN_CHARS, find_sanity_violations

# A realistic, substantive posting (>=300 chars) whose exact phrasing backs every source_context
# used below.
SUBSTANTIVE_POSTING = (
    "Senior Generative AI Solutions Architect at a large technology company. You will design "
    "and implement generative AI architectures that orchestrate complex workflows across "
    "enterprise systems. Experience with cloud-based machine learning platforms is required. "
    "Experience with agentic AI systems is a plus. You will mentor team members and contribute "
    "to knowledge sharing within the organization. Candidates must be willing to travel up to "
    "25% of the time to customer sites."
)
assert len(SUBSTANTIVE_POSTING) >= SUBSTANTIVE_TEXT_MIN_CHARS

SHORT_POSTING = "A short, non-substantive posting stub."


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


class ZeroRequirementsTests(SimpleTestCase):
    def test_zero_requirements_on_substantive_posting_is_a_violation(self):
        analysis = _analysis()
        violations = find_sanity_violations(analysis, SUBSTANTIVE_POSTING)
        self.assertTrue(any("Zero requirements" in v for v in violations))

    def test_zero_requirements_on_short_non_substantive_posting_is_not_a_violation(self):
        analysis = _analysis()
        violations = find_sanity_violations(analysis, SHORT_POSTING)
        self.assertEqual(violations, [])


class ExactObservedFailureShapeTests(SimpleTestCase):
    """Reproduces the JobApplication id=9 failure shape exactly: a valid-looking, schema-conformant
    analysis with zero requirements and numerous posting responsibilities recast as candidate-gap
    screening risks."""

    def test_zero_requirements_with_gap_style_screening_risks_is_rejected_on_every_ground(self):
        analysis = _analysis(
            requirements=[],
            screening_risks=[
                _risk(
                    "Lack of hands-on experience with cloud generative AI services.",
                    "generative AI architectures",
                ),
                _risk(
                    "No proven ability to design and implement generative AI architectures for "
                    "enterprise workloads.",
                    "design and implement generative AI architectures",
                ),
                _risk(
                    "Insufficient stakeholder-management skills.",
                    "mentor team members",
                ),
                _risk(
                    "No track record of mentoring or knowledge-sharing activities.",
                    "mentor team members and contribute to knowledge sharing",
                ),
            ],
        )
        violations = find_sanity_violations(analysis, SUBSTANTIVE_POSTING)

        self.assertTrue(any("Zero requirements" in v for v in violations))
        self.assertTrue(any("screening risk(s) were produced but zero requirements" in v for v in violations))
        # Every one of the four gap-phrased risks is individually flagged, not just the batch.
        gap_violations = [v for v in violations if "candidate-judgment language" in v]
        self.assertEqual(len(gap_violations), 4)


class GapLanguageTests(SimpleTestCase):
    def test_gap_language_in_a_requirement_is_rejected(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "RESPONSIBILITY",
                    "No proven ability to design generative AI architectures.",
                    "design generative AI architectures",
                )
            ]
        )
        violations = find_sanity_violations(analysis, SUBSTANTIVE_POSTING)
        self.assertTrue(any("candidate-judgment language" in v for v in violations))

    def test_gap_language_in_a_screening_risk_is_rejected_even_with_requirements_present(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "MANDATORY",
                    "Experience with cloud-based machine learning platforms",
                    "Experience with cloud-based machine learning platforms is required.",
                )
            ],
            screening_risks=[
                _risk("Lacks agentic AI experience.", "agentic AI systems"),
            ],
        )
        violations = find_sanity_violations(analysis, SUBSTANTIVE_POSTING)
        self.assertTrue(any("candidate-judgment language" in v for v in violations))


class ValidExtractionTests(SimpleTestCase):
    def test_valid_mandatory_preferred_responsibility_extraction_has_no_violations(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "MANDATORY",
                    "Experience with cloud-based machine learning platforms",
                    "Experience with cloud-based machine learning platforms is required.",
                ),
                _requirement(
                    "PREFERRED",
                    "Experience with agentic AI systems",
                    "Experience with agentic AI systems is a plus.",
                ),
                _requirement(
                    "RESPONSIBILITY",
                    "Design generative AI architectures for enterprise workflows",
                    "You will design and implement generative AI architectures that orchestrate "
                    "complex workflows across enterprise systems.",
                ),
                _requirement("IMPLIED_EXPECTATION", "Comfortable working with senior stakeholders"),
            ]
        )
        violations = find_sanity_violations(analysis, SUBSTANTIVE_POSTING)
        self.assertEqual(violations, [])

    def test_genuine_screening_constraint_with_provenance_has_no_violations(self):
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
                    "Requires willingness to travel up to 25% of the time.",
                    "Candidates must be willing to travel up to 25% of the time to customer sites.",
                )
            ],
        )
        violations = find_sanity_violations(analysis, SUBSTANTIVE_POSTING)
        self.assertEqual(violations, [])

    def test_implied_expectation_does_not_require_provenance(self):
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
        violations = find_sanity_violations(analysis, SUBSTANTIVE_POSTING)
        self.assertEqual(violations, [])


class DuplicateRequirementTests(SimpleTestCase):
    def test_duplicate_requirement_same_category_and_text_is_flagged(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "MANDATORY",
                    "Experience with cloud-based machine learning platforms",
                    "Experience with cloud-based machine learning platforms is required.",
                ),
                _requirement(
                    "MANDATORY",
                    "  experience with cloud-based machine learning platforms  ",
                    "Experience with cloud-based machine learning platforms is required.",
                ),
            ]
        )
        violations = find_sanity_violations(analysis, SUBSTANTIVE_POSTING)
        self.assertTrue(any("duplicates requirement" in v for v in violations))

    def test_same_text_in_different_categories_is_not_a_duplicate(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "RESPONSIBILITY",
                    "Design generative AI architectures for enterprise workflows",
                    "You will design and implement generative AI architectures that orchestrate "
                    "complex workflows across enterprise systems.",
                ),
                _requirement(
                    "ATS_SIGNAL",
                    "Design generative AI architectures for enterprise workflows",
                    "",
                ),
            ]
        )
        violations = find_sanity_violations(analysis, SUBSTANTIVE_POSTING)
        self.assertFalse(any("duplicates requirement" in v for v in violations))


class ProvenanceTests(SimpleTestCase):
    def test_mandatory_requirement_with_blank_source_context_is_unsupported(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "MANDATORY", "Experience with cloud-based machine learning platforms", ""
                )
            ]
        )
        violations = find_sanity_violations(analysis, SUBSTANTIVE_POSTING)
        self.assertTrue(any("unsupported requirement text" in v for v in violations))

    def test_requirement_with_source_context_not_actually_in_the_posting_is_unsupported(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "MANDATORY",
                    "Experience with Kubernetes",
                    "Kubernetes expertise is required.",  # not present in SUBSTANTIVE_POSTING
                )
            ]
        )
        violations = find_sanity_violations(analysis, SUBSTANTIVE_POSTING)
        self.assertTrue(any("unsupported requirement text" in v for v in violations))

    def test_screening_risk_with_a_quotation_not_actually_in_the_posting_is_unsupported(self):
        analysis = _analysis(
            requirements=[
                _requirement(
                    "MANDATORY",
                    "Experience with cloud-based machine learning platforms",
                    "Experience with cloud-based machine learning platforms is required.",
                )
            ],
            screening_risks=[_risk("Requires security clearance.", "a quotation nowhere in the posting")],
        )
        violations = find_sanity_violations(analysis, SUBSTANTIVE_POSTING)
        self.assertTrue(any("unsupported risk text" in v for v in violations))

    def test_screening_risk_schema_itself_rejects_a_blank_source_context(self):
        with self.assertRaises(ValidationError):
            _analysis(
                requirements=[
                    _requirement(
                        "MANDATORY",
                        "Experience with cloud-based machine learning platforms",
                        "Experience with cloud-based machine learning platforms is required.",
                    )
                ],
                screening_risks=[{"text": "Requires security clearance.", "source_context": ""}],
            )
