"""Extraction-quality repair: comparable-value schemas must fail closed on a blank (not merely
absent) required identifying field -- an empty string technically satisfies `str` typing but is
semantically the same as "missing"."""

from __future__ import annotations

from django.test import SimpleTestCase
from pydantic import ValidationError

from ..schemas import EmploymentLocationValue, LanguageProficiencyValue


class BlankRequiredFieldRejectionTests(SimpleTestCase):
    def test_employment_location_blank_city_rejected(self):
        with self.assertRaises(ValidationError):
            EmploymentLocationValue(city="   ")

    def test_employment_location_non_blank_city_accepted(self):
        EmploymentLocationValue(city="Springfield")  # must not raise

    def test_language_proficiency_blank_language_rejected(self):
        with self.assertRaises(ValidationError):
            LanguageProficiencyValue(language="")

    def test_language_proficiency_non_blank_language_accepted(self):
        LanguageProficiencyValue(language="French", attained_level="B1")  # must not raise
