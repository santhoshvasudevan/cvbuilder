"""services/lifecycle.py: review actions scoped to working revisions only, activation validation
(blockers vs warnings), and concurrency-safe/exactly-one-active activation."""

from __future__ import annotations

from django.test import TestCase

from ..exceptions import InvalidActivationError, RevisionNotEditableError
from ..models import CandidateMemory, ChunkExtractionAttempt, MemoryClaim, MemoryClaimSupport, MemoryConflict
from ..services import lifecycle as lifecycle_service
from .factories import freeze_revision, make_claim, make_revision, make_source


def _confirmed_claim_with_valid_support(rev, **overrides):
    source = make_source(rev, raw_content="Built the Ford integration.\n")
    claim = make_claim(
        rev, canonical_text_en="Built the Ford integration.",
        confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED, resume_eligible=True, **overrides,
    )
    MemoryClaimSupport.objects.create(
        memory_claim=claim, memory_source_document=source, quotation="Built the Ford integration.",
        start_line=1, end_line=1, source_language="en", support_role=MemoryClaimSupport.SupportRole.PRIMARY,
    )
    return claim


def _confirmed_employment_claim_with_valid_support(rev, **overrides):
    """A CONFIRMED claim of an employment claim_type with a valid structured_value -- satisfies
    both the zero-employment-coverage check and the comparable-type structured-value check
    (Candidate Memory recovery, 2026-09-03) so tests unrelated to either don't need extra setup."""
    defaults = dict(
        claim_type="employment_dates",
        structured_value={
            "start_year": 2018, "start_month": None, "end_status": "ONGOING",
            "end_year": None, "end_month": None, "precision": "YEAR",
        },
    )
    defaults.update(overrides)
    return _confirmed_claim_with_valid_support(rev, **defaults)


class ClaimReviewActionTests(TestCase):
    def test_confirm_retire_restore_on_mutable_revision(self):
        rev = make_revision()
        claim = make_claim(rev)
        lifecycle_service.confirm_claim(claim)
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)
        lifecycle_service.retire_claim(claim)
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)
        lifecycle_service.restore_claim(claim)
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.UNCONFIRMED)

    def test_correct_claim_updates_fields(self):
        rev = make_revision()
        claim = make_claim(rev, canonical_text_en="Old text.")
        lifecycle_service.correct_claim(claim, canonical_text_en="New text.")
        claim.refresh_from_db()
        self.assertEqual(claim.canonical_text_en, "New text.")

    def test_actions_refused_on_active_revision(self):
        rev = make_revision()
        claim = make_claim(rev)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        with self.assertRaises(RevisionNotEditableError):
            lifecycle_service.confirm_claim(claim)

    def test_actions_refused_on_superseded_revision(self):
        rev = make_revision()
        claim = make_claim(rev)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        freeze_revision(rev, CandidateMemory.Status.SUPERSEDED)
        with self.assertRaises(RevisionNotEditableError):
            lifecycle_service.retire_claim(claim)


class ConflictResolutionActionTests(TestCase):
    def test_resolve_conflict_retires_losing_blocked_claims(self):
        rev = make_revision()
        winner = make_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT)
        loser = make_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT)
        conflict = MemoryConflict.objects.create(candidate_memory=rev, conflict_key="k", description="d")
        conflict.involved_claims.set([winner, loser])

        lifecycle_service.resolve_conflict(
            conflict, resolved_claim=winner, resolution_note="operator chose winner"
        )

        conflict.refresh_from_db()
        loser.refresh_from_db()
        self.assertEqual(conflict.status, MemoryConflict.Status.RESOLVED)
        self.assertEqual(loser.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)

    def test_dismiss_conflict_does_not_change_claim_statuses(self):
        rev = make_revision()
        claim = make_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT)
        conflict = MemoryConflict.objects.create(candidate_memory=rev, conflict_key="k", description="d")
        conflict.involved_claims.set([claim])

        lifecycle_service.dismiss_conflict(conflict, resolution_note="not a real conflict")

        conflict.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(conflict.status, MemoryConflict.Status.DISMISSED)
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT)

    def test_conflict_actions_refused_on_active_revision(self):
        rev = make_revision()
        conflict = MemoryConflict.objects.create(candidate_memory=rev, conflict_key="k", description="d")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        with self.assertRaises(RevisionNotEditableError):
            lifecycle_service.dismiss_conflict(conflict, resolution_note="x")


class ActivationValidationTests(TestCase):
    def test_confirmed_claim_with_valid_support_has_no_blockers(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        _confirmed_employment_claim_with_valid_support(rev)
        self.assertEqual(lifecycle_service.activation_blockers(rev), [])

    def test_confirmed_claim_with_no_support_is_a_blocker(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        make_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED, resume_eligible=True)
        self.assertNotEqual(lifecycle_service.activation_blockers(rev), [])

    def test_open_conflict_now_blocks_even_with_only_blocked_claims(self):
        """Candidate Memory recovery (2026-09-03, D-018): every OPEN conflict now blocks
        activation outright -- superseding the earlier D-015 allowance that let one through as a
        warning as long as its claims stayed ineligible. Resolve or dismiss it first instead."""
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        claim = make_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT)
        conflict = MemoryConflict.objects.create(
            candidate_memory=rev, conflict_key="k", description="d", status=MemoryConflict.Status.OPEN
        )
        conflict.involved_claims.set([claim])

        self.assertNotEqual(lifecycle_service.activation_blockers(rev), [])

    def test_open_conflict_with_a_confirmed_involved_claim_is_a_blocker(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        claim = _confirmed_claim_with_valid_support(rev)
        conflict = MemoryConflict.objects.create(
            candidate_memory=rev, conflict_key="k", description="d", status=MemoryConflict.Status.OPEN
        )
        conflict.involved_claims.set([claim])

        self.assertNotEqual(lifecycle_service.activation_blockers(rev), [])

    def test_resolved_conflict_is_not_a_blocker(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        _confirmed_employment_claim_with_valid_support(rev)
        claim = make_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.RETIRED)
        conflict = MemoryConflict.objects.create(
            candidate_memory=rev, conflict_key="k", description="d", status=MemoryConflict.Status.RESOLVED
        )
        conflict.involved_claims.set([claim])
        self.assertEqual(lifecycle_service.activation_blockers(rev), [])

    def test_zero_employment_coverage_is_a_blocker_by_default(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        _confirmed_claim_with_valid_support(rev)  # claim_type="skill" -- not employment
        self.assertNotEqual(lifecycle_service.activation_blockers(rev), [])

    def test_zero_employment_coverage_blocker_can_be_explicitly_acknowledged(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        _confirmed_claim_with_valid_support(rev)
        self.assertEqual(
            lifecycle_service.activation_blockers(rev, acknowledge_zero_employment_coverage=True), []
        )

    def test_unresolved_failed_chunk_attempt_is_a_blocker(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        _confirmed_employment_claim_with_valid_support(rev)
        source = make_source(rev, logical_source_key="chunk_attempt_source")
        ChunkExtractionAttempt.objects.create(
            candidate_memory=rev, source_document=source, source_content_sha256=source.content_sha256,
            start_line=1, end_line=5, status=ChunkExtractionAttempt.Status.FAILED,
            error_category="CONFIGURATION",
        )
        self.assertNotEqual(lifecycle_service.activation_blockers(rev), [])

    def test_success_and_superseded_chunk_attempts_are_not_blockers(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        _confirmed_employment_claim_with_valid_support(rev)
        source = make_source(rev, logical_source_key="chunk_attempt_source")
        ChunkExtractionAttempt.objects.create(
            candidate_memory=rev, source_document=source, source_content_sha256=source.content_sha256,
            start_line=1, end_line=10, status=ChunkExtractionAttempt.Status.SUPERSEDED,
            error_category="CONFIGURATION",
        )
        ChunkExtractionAttempt.objects.create(
            candidate_memory=rev, source_document=source, source_content_sha256=source.content_sha256,
            start_line=1, end_line=5, status=ChunkExtractionAttempt.Status.SUCCESS,
        )
        ChunkExtractionAttempt.objects.create(
            candidate_memory=rev, source_document=source, source_content_sha256=source.content_sha256,
            start_line=6, end_line=10, status=ChunkExtractionAttempt.Status.SUCCESS,
        )
        self.assertEqual(lifecycle_service.activation_blockers(rev), [])


class ActivateRevisionTests(TestCase):
    def test_activate_needs_review_revision(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        _confirmed_employment_claim_with_valid_support(rev)
        activated = lifecycle_service.activate_revision(rev)
        self.assertEqual(activated.status, CandidateMemory.Status.ACTIVE)
        self.assertIsNotNone(activated.activated_at)

    def test_activating_supersedes_previous_active(self):
        old_active = make_revision(status=CandidateMemory.Status.ACTIVE, version=1)
        new_rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW, version=2)
        _confirmed_employment_claim_with_valid_support(new_rev)

        lifecycle_service.activate_revision(new_rev)

        old_active.refresh_from_db()
        new_rev.refresh_from_db()
        self.assertEqual(old_active.status, CandidateMemory.Status.SUPERSEDED)
        self.assertEqual(new_rev.status, CandidateMemory.Status.ACTIVE)
        self.assertEqual(
            CandidateMemory.objects.filter(status=CandidateMemory.Status.ACTIVE).count(), 1
        )

    def test_activation_refused_with_blockers(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        make_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED, resume_eligible=True)
        with self.assertRaises(InvalidActivationError):
            lifecycle_service.activate_revision(rev)

    def test_cannot_activate_a_building_revision(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        with self.assertRaises(InvalidActivationError):
            lifecycle_service.activate_revision(rev)

    def test_cannot_reactivate_an_already_active_revision(self):
        rev = make_revision(status=CandidateMemory.Status.ACTIVE)
        with self.assertRaises(InvalidActivationError):
            lifecycle_service.activate_revision(rev)
