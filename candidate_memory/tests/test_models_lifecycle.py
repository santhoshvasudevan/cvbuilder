"""Lifecycle enforcement at the model layer: BUILDING -> NEEDS_REVIEW -> ACTIVE -> SUPERSEDED,
exactly-one-active, and read-only-after-activation -- for CandidateMemory itself and for every
revision-scoped content model, so admin/shell/direct-ORM cannot bypass it either."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.test import TestCase

from ..exceptions import RevisionNotEditableError, SourceDocumentImmutableError
from ..models import CandidateMemory, MemoryClaim, MemoryClaimSupport, MemoryConflict
from .factories import make_claim, make_revision, make_source


class CandidateMemoryLifecycleTests(TestCase):
    def test_new_revision_starts_building(self):
        rev = make_revision()
        self.assertEqual(rev.status, CandidateMemory.Status.BUILDING)
        self.assertTrue(rev.is_mutable)

    def test_building_to_needs_review_allowed(self):
        rev = make_revision()
        rev.status = CandidateMemory.Status.NEEDS_REVIEW
        rev.save()
        self.assertEqual(rev.status, CandidateMemory.Status.NEEDS_REVIEW)

    def test_active_can_only_transition_to_superseded(self):
        rev = make_revision(status=CandidateMemory.Status.ACTIVE)
        with self.assertRaises(RevisionNotEditableError):
            rev.status = CandidateMemory.Status.BUILDING
            rev.save()

    def test_active_to_superseded_allowed(self):
        rev = make_revision(status=CandidateMemory.Status.ACTIVE)
        rev.status = CandidateMemory.Status.SUPERSEDED
        rev.save()
        self.assertEqual(rev.status, CandidateMemory.Status.SUPERSEDED)

    def test_superseded_is_frozen(self):
        rev = make_revision(status=CandidateMemory.Status.SUPERSEDED)
        with self.assertRaises(RevisionNotEditableError):
            rev.build_summary = {"x": 1}
            rev.save()

    def test_only_one_active_revision_enforced_by_db_constraint(self):
        make_revision(status=CandidateMemory.Status.ACTIVE, version=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_revision(status=CandidateMemory.Status.ACTIVE, version=2)


class RevisionScopedContentTests(TestCase):
    def test_claim_mutable_while_building(self):
        rev = make_revision()
        claim = make_claim(rev)
        claim.canonical_text_en = "Updated."
        claim.save()
        claim.refresh_from_db()
        self.assertEqual(claim.canonical_text_en, "Updated.")

    def test_claim_frozen_once_active(self):
        rev = make_revision()
        claim = make_claim(rev)
        rev.status = CandidateMemory.Status.ACTIVE
        rev.save()
        claim.canonical_text_en = "Should not save."
        with self.assertRaises(RevisionNotEditableError):
            claim.save()

    def test_claim_frozen_once_active_via_admin_style_direct_save(self):
        """Simulates a Django admin change form calling instance.save() directly -- the guard
        lives on the model, not a service function, so this path is blocked too."""
        rev = make_revision(status=CandidateMemory.Status.ACTIVE)
        claim = MemoryClaim(
            candidate_memory=rev, stable_key="k", canonical_text_en="x", claim_type="t", subject_scope="s"
        )
        with self.assertRaises(RevisionNotEditableError):
            claim.save()

    def test_claim_cannot_be_deleted_once_active(self):
        rev = make_revision()
        claim = make_claim(rev)
        rev.status = CandidateMemory.Status.ACTIVE
        rev.save()
        with self.assertRaises(RevisionNotEditableError):
            claim.delete()

    def test_source_document_immutable_on_update(self):
        rev = make_revision()
        source = make_source(rev)
        source.filename = "renamed.md"
        with self.assertRaises(SourceDocumentImmutableError):
            source.save()

    def test_source_document_never_deletable(self):
        rev = make_revision()
        source = make_source(rev)
        with self.assertRaises(SourceDocumentImmutableError):
            source.delete()

    def test_source_document_content_hash_computed_on_create(self):
        rev = make_revision()
        source = make_source(rev, raw_content="hello world")
        import hashlib

        self.assertEqual(source.content_sha256, hashlib.sha256(b"hello world").hexdigest())
        self.assertTrue(source.verify_content_hash())

    def test_support_verifies_against_exact_source_lines(self):
        rev = make_revision()
        source = make_source(rev, raw_content="line one\nline two\nline three\n")
        claim = make_claim(rev)
        support = MemoryClaimSupport.objects.create(
            memory_claim=claim,
            memory_source_document=source,
            quotation="line two",
            start_line=2,
            end_line=2,
            source_language="en",
            support_role=MemoryClaimSupport.SupportRole.PRIMARY,
        )
        self.assertTrue(support.verify_against_source())

    def test_support_fails_verification_if_quote_does_not_match_lines(self):
        rev = make_revision()
        source = make_source(rev, raw_content="line one\nline two\nline three\n")
        claim = make_claim(rev)
        support = MemoryClaimSupport.objects.create(
            memory_claim=claim,
            memory_source_document=source,
            quotation="line three",
            start_line=1,
            end_line=1,
            source_language="en",
            support_role=MemoryClaimSupport.SupportRole.PRIMARY,
        )
        self.assertFalse(support.verify_against_source())

    def test_claim_referenced_by_a_conflict_cannot_be_deleted(self):
        """Audit repair: deleting a claim a MemoryConflict references would silently desync that
        conflict's involved_claims set from its frozen description. Retirement is the supported
        removal path instead."""
        rev = make_revision()
        claim = make_claim(rev)
        conflict = MemoryConflict.objects.create(candidate_memory=rev, conflict_key="k", description="d")
        conflict.involved_claims.add(claim)

        with self.assertRaises(RevisionNotEditableError):
            claim.delete()

    def test_claim_not_referenced_by_any_conflict_can_still_be_deleted(self):
        rev = make_revision()
        claim = make_claim(rev)
        claim.delete()
        self.assertFalse(MemoryClaim.objects.filter(pk=claim.pk).exists())
