"""AC-2 / CLAUDE-M3A-002: same-engagement start/end date contradictions across sources."""

from pathlib import Path

from django.conf import settings
from django.test import TestCase

from candidate_memory.models import CandidateMemory, MemoryConflict
from candidate_memory.services.ingestion import (
    ingest_default_candidate_sources,
    ingest_source_file,
)


class EngagementDateContradictionTests(TestCase):
    def test_generic_same_engagement_start_date_contradiction_is_preserved(self):
        memory = CandidateMemory.objects.create(label="ac2-dates", is_active=True)
        fixture_dir = Path(settings.BASE_DIR) / "candidate_memory" / "tests" / "fixtures"
        for name in ("date_conflict_source_a.md", "date_conflict_source_b.md"):
            result = ingest_source_file(memory, fixture_dir / name)
            self.assertTrue(result.created, result.skipped_reason)

        conflict = MemoryConflict.objects.get(
            memory=memory,
            topic="engagement_start_date:acme_robotics_gmbh",
            status=MemoryConflict.Status.UNRESOLVED,
        )
        self.assertIn("acme_robotics_gmbh", conflict.description)
        self.assertTrue(conflict.related_claims.exists())

    def test_maruti_start_date_contradiction_retained_as_unresolved_conflict(self):
        memory = CandidateMemory.objects.create(label="ac2-maruti", is_active=True)
        ingest_default_candidate_sources(memory)

        conflict = MemoryConflict.objects.get(
            memory=memory,
            topic="engagement_start_date:maruti_suzuki",
            status=MemoryConflict.Status.UNRESOLVED,
        )
        self.assertIn("maruti_suzuki", conflict.description)
        self.assertTrue(conflict.related_claims.exists())
        # Known variants: 08/2012 (2012-08) vs Sep 2012 (2012-09).
        self.assertIn("2012-08", conflict.description)
        self.assertIn("2012-09", conflict.description)

    def test_known_non_date_conflicts_still_preserved_alongside_maruti(self):
        memory = CandidateMemory.objects.create(label="ac2-all-three", is_active=True)
        ingest_default_candidate_sources(memory)
        topics = set(
            MemoryConflict.objects.filter(
                memory=memory,
                status=MemoryConflict.Status.UNRESOLVED,
            ).values_list("topic", flat=True)
        )
        self.assertIn("german_language_level", topics)
        self.assertIn("employment_structure_and_dates", topics)
        self.assertIn("engagement_start_date:maruti_suzuki", topics)
