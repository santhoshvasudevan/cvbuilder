"""End-to-end orchestration (services/bootstrap.py) using the M2 FakeAdapter only: unchanged
sources are reused without re-extraction, changed/new sources trigger a new working revision,
prior revisions are left untouched, the snapshot file is refused as an import source, and the
"add experience / update profile" text workflow carries every other source forward automatically."""

from __future__ import annotations

from pathlib import Path

from django.test import TestCase

from llm_provider.models import LLMCallLog

from ..models import CandidateMemory, MemoryClaim, MemorySourceDocument
from ..services import lifecycle as lifecycle_service
from ..services.bootstrap import (
    SnapshotImportRefusedError,
    SourceSpec,
    build_revision,
    build_revision_from_operator_text,
)
from .factories import scripted_extraction

_ONE_ITEM_RESPONSE = {
    "items": [
        {
            "plane": "EVIDENCE",
            "canonical_text_en": "Built the Ford integration.",
            "support": {
                "quote": "Built the Ford integration.", "start_line": 1, "end_line": 1, "language": "en",
            },
            "claim_type": "employment",
            "subject_scope": "Ford Motor Company",
            "resume_eligible": True,
        }
    ]
}


def _corpus_spec(path: Path) -> SourceSpec:
    return SourceSpec(
        path=path, logical_source_key="corpus",
        source_role=MemorySourceDocument.SourceRole.ENGLISH_CORPUS, language="en", precedence=2,
    )


class BootstrapFileBasedTests(TestCase):
    def setUp(self):
        self.tmp_dir = Path(self._testMethodName + "_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: [p.unlink() for p in self.tmp_dir.glob("*")] and self.tmp_dir.rmdir())

    def _write(self, name: str, content: str) -> Path:
        path = self.tmp_dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_first_bootstrap_creates_needs_review_revision_with_extracted_claim(self):
        spec = _corpus_spec(self._write("corpus.md", "Built the Ford integration.\n"))
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev = build_revision([spec])

        self.assertEqual(rev.status, CandidateMemory.Status.NEEDS_REVIEW)
        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=rev).count(), 1)
        self.assertEqual(LLMCallLog.objects.count(), 1)

    def test_snapshot_file_refused_as_import_source(self):
        path = self._write("CANDIDATE_MEMORY_SNAPSHOT.md", "irrelevant")
        spec = SourceSpec(
            path=path, logical_source_key="snapshot",
            source_role=MemorySourceDocument.SourceRole.PRIMARY_PROFILE, language="en", precedence=1,
        )
        with self.assertRaises(SnapshotImportRefusedError):
            build_revision([spec])

    def test_unchanged_source_on_rerun_is_reused_without_reextraction(self):
        spec = _corpus_spec(self._write("corpus.md", "Built the Ford integration.\n"))
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev1 = build_revision([spec])
        claim = MemoryClaim.objects.get(candidate_memory=rev1)
        lifecycle_service.confirm_claim(claim)
        lifecycle_service.activate_revision(rev1, acknowledge_zero_employment_coverage=True)

        calls_before_rerun = LLMCallLog.objects.count()
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev2 = build_revision([spec])  # same file, unchanged content

        self.assertEqual(LLMCallLog.objects.count(), calls_before_rerun)  # no new extraction call
        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=rev2).count(), 1)
        carried_claim = MemoryClaim.objects.get(candidate_memory=rev2)
        self.assertEqual(carried_claim.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)

    def test_changed_source_on_rerun_triggers_new_extraction(self):
        path = self._write("corpus.md", "Built the Ford integration.\n")
        spec = _corpus_spec(path)
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev1 = build_revision([spec])
        claim = MemoryClaim.objects.get(candidate_memory=rev1)
        lifecycle_service.confirm_claim(claim)
        lifecycle_service.activate_revision(rev1, acknowledge_zero_employment_coverage=True)

        calls_before_rerun = LLMCallLog.objects.count()
        path.write_text("Built the Ford integration.\nAlso led a new initiative.\n", encoding="utf-8")
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev2 = build_revision([spec])

        self.assertGreater(LLMCallLog.objects.count(), calls_before_rerun)  # re-extracted
        self.assertEqual(rev2.build_summary["sources_processed"], 1)

    def test_prior_active_revision_untouched_by_rerun(self):
        spec = _corpus_spec(self._write("corpus.md", "Built the Ford integration.\n"))
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev1 = build_revision([spec])
        claim = MemoryClaim.objects.get(candidate_memory=rev1)
        lifecycle_service.confirm_claim(claim)
        lifecycle_service.activate_revision(rev1, acknowledge_zero_employment_coverage=True)

        with scripted_extraction(_ONE_ITEM_RESPONSE):
            build_revision([spec])

        rev1.refresh_from_db()
        self.assertEqual(rev1.status, CandidateMemory.Status.ACTIVE)
        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=rev1).count(), 1)


class BootstrapOperatorTextUpdateTests(TestCase):
    def setUp(self):
        self.tmp_dir = Path(self._testMethodName + "_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: [p.unlink() for p in self.tmp_dir.glob("*")] and self.tmp_dir.rmdir())

    def test_operator_text_update_creates_one_new_source_and_carries_rest_forward(self):
        path = self.tmp_dir / "corpus.md"
        path.write_text("Built the Ford integration.\n", encoding="utf-8")
        spec = _corpus_spec(path)
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev1 = build_revision([spec])
        claim = MemoryClaim.objects.get(candidate_memory=rev1)
        lifecycle_service.confirm_claim(claim)
        lifecycle_service.activate_revision(rev1, acknowledge_zero_employment_coverage=True)

        new_item_response = {
            "items": [
                {
                    "plane": "EVIDENCE",
                    "canonical_text_en": "Delivered a new Continental integration.",
                    "support": {
                        "quote": "Context: New project\nDelivered a new Continental integration.",
                        "start_line": 1, "end_line": 2, "language": "en",
                    },
                    "claim_type": "employment",
                    "subject_scope": "Continental",
                    "resume_eligible": True,
                }
            ]
        }
        with scripted_extraction(new_item_response):
            rev2 = build_revision_from_operator_text(
                text="Delivered a new Continental integration.", context="New project"
            )

        self.assertEqual(rev2.status, CandidateMemory.Status.NEEDS_REVIEW)
        # The original corpus source (and its claim) is carried forward unchanged...
        self.assertEqual(
            MemorySourceDocument.objects.filter(
                candidate_memory=rev2, logical_source_key="corpus"
            ).count(),
            1,
        )
        self.assertTrue(
            MemoryClaim.objects.filter(
                candidate_memory=rev2, canonical_text_en="Built the Ford integration."
            ).exists()
        )
        # ...plus exactly one new OPERATOR_UPDATE source with the new claim.
        new_sources = MemorySourceDocument.objects.filter(
            candidate_memory=rev2, source_role=MemorySourceDocument.SourceRole.OPERATOR_UPDATE
        )
        self.assertEqual(new_sources.count(), 1)
        self.assertTrue(
            MemoryClaim.objects.filter(
                candidate_memory=rev2, canonical_text_en="Delivered a new Continental integration."
            ).exists()
        )

        rev1.refresh_from_db()
        self.assertEqual(rev1.status, CandidateMemory.Status.ACTIVE)
