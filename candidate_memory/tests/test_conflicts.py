"""Unit-level coverage of `services/conflicts.py`'s pure detection/precedence logic, using
hand-built claims with the *correct*, validated `structured_value` shape (see
`services/comparable_values.py`). These are fast, isolated tests of the detector itself --
`tests/test_conflict_pipeline.py` is the authoritative proof that the real extraction/storage
path actually produces claims the detector can act on; do not treat passing tests here as evidence
that the production pipeline is wired correctly."""

from __future__ import annotations

from django.test import TestCase

from ..models import MemoryClaim, MemoryClaimSupport, MemoryConflict, MemorySourceDocument
from ..services.conflicts import detect_and_resolve_conflicts
from .factories import make_claim, make_revision, make_source


def _support_for(claim, source):
    MemoryClaimSupport.objects.create(
        memory_claim=claim,
        memory_source_document=source,
        quotation=claim.canonical_text_en,
        start_line=1,
        end_line=1,
        source_language="en",
        support_role=MemoryClaimSupport.SupportRole.PRIMARY,
    )


def _dates(start_year, start_month, end_status="KNOWN", end_year=None, end_month=None):
    return {
        "start_year": start_year, "start_month": start_month, "end_status": end_status,
        "end_year": end_year, "end_month": end_month, "precision": "YEAR_MONTH",
    }


class ContinentalDatesLocationConflictTests(TestCase):
    """Operator resolution: Continental client work July 2015-October 2017, Nuremberg, Germany --
    supersedes conflicting '...-July 2017' / 'Frankfurt am Main' statements from the corpora."""

    def test_conflicting_end_dates_auto_resolved_by_operator_update_precedence(self):
        rev = make_revision()
        corpus_source = make_source(rev, precedence=2)  # ENGLISH_CORPUS precedence
        operator_source = make_source(
            rev, logical_source_key="operator_update", precedence=0,
            source_role=MemorySourceDocument.SourceRole.OPERATOR_UPDATE,
        )

        stale_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Continental",
            structured_value=_dates(2015, 7, end_year=2017, end_month=7),
        )
        _support_for(stale_claim, corpus_source)

        resolved_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Continental",
            structured_value=_dates(2015, 7, end_year=2017, end_month=10),
        )
        _support_for(resolved_claim, operator_source)

        conflicts = detect_and_resolve_conflicts(rev)

        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict.status, MemoryConflict.Status.RESOLVED)
        self.assertEqual(conflict.resolved_claim_id, resolved_claim.pk)

        stale_claim.refresh_from_db()
        resolved_claim.refresh_from_db()
        self.assertEqual(stale_claim.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)
        self.assertNotEqual(resolved_claim.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)

    def test_conflicting_location_auto_resolved_by_operator_update_precedence(self):
        rev = make_revision()
        corpus_source = make_source(rev, precedence=2)
        operator_source = make_source(
            rev, logical_source_key="operator_update", precedence=0,
            source_role=MemorySourceDocument.SourceRole.OPERATOR_UPDATE,
        )

        stale_claim = make_claim(
            rev, claim_type="employment_location", subject_scope="Continental",
            structured_value={"city": "Frankfurt am Main"},
        )
        _support_for(stale_claim, corpus_source)

        resolved_claim = make_claim(
            rev, claim_type="employment_location", subject_scope="Continental",
            structured_value={"city": "Nuremberg", "country": "Germany"},
        )
        _support_for(resolved_claim, operator_source)

        detect_and_resolve_conflicts(rev)

        stale_claim.refresh_from_db()
        resolved_claim.refresh_from_db()
        self.assertEqual(stale_claim.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)
        self.assertNotEqual(resolved_claim.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)
        self.assertEqual(resolved_claim.structured_value["city"], "Nuremberg")


class MarutiStartDateConflictTests(TestCase):
    """Operator resolution: Maruti Suzuki began August 2012 -- supersedes a conflicting
    September 2012 statement."""

    def test_conflicting_start_date_auto_resolved_by_operator_update_precedence(self):
        rev = make_revision()
        corpus_source = make_source(rev, precedence=2)
        operator_source = make_source(
            rev, logical_source_key="operator_update", precedence=0,
            source_role=MemorySourceDocument.SourceRole.OPERATOR_UPDATE,
        )

        stale_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Maruti Suzuki",
            structured_value=_dates(2012, 9, end_status="ONGOING"),
        )
        _support_for(stale_claim, corpus_source)

        resolved_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Maruti Suzuki",
            structured_value=_dates(2012, 8, end_status="ONGOING"),
        )
        _support_for(resolved_claim, operator_source)

        detect_and_resolve_conflicts(rev)

        stale_claim.refresh_from_db()
        self.assertEqual(stale_claim.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)


class GenuineUnresolvedConflictTests(TestCase):
    """A conflict the operator-resolutions file does not address must stay OPEN and block every
    involved claim -- the detector must never silently pick a favorable statement."""

    def test_conflict_with_no_operator_update_support_stays_open_and_blocks_all(self):
        rev = make_revision()
        source_a = make_source(rev, logical_source_key="a", precedence=2)
        source_b = make_source(rev, logical_source_key="b", precedence=3)

        claim_a = make_claim(
            rev, claim_type="employment_dates", subject_scope="Some Employer",
            structured_value=_dates(2020, 1, end_year=2021, end_month=1),
        )
        _support_for(claim_a, source_a)
        claim_b = make_claim(
            rev, claim_type="employment_dates", subject_scope="Some Employer",
            structured_value=_dates(2020, 1, end_year=2022, end_month=1),
        )
        _support_for(claim_b, source_b)

        conflicts = detect_and_resolve_conflicts(rev)

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].status, MemoryConflict.Status.OPEN)
        claim_a.refresh_from_db()
        claim_b.refresh_from_db()
        self.assertEqual(claim_a.confirmation_status, MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT)
        self.assertEqual(claim_b.confirmation_status, MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT)

    def test_agreeing_claims_produce_no_conflict(self):
        rev = make_revision()
        source_a = make_source(rev, logical_source_key="a", precedence=2)
        source_b = make_source(rev, logical_source_key="b", precedence=3)
        claim_a = make_claim(
            rev, claim_type="employment_dates", subject_scope="Some Employer",
            structured_value=_dates(2020, 1, end_year=2021, end_month=1),
        )
        _support_for(claim_a, source_a)
        claim_b = make_claim(
            rev, claim_type="employment_dates", subject_scope="Some Employer",
            structured_value=_dates(2020, 1, end_year=2021, end_month=1),
        )
        _support_for(claim_b, source_b)

        conflicts = detect_and_resolve_conflicts(rev)
        self.assertEqual(conflicts, [])

    def test_claim_with_invalid_structured_value_never_falsely_agrees_with_another(self):
        """Fail-closed: a comparable-type claim with missing/malformed structured_value must get
        a key that can never spuriously match another claim's key -- it must not be silently
        treated as agreeing with (or conflicting with) anything."""
        rev = make_revision()
        source_a = make_source(rev, logical_source_key="a", precedence=2)
        source_b = make_source(rev, logical_source_key="b", precedence=3)
        valid_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Some Employer",
            structured_value=_dates(2020, 1, end_year=2021, end_month=1),
        )
        _support_for(valid_claim, source_a)
        invalid_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Some Employer",
            structured_value={},  # missing required start_year -- fails schema validation
        )
        _support_for(invalid_claim, source_b)

        conflicts = detect_and_resolve_conflicts(rev)
        self.assertEqual(conflicts, [])  # no false conflict and no false agreement
        valid_claim.refresh_from_db()
        invalid_claim.refresh_from_db()
        self.assertNotEqual(
            valid_claim.confirmation_status, MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT
        )
        self.assertNotEqual(
            invalid_claim.confirmation_status, MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT
        )


class GermanLanguageProficiencyConflictTests(TestCase):
    """Operator resolution: German B1 confirmed/current, B2 actively pursued but not attained.
    A stale corpus claim overstating B2 as attained must be superseded by the operator update."""

    def test_overstated_b2_attained_claim_superseded_by_operator_update(self):
        rev = make_revision()
        corpus_source = make_source(rev, precedence=2)
        operator_source = make_source(
            rev, logical_source_key="operator_update", precedence=0,
            source_role=MemorySourceDocument.SourceRole.OPERATOR_UPDATE,
        )

        overstated_claim = make_claim(
            rev, claim_type="language_proficiency", subject_scope="German language",
            structured_value={"language": "German", "attained_level": "B2"},
        )
        _support_for(overstated_claim, corpus_source)

        accurate_claim = make_claim(
            rev, claim_type="language_proficiency", subject_scope="German language",
            structured_value={"language": "German", "attained_level": "B1", "in_progress_level": "B2"},
        )
        _support_for(accurate_claim, operator_source)

        detect_and_resolve_conflicts(rev)

        overstated_claim.refresh_from_db()
        accurate_claim.refresh_from_db()
        self.assertEqual(overstated_claim.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)
        self.assertNotEqual(accurate_claim.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)
        self.assertEqual(accurate_claim.structured_value["in_progress_level"], "B2")


class ConflictDetectionIdempotencyTests(TestCase):
    """Candidate Memory recovery (2026-09-03): re-running detect_and_resolve_conflicts after new
    content is added (e.g. a targeted chunk retry) must never duplicate a conflict for the same
    revision/conflict_key, and must never reopen or re-litigate an existing decision."""

    def test_rerunning_detection_does_not_duplicate_an_existing_conflict(self):
        rev = make_revision()
        corpus_source = make_source(rev, precedence=2)
        operator_source = make_source(
            rev, logical_source_key="operator_update", precedence=0,
            source_role=MemorySourceDocument.SourceRole.OPERATOR_UPDATE,
        )
        stale_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Continental",
            structured_value=_dates(2015, 7, end_year=2017, end_month=7),
        )
        _support_for(stale_claim, corpus_source)
        resolved_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Continental",
            structured_value=_dates(2015, 7, end_year=2017, end_month=10),
        )
        _support_for(resolved_claim, operator_source)

        first_pass = detect_and_resolve_conflicts(rev)
        self.assertEqual(len(first_pass), 1)
        second_pass = detect_and_resolve_conflicts(rev)

        self.assertEqual(len(second_pass), 0)  # nothing new created
        self.assertEqual(rev.conflicts.filter(conflict_key="employment_dates:continental").count(), 1)

    def test_rerunning_detection_preserves_the_existing_resolution_even_with_new_claims_in_scope(self):
        rev = make_revision()
        corpus_source = make_source(rev, precedence=2)
        operator_source = make_source(
            rev, logical_source_key="operator_update", precedence=0,
            source_role=MemorySourceDocument.SourceRole.OPERATOR_UPDATE,
        )
        stale_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Continental",
            structured_value=_dates(2015, 7, end_year=2017, end_month=7),
        )
        _support_for(stale_claim, corpus_source)
        resolved_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Continental",
            structured_value=_dates(2015, 7, end_year=2017, end_month=10),
        )
        _support_for(resolved_claim, operator_source)
        detect_and_resolve_conflicts(rev)
        conflict = rev.conflicts.get(conflict_key="employment_dates:continental")
        self.assertEqual(conflict.status, MemoryConflict.Status.RESOLVED)

        # A third, newly-recovered claim joins the same structural group -- re-running detection
        # must not reopen or duplicate the already-resolved conflict; the new claim is simply left
        # exactly as it already is (fail closed), not silently retired or added to the old record.
        new_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Continental",
            structured_value=_dates(2015, 7, end_year=2017, end_month=7),
        )
        _support_for(new_claim, corpus_source)

        detect_and_resolve_conflicts(rev)

        conflict.refresh_from_db()
        resolved_claim.refresh_from_db()
        self.assertEqual(rev.conflicts.filter(conflict_key="employment_dates:continental").count(), 1)
        self.assertEqual(conflict.status, MemoryConflict.Status.RESOLVED)
        self.assertNotEqual(resolved_claim.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)

    def test_a_genuinely_new_conflict_key_is_still_detected_after_an_earlier_pass(self):
        rev = make_revision()
        corpus_source = make_source(rev, precedence=2)
        operator_source = make_source(
            rev, logical_source_key="operator_update", precedence=0,
            source_role=MemorySourceDocument.SourceRole.OPERATOR_UPDATE,
        )
        stale_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Continental",
            structured_value=_dates(2015, 7, end_year=2017, end_month=7),
        )
        _support_for(stale_claim, corpus_source)
        resolved_claim = make_claim(
            rev, claim_type="employment_dates", subject_scope="Continental",
            structured_value=_dates(2015, 7, end_year=2017, end_month=10),
        )
        _support_for(resolved_claim, operator_source)
        detect_and_resolve_conflicts(rev)  # first pass: Continental only

        # A brand-new, unrelated conflicting pair (different subject_scope) appears later.
        ford_a = make_claim(
            rev, claim_type="employment_dates", subject_scope="Ford",
            structured_value=_dates(2017, 7),
        )
        _support_for(ford_a, corpus_source)
        ford_b = make_claim(
            rev, claim_type="employment_dates", subject_scope="Ford",
            structured_value=_dates(2017, 11),
        )
        _support_for(ford_b, corpus_source)

        second_pass = detect_and_resolve_conflicts(rev)

        self.assertEqual(len(second_pass), 1)
        self.assertEqual(second_pass[0].conflict_key, "employment_dates:ford")
        self.assertEqual(rev.conflicts.count(), 2)
