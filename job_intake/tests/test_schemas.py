from __future__ import annotations

from django.test import SimpleTestCase
from pydantic import ValidationError

from ..schemas import AgentJobberAnalysis, ExtractedRequirement, RequirementCategory
from .factories import valid_analysis_response


class AgentJobberAnalysisValidationTests(SimpleTestCase):
    def test_full_valid_response_validates(self):
        analysis = AgentJobberAnalysis.model_validate(valid_analysis_response())
        self.assertEqual(analysis.posting_language, "en")
        self.assertEqual(len(analysis.requirements), 5)
        self.assertEqual(analysis.requirements[-1].category, RequirementCategory.IMPLIED_EXPECTATION)

    def test_missing_posting_language_rejected(self):
        response = valid_analysis_response()
        del response["posting_language"]
        with self.assertRaises(ValidationError):
            AgentJobberAnalysis.model_validate(response)

    def test_blank_posting_language_rejected(self):
        with self.assertRaises(ValidationError):
            AgentJobberAnalysis.model_validate(valid_analysis_response(posting_language="   "))

    def test_minimal_response_with_no_requirements_still_validates(self):
        analysis = AgentJobberAnalysis.model_validate({"posting_language": "en"})
        self.assertEqual(analysis.requirements, [])
        self.assertEqual(analysis.employer, "")

    def test_blank_requirement_text_rejected(self):
        with self.assertRaises(ValidationError):
            ExtractedRequirement.model_validate({"category": "MANDATORY", "text": "   "})

    def test_invalid_category_rejected(self):
        with self.assertRaises(ValidationError):
            ExtractedRequirement.model_validate({"category": "NOT_A_REAL_CATEGORY", "text": "x"})
