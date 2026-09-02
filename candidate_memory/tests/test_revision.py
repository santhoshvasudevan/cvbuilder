"""Revision creation and carry-forward (D-002): unchanged sources reused without re-extraction,
all-or-nothing per-claim carry-forward, prior revisions never mutated."""

from __future__ import annotations

from django.test import TestCase

from ..models import CandidateMemory, MemoryClaim, MemoryClaimSupport, MemorySourceDocument
from ..services.revision import (
    carry_forward_unchanged,
    current_active_revision,
    next_version,
    start_new_revision,
)
from .factories import freeze_revision, make_claim, make_revision, make_source


class NextVersionTests(TestCase):
    def test_first_version_is_one(self):
        self.assertEqual(next_version(), 1)

    def test_increments_from_latest(self):
        make_revision(version=1)
        make_revision(version=5)
        self.assertEqual(next_version(), 6)


class CurrentActiveRevisionTests(TestCase):
    def test_none_when_nothing_active(self):
        make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        self.assertIsNone(current_active_revision())

    def test_returns_the_active_one(self):
        active = make_revision(status=CandidateMemory.Status.ACTIVE)
        self.assertEqual(current_active_revision(), active)


class StartNewRevisionTests(TestCase):
    def test_new_revision_references_base(self):
        active = make_revision(status=CandidateMemory.Status.ACTIVE)
        new = start_new_revision()
        self.assertEqual(new.base_revision_id, active.pk)
        self.assertEqual(new.status, CandidateMemory.Status.BUILDING)

    def test_new_revision_with_no_prior_active(self):
        new = start_new_revision()
        self.assertIsNone(new.base_revision)


class CarryForwardUnchangedTests(TestCase):
    def test_claim_with_all_supports_from_unchanged_sources_is_carried_forward(self):
        old = make_revision(version=1)
        old_source = make_source(old, raw_content="Built the Ford integration.\n")
        claim = make_claim(
            old, canonical_text_en="Built the Ford integration.",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )
        MemoryClaimSupport.objects.create(
            memory_claim=claim, memory_source_document=old_source,
            quotation="Built the Ford integration.", start_line=1, end_line=1,
            source_language="en", support_role=MemoryClaimSupport.SupportRole.PRIMARY,
        )
        freeze_revision(old, CandidateMemory.Status.ACTIVE)

        new = start_new_revision(base_revision=old)
        carry_forward_unchanged(new, old, {old_source.logical_source_key: old_source})

        carried = MemoryClaim.objects.get(candidate_memory=new)
        self.assertEqual(carried.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)
        self.assertEqual(carried.supports.count(), 1)
        new_source = MemorySourceDocument.objects.get(candidate_memory=new)
        self.assertEqual(new_source.unchanged_from_id, old_source.pk)

    def test_claim_with_one_support_from_changed_source_is_not_carried_forward(self):
        old = make_revision(version=1)
        unchanged_source = make_source(old, logical_source_key="unchanged", raw_content="line one\n")
        changed_source = make_source(
            old, logical_source_key="changed", precedence=3, raw_content="line two\n"
        )
        claim = make_claim(
            old, canonical_text_en="line one line two",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )
        MemoryClaimSupport.objects.create(
            memory_claim=claim, memory_source_document=unchanged_source, quotation="line one",
            start_line=1, end_line=1, source_language="en",
            support_role=MemoryClaimSupport.SupportRole.PRIMARY,
        )
        MemoryClaimSupport.objects.create(
            memory_claim=claim, memory_source_document=changed_source, quotation="line two",
            start_line=1, end_line=1, source_language="en",
            support_role=MemoryClaimSupport.SupportRole.CORROBORATING,
        )
        freeze_revision(old, CandidateMemory.Status.ACTIVE)

        new = start_new_revision(base_revision=old)
        # Only the unchanged source is passed -- the changed one is not in this dict, matching
        # what build_revision_from_sources computes for a genuinely re-processed source.
        carry_forward_unchanged(new, old, {unchanged_source.logical_source_key: unchanged_source})

        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=new).count(), 0)

    def test_prior_revision_content_is_untouched(self):
        old = make_revision(version=1)
        old_source = make_source(old, raw_content="Built the Ford integration.\n")
        claim = make_claim(old, canonical_text_en="Built the Ford integration.")
        MemoryClaimSupport.objects.create(
            memory_claim=claim, memory_source_document=old_source,
            quotation="Built the Ford integration.", start_line=1, end_line=1,
            source_language="en", support_role=MemoryClaimSupport.SupportRole.PRIMARY,
        )
        freeze_revision(old, CandidateMemory.Status.ACTIVE)
        new = start_new_revision(base_revision=old)
        carry_forward_unchanged(new, old, {old_source.logical_source_key: old_source})

        old.refresh_from_db()
        self.assertEqual(old.status, CandidateMemory.Status.ACTIVE)
        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=old).count(), 1)
        self.assertEqual(MemorySourceDocument.objects.filter(candidate_memory=old).count(), 1)
