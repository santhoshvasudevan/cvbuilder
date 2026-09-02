"""Dry-run preflight (audit repair): proves zero provider calls and zero database writes, and
that the reported chunk count matches the exact calculation the real build uses."""

from __future__ import annotations

from pathlib import Path

from django.test import TestCase

from ..models import CandidateMemory, MemorySourceDocument
from ..services.bootstrap import SourceSpec, build_revision
from ..services.chunking import chunk_source
from ..services.extraction import DEFAULT_MAX_OUTPUT_TOKENS
from ..services.preflight import build_preflight_report
from ..services.revision import abandon_revision
from .factories import make_fake_stage_assignment, scripted_extraction

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


class DryRunPreflightTests(TestCase):
    def setUp(self):
        self.tmp_dir = Path(self._testMethodName + "_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: [p.unlink() for p in self.tmp_dir.glob("*")] and self.tmp_dir.rmdir())

    def _spec(self, name: str, content: str, role=MemorySourceDocument.SourceRole.ENGLISH_CORPUS):
        path = self.tmp_dir / name
        path.write_text(content, encoding="utf-8")
        return SourceSpec(
            path=path, logical_source_key=name, source_role=role, language="en", precedence=2,
        )

    def test_dry_run_makes_zero_database_writes(self):
        spec = self._spec("corpus.md", "Built the Ford integration.\n")
        before_cm = CandidateMemory.objects.count()

        report = build_preflight_report([spec])

        self.assertEqual(CandidateMemory.objects.count(), before_cm)
        self.assertEqual(MemorySourceDocument.objects.count(), 0)
        self.assertEqual(len(report.sources), 1)
        self.assertEqual(report.sources[0].would_reuse_unchanged, False)
        self.assertEqual(report.sources[0].chunk_count, 1)
        self.assertEqual(report.total_chunks, 1)

    def test_dry_run_never_calls_the_provider(self):
        spec = self._spec("corpus.md", "Built the Ford integration.\n")
        make_fake_stage_assignment()
        from llm_provider.models import LLMCallLog

        before = LLMCallLog.objects.count()
        build_preflight_report([spec])
        self.assertEqual(LLMCallLog.objects.count(), before)

    def test_chunk_count_matches_real_chunking_calculation(self):
        content = "\n".join(f"line {i}" for i in range(1, 201))
        spec = self._spec("corpus.md", content)
        report = build_preflight_report([spec])
        self.assertEqual(report.sources[0].chunk_count, len(chunk_source(content)))

    def test_reports_existing_working_revision(self):
        spec = self._spec("corpus.md", "Built the Ford integration.\n")
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev = build_revision([spec])

        report = build_preflight_report([spec])
        self.assertEqual(report.existing_working_revision, f"v{rev.version} (NEEDS_REVIEW)")

    def test_reports_none_when_no_working_revision(self):
        spec = self._spec("corpus.md", "Built the Ford integration.\n")
        report = build_preflight_report([spec])
        self.assertIsNone(report.existing_working_revision)

    def test_unchanged_source_reports_zero_chunks_and_reuse(self):
        spec = self._spec("corpus.md", "Built the Ford integration.\n")
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev = build_revision([spec])
        from ..services import lifecycle as lifecycle_service

        for claim in rev.claims.all():
            lifecycle_service.confirm_claim(claim)
        lifecycle_service.activate_revision(rev)

        # No working revision now (it's ACTIVE) -- preflight should compare against ACTIVE.
        same_spec = self._spec("corpus.md", "Built the Ford integration.\n")
        report = build_preflight_report([same_spec])
        self.assertTrue(report.sources[0].would_reuse_unchanged)
        self.assertEqual(report.sources[0].chunk_count, 0)
        self.assertEqual(report.total_chunks, 0)

    def test_credential_configured_is_boolean_only_never_the_value(self):
        spec = self._spec("corpus.md", "Built the Ford integration.\n")
        report = build_preflight_report([spec])
        # No StageModelAssignment exists in a fresh test DB -- stage_configured is False and
        # would_make_live_call must be False.
        self.assertFalse(report.stage_configured)
        self.assertFalse(report.would_make_live_call)
        self.assertIsInstance(report.credential_configured, bool)

    def test_fake_provider_never_reports_would_make_live_call(self):
        spec = self._spec("corpus.md", "Built the Ford integration.\n")
        make_fake_stage_assignment()
        report = build_preflight_report([spec])
        self.assertTrue(report.stage_configured)
        self.assertFalse(report.would_make_live_call)  # FAKE provider is never a live call

    def test_dry_run_does_not_abandon_or_mutate_an_existing_working_revision(self):
        spec = self._spec("corpus.md", "Built the Ford integration.\n")
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev = build_revision([spec])

        build_preflight_report([spec])

        rev.refresh_from_db()
        self.assertEqual(rev.status, CandidateMemory.Status.NEEDS_REVIEW)  # untouched, not FAILED

    def test_dry_run_ignores_a_failed_revision_when_comparing_unchanged_sources(self):
        spec = self._spec("corpus.md", "Built the Ford integration.\n")
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev = build_revision([spec])
        abandon_revision(rev, reason="test")

        report = build_preflight_report([spec])
        self.assertIsNone(report.existing_working_revision)
        # No ACTIVE revision either, so nothing to reuse -- must process.
        self.assertFalse(report.sources[0].would_reuse_unchanged)


class OutputTokenLimitPreflightTests(TestCase):
    """Audit repair: the dry-run must display the same finite output-token limit the real build
    will use, and compute the maximum theoretical output exposure across the whole run."""

    def setUp(self):
        self.tmp_dir = Path(self._testMethodName + "_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: [p.unlink() for p in self.tmp_dir.glob("*")] and self.tmp_dir.rmdir())

    def _spec(self, name: str, content: str):
        path = self.tmp_dir / name
        path.write_text(content, encoding="utf-8")
        return SourceSpec(
            path=path, logical_source_key=name,
            source_role=MemorySourceDocument.SourceRole.ENGLISH_CORPUS, language="en", precedence=2,
        )

    def test_defaults_to_the_conservative_canary_limit_when_no_stage_assignment_exists(self):
        spec = self._spec("corpus.md", "Built the Ford integration.\n")
        report = build_preflight_report([spec])
        self.assertEqual(report.configured_max_output_tokens, DEFAULT_MAX_OUTPUT_TOKENS)
        self.assertEqual(
            report.max_theoretical_output_tokens, DEFAULT_MAX_OUTPUT_TOKENS * report.total_chunks
        )

    def test_uses_the_assigned_models_registry_ceiling_when_set(self):
        model = make_fake_stage_assignment()
        model.max_output_tokens = 8192
        model.save(update_fields=["max_output_tokens"])

        spec = self._spec("corpus.md", "Built the Ford integration.\n")
        report = build_preflight_report([spec])
        self.assertEqual(report.configured_max_output_tokens, 8192)
        self.assertEqual(report.max_theoretical_output_tokens, 8192 * report.total_chunks)

    def test_max_theoretical_output_is_zero_when_nothing_needs_processing(self):
        spec = self._spec("corpus.md", "Built the Ford integration.\n")
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev = build_revision([spec])
        from ..services import lifecycle as lifecycle_service

        for claim in rev.claims.all():
            lifecycle_service.confirm_claim(claim)
        lifecycle_service.activate_revision(rev)

        same_spec = self._spec("corpus.md", "Built the Ford integration.\n")
        report = build_preflight_report([same_spec])
        self.assertEqual(report.total_chunks, 0)
        self.assertEqual(report.max_theoretical_output_tokens, 0)
