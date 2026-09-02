"""Deterministic subject_scope derivation/preservation (extraction-quality repair)."""

from __future__ import annotations

from django.test import SimpleTestCase

from ..schemas import (
    ContentPlane,
    EmploymentDatesValue,
    ExtractedItem,
    LanguageProficiencyValue,
    SourcePassage,
)
from ..services.subject_scope import is_malformed_subject_scope, normalize_subject_scope


def _support():
    return SourcePassage(quote="x", start_line=1, end_line=1, language="en")


def _item(**overrides):
    defaults = dict(
        plane=ContentPlane.EVIDENCE,
        canonical_text_en="x",
        support=_support(),
        claim_type="employment_dates",
    )
    defaults.update(overrides)
    return ExtractedItem(**defaults)


class MalformedSubjectScopeTests(SimpleTestCase):
    def test_empty_is_malformed(self):
        self.assertTrue(is_malformed_subject_scope(""))
        self.assertTrue(is_malformed_subject_scope(None))
        self.assertTrue(is_malformed_subject_scope("   "))

    def test_short_plain_identifier_is_not_malformed(self):
        self.assertFalse(is_malformed_subject_scope("Continental"))
        self.assertFalse(is_malformed_subject_scope("organization:globex corporation"))

    def test_sentence_like_prose_is_malformed(self):
        self.assertTrue(
            is_malformed_subject_scope(
                "This describes the candidate's career. It covers many different roles."
            )
        )

    def test_overlong_value_is_malformed(self):
        self.assertTrue(is_malformed_subject_scope("x" * 81))

    def test_multiline_value_is_malformed(self):
        self.assertTrue(is_malformed_subject_scope("Acme\nCorp"))


class DerivationFromEmploymentFieldsTests(SimpleTestCase):
    def test_missing_scope_derived_from_client_organization(self):
        item = _item(
            claim_type="employment_dates",
            subject_scope=None,
            client_organization="Globex Corporation",
            employment_dates=EmploymentDatesValue(start_year=2018),
        )
        self.assertEqual(normalize_subject_scope(item), "organization:globex corporation")

    def test_missing_scope_derived_from_legal_employer_when_no_client(self):
        item = _item(
            claim_type="employment_location",
            subject_scope=None,
            legal_employer="Fictional Consulting Group",
        )
        self.assertEqual(normalize_subject_scope(item), "organization:fictional consulting group")

    def test_client_organization_preferred_over_legal_employer_when_both_set(self):
        item = _item(
            claim_type="employment_dates",
            subject_scope=None,
            legal_employer="Fictional Consulting Group",
            client_organization="Globex Corporation",
            employment_dates=EmploymentDatesValue(start_year=2018),
        )
        self.assertEqual(normalize_subject_scope(item), "organization:globex corporation")

    def test_missing_scope_with_no_organization_identity_cannot_be_derived(self):
        item = _item(
            claim_type="employment_dates",
            subject_scope=None,
            employment_dates=EmploymentDatesValue(start_year=2018),
        )
        self.assertIsNone(normalize_subject_scope(item))


class DerivationFromLanguageFieldsTests(SimpleTestCase):
    def test_missing_scope_derived_from_language(self):
        item = _item(
            claim_type="language_proficiency",
            subject_scope=None,
            language_proficiency=LanguageProficiencyValue(language="French", attained_level="B1"),
        )
        self.assertEqual(normalize_subject_scope(item), "language:french")

    def test_missing_scope_with_no_language_payload_cannot_be_derived(self):
        item = _item(claim_type="language_proficiency", subject_scope=None, language_proficiency=None)
        self.assertIsNone(normalize_subject_scope(item))


class NoDerivationForUnsupportedClaimTypesTests(SimpleTestCase):
    def test_skill_claim_with_missing_scope_and_no_structured_source_cannot_be_derived(self):
        """There is no dedicated structured field carrying a skill name -- a missing subject_scope
        here is genuinely ambiguous and must fail closed, never be guessed from canonical_text_en
        or any other free-text field."""
        item = _item(claim_type="skill", subject_scope=None, canonical_text_en="Uses Python daily.")
        self.assertIsNone(normalize_subject_scope(item))

    def test_other_evidence_claim_type_derives_from_organization_fields_when_present(self):
        item = _item(
            claim_type="achievement", subject_scope=None, client_organization="Globex Corporation"
        )
        self.assertEqual(normalize_subject_scope(item), "organization:globex corporation")


class PreservationOfModelProvidedValuesTests(SimpleTestCase):
    def test_well_formed_raw_scope_is_preserved_verbatim_even_when_non_canonical(self):
        """Plain identifiers already in real corpora (predating this convention) must keep
        meaning exactly what they already mean -- never silently rewritten into the canonical
        form just because a structured field happens to be present too."""
        item = _item(
            claim_type="employment_dates",
            subject_scope="Continental",
            client_organization="Continental AG",
            employment_dates=EmploymentDatesValue(start_year=2015),
        )
        self.assertEqual(normalize_subject_scope(item), "Continental")

    def test_already_canonical_scope_is_preserved(self):
        item = _item(
            claim_type="employment_dates",
            subject_scope="organization:acme corp",
            employment_dates=EmploymentDatesValue(start_year=2015),
        )
        self.assertEqual(normalize_subject_scope(item), "organization:acme corp")

    def test_malformed_raw_scope_is_replaced_by_derivation_when_possible(self):
        item = _item(
            claim_type="employment_dates",
            subject_scope="This is a long sentence. It should not be a scope.",
            client_organization="Globex Corporation",
            employment_dates=EmploymentDatesValue(start_year=2018),
        )
        self.assertEqual(normalize_subject_scope(item), "organization:globex corporation")

    def test_malformed_raw_scope_with_no_derivable_data_returns_none(self):
        item = _item(
            claim_type="employment_dates",
            subject_scope="This is a long sentence. It should not be a scope.",
            employment_dates=EmploymentDatesValue(start_year=2018),
        )
        self.assertIsNone(normalize_subject_scope(item))
