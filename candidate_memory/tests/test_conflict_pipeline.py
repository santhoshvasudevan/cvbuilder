"""End-to-end proof that the extraction-to-conflict pipeline actually works through the real
production path (audit repair) -- every claim in this file is created by `build_revision`/
`build_revision_from_operator_text` -> `store_extracted_item`, driven only by scripted
`FakeAdapter` structured responses. No test in this file constructs a `MemoryClaim` row by hand.

This is the authoritative proof the earlier `test_conflicts.py` unit tests could not provide on
their own: that real extraction output actually reaches `MemoryClaim.structured_value` in a shape
`services/conflicts.py` can act on, for the exact named operator-resolution scenarios."""

from __future__ import annotations

from pathlib import Path

from django.test import TestCase

from ..models import CandidateMemory, MemoryClaim, MemorySourceDocument
from ..services import lifecycle as lifecycle_service
from ..services.bootstrap import SourceSpec, build_revision, build_revision_from_operator_text
from .factories import scripted_extraction


def _item(
    text,
    quote=None,
    claim_type="employment_dates",
    subject_scope="Continental",
    start_line=1,
    end_line=1,
    **structured,
):
    quote = quote or text
    item = {
        "plane": "EVIDENCE",
        "canonical_text_en": text,
        "support": {"quote": quote, "start_line": start_line, "end_line": end_line, "language": "en"},
        "claim_type": claim_type,
        "subject_scope": subject_scope,
        "resume_eligible": True,
    }
    item.update(structured)
    return item


def _response(*items):
    return {"items": list(items)}


class ConflictPipelineTestCase(TestCase):
    """Shared plumbing: a corpus source bootstrapped first and activated, then a second
    OPERATOR_UPDATE revision layered on top via the real "add/update profile" workflow -- exactly
    how a real operator resolution would be applied."""

    def setUp(self):
        self.tmp_dir = Path(self._testMethodName + "_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: [p.unlink() for p in self.tmp_dir.glob("*")] and self.tmp_dir.rmdir())

    def _bootstrap_corpus(self, content: str, response: dict) -> CandidateMemory:
        path = self.tmp_dir / "corpus.md"
        path.write_text(content, encoding="utf-8")
        spec = SourceSpec(
            path=path, logical_source_key="corpus",
            source_role=MemorySourceDocument.SourceRole.ENGLISH_CORPUS, language="en", precedence=2,
        )
        with scripted_extraction(response):
            rev = build_revision([spec])
        return rev

    def _activate(self, rev: CandidateMemory, confirm_all: bool = True) -> CandidateMemory:
        if confirm_all:
            for claim in rev.claims.filter(confirmation_status=MemoryClaim.ConfirmationStatus.UNCONFIRMED):
                lifecycle_service.confirm_claim(claim)
        lifecycle_service.activate_revision(rev)
        rev.refresh_from_db()
        return rev

    def _apply_operator_update(self, text: str, response: dict) -> CandidateMemory:
        with scripted_extraction(response):
            return build_revision_from_operator_text(text=text, context="Operator resolution")


class ContinentalDatesConflictPipelineTests(ConflictPipelineTestCase):
    def test_conflicting_end_dates_resolved_by_operator_update_through_real_pipeline(self):
        rev1 = self._bootstrap_corpus(
            "Continental: July 2015 to July 2017.\n",
            _response(_item(
                "Continental: July 2015 to July 2017.",
                employment_dates={
                    "start_year": 2015, "start_month": 7, "end_status": "KNOWN",
                    "end_year": 2017, "end_month": 7, "precision": "YEAR_MONTH",
                },
            )),
        )
        self._activate(rev1)

        rev2 = self._apply_operator_update(
            "Continental client assignment ran July 2015 to October 2017.",
            _response(_item(
                "Continental client assignment ran July 2015 to October 2017.",
                quote="Context: Operator resolution\nContinental client assignment ran July 2015 "
                "to October 2017.",
                end_line=2,
                employment_dates={
                    "start_year": 2015, "start_month": 7, "end_status": "KNOWN",
                    "end_year": 2017, "end_month": 10, "precision": "YEAR_MONTH",
                },
            )),
        )

        claims = list(rev2.claims.filter(claim_type="employment_dates", subject_scope="Continental"))
        self.assertEqual(len(claims), 2)
        stale = next(c for c in claims if c.structured_value.get("end_month") == 7)
        resolved = next(c for c in claims if c.structured_value.get("end_month") == 10)
        self.assertEqual(stale.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)
        self.assertEqual(resolved.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)
        self.assertEqual(resolved.structured_value["end_year"], 2017)


class ContinentalLocationConflictPipelineTests(ConflictPipelineTestCase):
    def test_frankfurt_vs_nuremberg_resolved_by_operator_update_through_real_pipeline(self):
        rev1 = self._bootstrap_corpus(
            "Continental: based in Frankfurt am Main.\n",
            _response(_item(
                "Continental: based in Frankfurt am Main.",
                claim_type="employment_location",
                employment_location={"city": "Frankfurt am Main", "country": "Germany"},
            )),
        )
        self._activate(rev1)

        rev2 = self._apply_operator_update(
            "Continental client assignment location was Nuremberg, Germany.",
            _response(_item(
                "Continental client assignment location was Nuremberg, Germany.",
                quote="Context: Operator resolution\nContinental client assignment location was "
                "Nuremberg, Germany.",
                end_line=2,
                claim_type="employment_location",
                employment_location={"city": "Nuremberg", "country": "Germany"},
            )),
        )

        claims = list(
            rev2.claims.filter(claim_type="employment_location", subject_scope="Continental")
        )
        self.assertEqual(len(claims), 2)
        frankfurt = next(c for c in claims if c.structured_value.get("city") == "Frankfurt am Main")
        nuremberg = next(c for c in claims if c.structured_value.get("city") == "Nuremberg")
        self.assertEqual(frankfurt.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)
        self.assertEqual(nuremberg.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)


class MarutiStartDateConflictPipelineTests(ConflictPipelineTestCase):
    def test_september_vs_august_resolved_by_operator_update_through_real_pipeline(self):
        rev1 = self._bootstrap_corpus(
            "Maruti Suzuki: joined September 2012.\n",
            _response(_item(
                "Maruti Suzuki: joined September 2012.", claim_type="employment_dates",
                subject_scope="Maruti Suzuki",
                employment_dates={
                    "start_year": 2012, "start_month": 9, "end_status": "ONGOING",
                    "precision": "YEAR_MONTH",
                },
            )),
        )
        self._activate(rev1)

        rev2 = self._apply_operator_update(
            "Maruti Suzuki employment began August 2012.",
            _response(_item(
                "Maruti Suzuki employment began August 2012.",
                quote="Context: Operator resolution\nMaruti Suzuki employment began August 2012.",
                end_line=2,
                claim_type="employment_dates", subject_scope="Maruti Suzuki",
                employment_dates={
                    "start_year": 2012, "start_month": 8, "end_status": "ONGOING",
                    "precision": "YEAR_MONTH",
                },
            )),
        )

        claims = list(
            rev2.claims.filter(claim_type="employment_dates", subject_scope="Maruti Suzuki")
        )
        self.assertEqual(len(claims), 2)
        september = next(c for c in claims if c.structured_value.get("start_month") == 9)
        august = next(c for c in claims if c.structured_value.get("start_month") == 8)
        self.assertEqual(september.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)
        self.assertEqual(august.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)


class GermanProficiencyConflictPipelineTests(ConflictPipelineTestCase):
    def test_b1_vs_overstated_b2_resolved_preserving_in_progress_b2_through_real_pipeline(self):
        rev1 = self._bootstrap_corpus(
            "German: B2 attained.\n",
            _response(_item(
                "German: B2 attained.", claim_type="language_proficiency", subject_scope="German language",
                language_proficiency={"language": "German", "attained_level": "B2"},
            )),
        )
        self._activate(rev1)

        rev2 = self._apply_operator_update(
            "German proficiency is B1, confirmed and current; B2 is actively pursued but not attained.",
            _response(_item(
                "German proficiency is B1 confirmed; B2 in progress, not attained.",
                quote="Context: Operator resolution\nGerman proficiency is B1, confirmed and "
                "current; B2 is actively pursued but not attained.",
                end_line=2,
                claim_type="language_proficiency", subject_scope="German language",
                language_proficiency={
                    "language": "German", "attained_level": "B1", "in_progress_level": "B2",
                },
            )),
        )

        claims = list(
            rev2.claims.filter(claim_type="language_proficiency", subject_scope="German language")
        )
        self.assertEqual(len(claims), 2)
        overstated = next(c for c in claims if c.structured_value.get("attained_level") == "B2")
        accurate = next(c for c in claims if c.structured_value.get("attained_level") == "B1")
        self.assertEqual(overstated.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)
        self.assertEqual(accurate.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)
        self.assertEqual(accurate.structured_value["in_progress_level"], "B2")
        # The accurate claim's attained level must never itself read "B2" -- in-progress and
        # attained stay structurally distinct fields, never merged.
        self.assertEqual(accurate.structured_value["attained_level"], "B1")


class SubjectScopeNormalizationConflictReachabilityTests(ConflictPipelineTestCase):
    """Extraction-quality repair: two employment_dates claims with NO model-provided
    subject_scope, but the same client_organization, must still be normalized to the same
    derived scope and reach conflict detection through the real end-to-end pipeline -- proving
    normalization runs early enough (before storage/grouping) for `services/conflicts.py`'s
    (subject_scope, claim_type) grouping to actually see them together."""

    def test_two_claims_missing_subject_scope_but_sharing_client_organization_conflict(self):
        rev1 = self._bootstrap_corpus(
            "Globex Corporation engagement started September 2018.\n",
            _response(_item(
                "Globex Corporation engagement started September 2018.",
                subject_scope=None,
                legal_employer="Fictional Consulting Group",
                client_organization="Globex Corporation",
                employment_dates={
                    "start_year": 2018, "start_month": 9, "end_status": "ONGOING",
                    "precision": "YEAR_MONTH",
                },
            )),
        )
        self._activate(rev1)

        rev2 = self._apply_operator_update(
            "Globex Corporation engagement actually started August 2018.",
            _response(_item(
                "Globex Corporation engagement actually started August 2018.",
                quote="Context: Operator resolution\nGlobex Corporation engagement actually "
                "started August 2018.",
                end_line=2,
                subject_scope=None,
                legal_employer="Fictional Consulting Group",
                client_organization="Globex Corporation",
                employment_dates={
                    "start_year": 2018, "start_month": 8, "end_status": "ONGOING",
                    "precision": "YEAR_MONTH",
                },
            )),
        )

        claims = list(
            rev2.claims.filter(
                claim_type="employment_dates", subject_scope="organization:globex corporation"
            )
        )
        self.assertEqual(len(claims), 2)
        september = next(c for c in claims if c.structured_value.get("start_month") == 9)
        august = next(c for c in claims if c.structured_value.get("start_month") == 8)
        self.assertEqual(september.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)
        self.assertEqual(august.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)
        self.assertEqual(rev2.conflicts.count(), 1)


class FordNoInventedEndDatePipelineTests(ConflictPipelineTestCase):
    def test_ford_start_date_stored_without_inventing_an_end_date(self):
        rev = self._bootstrap_corpus(
            "Ford Motor Company: client engagement began November 2017.\n",
            _response(_item(
                "Ford Motor Company: client engagement began November 2017.",
                claim_type="employment_dates", subject_scope="Ford Motor Company",
                employment_dates={
                    "start_year": 2017, "start_month": 11, "end_status": "UNKNOWN",
                    "precision": "YEAR_MONTH",
                },
            )),
        )
        claim = rev.claims.get(claim_type="employment_dates", subject_scope="Ford Motor Company")
        self.assertEqual(claim.structured_value["start_year"], 2017)
        self.assertEqual(claim.structured_value["start_month"], 11)
        self.assertEqual(claim.structured_value["end_status"], "UNKNOWN")
        self.assertIsNone(claim.structured_value["end_year"])
        # Only one Ford employment_dates claim exists -- no conflict, no invented end date, and
        # (with a valid, unique structural key) it auto-confirms normally.
        self.assertEqual(
            rev.claims.filter(claim_type="employment_dates", subject_scope="Ford Motor Company").count(), 1
        )
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)


class MissingStructuredDataFailsClosedPipelineTests(ConflictPipelineTestCase):
    def test_comparable_claim_missing_structured_data_never_auto_confirms(self):
        rev = self._bootstrap_corpus(
            "Some Employer: dates unclear from this excerpt.\n",
            _response(_item(
                "Some Employer: dates unclear from this excerpt.",
                claim_type="employment_dates", subject_scope="Some Employer",
                # employment_dates deliberately omitted -- the LLM could not state it clearly.
            )),
        )
        claim = rev.claims.get(claim_type="employment_dates", subject_scope="Some Employer")
        self.assertEqual(claim.structured_value, {})
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.UNCONFIRMED)
        self.assertEqual(rev.build_summary["structured_value_issues"], 1)

    def test_activation_blocked_if_operator_force_confirms_a_structurally_invalid_claim(self):
        """Fail-closed extends past auto-confirmation: even if an operator manually confirms a
        comparable-type claim whose structured_value never validated, activation must still
        refuse -- confirmation status alone must never be sufficient."""
        rev = self._bootstrap_corpus(
            "Some Employer: dates unclear from this excerpt.\n",
            _response(_item(
                "Some Employer: dates unclear from this excerpt.",
                claim_type="employment_dates", subject_scope="Some Employer",
            )),
        )
        claim = rev.claims.get(claim_type="employment_dates", subject_scope="Some Employer")
        lifecycle_service.confirm_claim(claim)
        rev.refresh_from_db()

        blockers = lifecycle_service.activation_blockers(rev)
        self.assertTrue(any("structured_value is missing or invalid" in b for b in blockers))
        with self.assertRaises(lifecycle_service.InvalidActivationError):
            lifecycle_service.activate_revision(rev)


class PrecedenceScopedToMatchingFactPipelineTests(ConflictPipelineTestCase):
    def test_operator_resolution_for_one_employer_does_not_affect_a_different_employers_claim(self):
        rev1 = self._bootstrap_corpus(
            "Continental: July 2015 to July 2017.\n"
            "Maruti Suzuki: joined September 2012.\n",
            _response(
                _item(
                    "Continental: July 2015 to July 2017.", claim_type="employment_dates",
                    subject_scope="Continental",
                    employment_dates={
                        "start_year": 2015, "start_month": 7, "end_status": "KNOWN",
                        "end_year": 2017, "end_month": 7, "precision": "YEAR_MONTH",
                    },
                ),
                _item(
                    "Maruti Suzuki: joined September 2012.",
                    claim_type="employment_dates", subject_scope="Maruti Suzuki",
                    start_line=2, end_line=2,
                    employment_dates={
                        "start_year": 2012, "start_month": 9, "end_status": "ONGOING",
                        "precision": "YEAR_MONTH",
                    },
                ),
            ),
        )
        self._activate(rev1)
        maruti_claim_v1 = rev1.claims.get(claim_type="employment_dates", subject_scope="Maruti Suzuki")
        self.assertEqual(maruti_claim_v1.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)

        rev2 = self._apply_operator_update(
            "Continental client assignment ran July 2015 to October 2017.",
            _response(_item(
                "Continental client assignment ran July 2015 to October 2017.",
                quote="Context: Operator resolution\nContinental client assignment ran July 2015 "
                "to October 2017.",
                end_line=2,
                claim_type="employment_dates", subject_scope="Continental",
                employment_dates={
                    "start_year": 2015, "start_month": 7, "end_status": "KNOWN",
                    "end_year": 2017, "end_month": 10, "precision": "YEAR_MONTH",
                },
            )),
        )

        # The Continental conflict resolved as expected...
        continental_claims = rev2.claims.filter(
            claim_type="employment_dates", subject_scope="Continental"
        )
        self.assertEqual(continental_claims.filter(
            confirmation_status=MemoryClaim.ConfirmationStatus.RETIRED
        ).count(), 1)

        # ...but the unrelated Maruti claim (carried forward, never touched by this operator
        # update) must remain exactly as it was -- untouched, still confirmed, no conflict.
        maruti_claim_v2 = rev2.claims.get(claim_type="employment_dates", subject_scope="Maruti Suzuki")
        self.assertEqual(maruti_claim_v2.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)
        self.assertEqual(rev2.conflicts.filter(conflict_key__icontains="maruti").count(), 0)
