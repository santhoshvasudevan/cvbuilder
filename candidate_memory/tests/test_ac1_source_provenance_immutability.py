"""AC-1 / AUDIT-002: immutable source provenance after creation."""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase

from candidate_memory.admin import MemorySourceDocumentAdmin
from candidate_memory.integrity import HardIntegrityError
from candidate_memory.models import (
    CandidateMemory,
    MemoryClaim,
    MemoryClaimSupport,
    MemorySourceDocument,
)


class SourceProvenanceImmutabilityTests(TestCase):
    def setUp(self):
        self.memory = CandidateMemory.objects.create(label="ac1-provenance", is_active=True)
        self.document = MemorySourceDocument.objects.create(
            memory=self.memory,
            source_path="fixtures/ac1_source.md",
            source_kind=MemorySourceDocument.SourceKind.OTHER,
            precedence_rank=100,
            content_sha256="a" * 64,
            content_text="Original immutable source content.",
            byte_size=32,
        )
        self.claim = MemoryClaim.objects.create(
            memory=self.memory,
            claim_key="career_history:ac1",
            text="Example engagement claim",
            category=MemoryClaim.Category.CAREER_HISTORY,
        )
        self.support = MemoryClaimSupport.objects.create(
            claim=self.claim,
            source_document=self.document,
            excerpt="Example engagement claim",
            location_hint="bullet@0",
            char_start=0,
            char_end=24,
        )

    def test_source_document_rejects_instance_save_mutation(self):
        self.document.content_text = "mutated"
        with self.assertRaises(HardIntegrityError) as ctx:
            self.document.save()
        self.assertTrue(any(f.code == "PROVENANCE_IMMUTABLE" for f in ctx.exception.findings))
        self.document.refresh_from_db()
        self.assertEqual(self.document.content_text, "Original immutable source content.")
        self.assertEqual(self.document.content_sha256, "a" * 64)
        self.assertEqual(self.document.source_path, "fixtures/ac1_source.md")

    def test_source_document_rejects_instance_delete(self):
        with self.assertRaises(HardIntegrityError):
            self.document.delete()
        self.assertTrue(MemorySourceDocument.objects.filter(pk=self.document.pk).exists())
        self.assertTrue(MemoryClaimSupport.objects.filter(pk=self.support.pk).exists())

    def test_source_document_rejects_queryset_update_and_delete(self):
        with self.assertRaises(HardIntegrityError):
            MemorySourceDocument.objects.filter(pk=self.document.pk).update(content_text="bulk")
        with self.assertRaises(HardIntegrityError):
            MemorySourceDocument.objects.filter(pk=self.document.pk).delete()
        self.document.refresh_from_db()
        self.assertEqual(self.document.content_text, "Original immutable source content.")
        self.assertTrue(MemoryClaimSupport.objects.filter(pk=self.support.pk).exists())

    def test_claim_support_rejects_instance_and_queryset_mutation_and_delete(self):
        self.support.excerpt = "tampered excerpt"
        with self.assertRaises(HardIntegrityError):
            self.support.save()
        with self.assertRaises(HardIntegrityError):
            self.support.delete()
        with self.assertRaises(HardIntegrityError):
            MemoryClaimSupport.objects.filter(pk=self.support.pk).update(excerpt="bulk")
        with self.assertRaises(HardIntegrityError):
            MemoryClaimSupport.objects.filter(pk=self.support.pk).delete()
        self.support.refresh_from_db()
        self.assertEqual(self.support.excerpt, "Example engagement claim")
        self.assertEqual(self.support.source_document_id, self.document.pk)

    def test_protective_source_deletion_does_not_cascade_destroy_evidence(self):
        """PROTECT + immutability: source delete fails closed; claim-support evidence remains."""
        support_id = self.support.pk
        with self.assertRaises(HardIntegrityError):
            self.document.delete()
        self.assertTrue(MemoryClaimSupport.objects.filter(pk=support_id).exists())

        field = MemoryClaimSupport._meta.get_field("source_document")
        self.assertEqual(field.remote_field.on_delete.__name__, "PROTECT")

        from django.db.models.deletion import Collector

        collector = Collector(using="default")
        with self.assertRaises(ProtectedError):
            collector.collect([self.document])
        self.assertTrue(MemoryClaimSupport.objects.filter(pk=support_id).exists())
        self.assertTrue(MemorySourceDocument.objects.filter(pk=self.document.pk).exists())

    def test_source_document_admin_cannot_delete(self):
        User = get_user_model()
        user = User.objects.create_superuser("admin", "admin@example.com", "password")
        factory = RequestFactory()
        request = factory.get("/admin/candidate_memory/memorysourcedocument/")
        request.user = user
        admin = MemorySourceDocumentAdmin(MemorySourceDocument, AdminSite())
        self.assertFalse(admin.has_delete_permission(request))
        self.assertFalse(admin.has_delete_permission(request, obj=self.document))

    def test_source_document_base_manager_rejects_update_and_delete(self):
        """AUDIT-001: _base_manager must use ImmutableProvenanceQuerySet (no bypass)."""
        from candidate_memory.models import ImmutableProvenanceQuerySet

        self.assertIsInstance(
            MemorySourceDocument._base_manager.get_queryset(),
            ImmutableProvenanceQuerySet,
        )
        with self.assertRaises(HardIntegrityError) as ctx_update:
            MemorySourceDocument._base_manager.filter(pk=self.document.pk).update(
                content_text="base-manager bypass"
            )
        self.assertTrue(
            any(f.code == "PROVENANCE_IMMUTABLE" for f in ctx_update.exception.findings)
        )
        with self.assertRaises(HardIntegrityError) as ctx_delete:
            MemorySourceDocument._base_manager.filter(pk=self.document.pk).delete()
        self.assertTrue(
            any(f.code == "PROVENANCE_IMMUTABLE" for f in ctx_delete.exception.findings)
        )
        self.document.refresh_from_db()
        self.assertTrue(MemorySourceDocument.objects.filter(pk=self.document.pk).exists())
        self.assertEqual(self.document.content_text, "Original immutable source content.")
        self.assertEqual(self.document.content_sha256, "a" * 64)
        self.assertEqual(self.document.source_path, "fixtures/ac1_source.md")

    def test_claim_support_base_manager_rejects_update_and_delete(self):
        """AUDIT-001: _base_manager must use ImmutableProvenanceQuerySet (no bypass)."""
        from candidate_memory.models import ImmutableProvenanceQuerySet

        self.assertIsInstance(
            MemoryClaimSupport._base_manager.get_queryset(),
            ImmutableProvenanceQuerySet,
        )
        with self.assertRaises(HardIntegrityError) as ctx_update:
            MemoryClaimSupport._base_manager.filter(pk=self.support.pk).update(
                excerpt="base-manager bypass"
            )
        self.assertTrue(
            any(f.code == "PROVENANCE_IMMUTABLE" for f in ctx_update.exception.findings)
        )
        with self.assertRaises(HardIntegrityError) as ctx_delete:
            MemoryClaimSupport._base_manager.filter(pk=self.support.pk).delete()
        self.assertTrue(
            any(f.code == "PROVENANCE_IMMUTABLE" for f in ctx_delete.exception.findings)
        )
        self.support.refresh_from_db()
        self.assertTrue(MemoryClaimSupport.objects.filter(pk=self.support.pk).exists())
        self.assertEqual(self.support.excerpt, "Example engagement claim")
        self.assertEqual(self.support.source_document_id, self.document.pk)
