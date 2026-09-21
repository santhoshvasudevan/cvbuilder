from pathlib import Path

from django.conf import settings
from django.test import TestCase

from candidate_memory.models import (
    CandidateMemory,
    MemoryClaim,
    MemoryConflict,
    MemorySourceDocument,
)
from candidate_memory.services.ingestion import ingest_default_candidate_sources, ingest_source_file


class SourceIngestionTests(TestCase):
    def test_default_sources_ingest_with_provenance_and_exclude_snapshot(self):
        memory = CandidateMemory.objects.create(label="ingest-test", is_active=True)
        results = ingest_default_candidate_sources(memory)

        skipped = [result for result in results if result.skipped_reason]
        created = [result for result in results if result.created]
        self.assertTrue(any(result.skipped_reason == "generated_snapshot_path" for result in skipped))
        self.assertGreaterEqual(len(created), 3)

        docs = list(MemorySourceDocument.objects.filter(memory=memory))
        self.assertGreaterEqual(len(docs), 3)
        paths = {doc.source_path for doc in docs}
        self.assertTrue(any(path.endswith("AC-MEMORY_PROFILE.md") for path in paths))
        self.assertFalse(any("CANDIDATE_MEMORY_SNAPSHOT.md" in path for path in paths))

        for doc in docs:
            self.assertEqual(len(doc.content_sha256), 64)
            self.assertGreater(doc.byte_size, 0)
            self.assertTrue(doc.claim_supports.exists())

        self.assertTrue(MemoryClaim.objects.filter(memory=memory).exists())
        self.assertTrue(hasattr(memory, "profile"))
        self.assertTrue(hasattr(memory, "static_resume_profile"))

    def test_ingestion_is_idempotent_by_content_hash(self):
        memory = CandidateMemory.objects.create(label="idempotent", is_active=True)
        path = Path(settings.BASE_DIR) / "docs" / "AC" / "AC-MEMORY_PROFILE.md"
        first = ingest_source_file(memory, path)
        second = ingest_source_file(memory, path)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.skipped_reason, "idempotent_hash_match")
        self.assertEqual(
            MemorySourceDocument.objects.filter(memory=memory, source_path__endswith=path.name).count(),
            1,
        )

    def test_generated_snapshot_marker_is_excluded_even_from_temp_copy(self):
        memory = CandidateMemory.objects.create(label="exclude-marker", is_active=True)
        snapshot = Path(settings.BASE_DIR) / "docs" / "CANDIDATE_MEMORY_SNAPSHOT.md"
        result = ingest_source_file(memory, snapshot)
        self.assertFalse(result.created)
        self.assertIn(result.skipped_reason, {"generated_snapshot_path", "generated_reference_marker"})
        self.assertEqual(MemorySourceDocument.objects.filter(memory=memory).count(), 0)

    def test_unresolved_conflicts_are_preserved(self):
        memory = CandidateMemory.objects.create(label="conflicts", is_active=True)
        fixture_dir = Path(settings.BASE_DIR) / "candidate_memory" / "tests" / "fixtures"
        for name in ("conflict_source_a.md", "conflict_source_b.md"):
            result = ingest_source_file(memory, fixture_dir / name)
            self.assertTrue(result.created, result.skipped_reason)

        conflicts = list(
            MemoryConflict.objects.filter(
                memory=memory,
                status=MemoryConflict.Status.UNRESOLVED,
            )
        )
        topics = {conflict.topic for conflict in conflicts}
        self.assertIn("german_language_level", topics)
        self.assertIn("employment_structure_and_dates", topics)
        for conflict in conflicts:
            self.assertTrue(conflict.related_claims.exists())
