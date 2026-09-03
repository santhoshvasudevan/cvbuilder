"""Bootstrap idempotency and crash recovery (audit repair): a working revision is never silently
duplicated, an unexpected mid-build failure marks the revision FAILED rather than leaving it stuck
looking review-ready, the ACTIVE revision is never touched by any of this, and an explicit
operator action (abandon_revision / --abandon-existing) is the only way to discard a stuck
revision and retry."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from django.test import TestCase

from llm_provider.adapters.fake import FakeAdapter

from ..exceptions import ExistingWorkingRevisionError
from ..models import CandidateMemory, MemoryClaim, MemorySourceDocument
from ..services import lifecycle as lifecycle_service
from ..services import revision as revision_service
from ..services.bootstrap import SourceSpec, build_revision
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


@contextmanager
def _fail_on_nth_call(response: dict, fail_at_call: int):
    """Like `scripted_extraction`, but raises on the Nth `get_adapter_for_stage` call instead of
    always succeeding -- simulates an unexpected failure partway through a multi-chunk build."""
    model = make_fake_stage_assignment()
    calls = {"count": 0}

    def _get_adapter_for_stage(stage):
        calls["count"] += 1
        if calls["count"] >= fail_at_call:
            raise RuntimeError("simulated provider outage mid-build")
        return FakeAdapter(model, fixed_response=response)

    with mock.patch("candidate_memory.services.extraction.get_adapter_for_stage", _get_adapter_for_stage):
        yield


class BootstrapIdempotencyTests(TestCase):
    def setUp(self):
        self.tmp_dir = Path(self._testMethodName + "_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: [p.unlink() for p in self.tmp_dir.glob("*")] and self.tmp_dir.rmdir())

    def _spec(self) -> SourceSpec:
        path = self.tmp_dir / "corpus.md"
        path.write_text("Built the Ford integration.\n", encoding="utf-8")
        return SourceSpec(
            path=path, logical_source_key="corpus",
            source_role=MemorySourceDocument.SourceRole.ENGLISH_CORPUS, language="en", precedence=2,
        )

    def test_second_invocation_while_working_revision_exists_is_refused(self):
        spec = self._spec()
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev1 = build_revision([spec])

        with self.assertRaises(ExistingWorkingRevisionError):
            with scripted_extraction(_ONE_ITEM_RESPONSE):
                build_revision([spec])

        # No second (or third) CandidateMemory was silently created.
        self.assertEqual(CandidateMemory.objects.count(), 1)
        rev1.refresh_from_db()
        self.assertEqual(rev1.status, CandidateMemory.Status.NEEDS_REVIEW)

    def test_abandon_existing_flag_marks_old_revision_failed_and_proceeds(self):
        spec = self._spec()
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev1 = build_revision([spec])

        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev2 = build_revision([spec], abandon_existing=True)

        rev1.refresh_from_db()
        self.assertEqual(rev1.status, CandidateMemory.Status.FAILED)
        self.assertIn("abandoned_reason", rev1.build_summary)
        self.assertEqual(rev2.status, CandidateMemory.Status.NEEDS_REVIEW)
        self.assertNotEqual(rev1.pk, rev2.pk)

    def test_explicit_abandon_revision_service_function(self):
        spec = self._spec()
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            rev1 = build_revision([spec])

        revision_service.abandon_revision(rev1, reason="operator changed their mind")
        rev1.refresh_from_db()
        self.assertEqual(rev1.status, CandidateMemory.Status.FAILED)
        self.assertEqual(rev1.build_summary["abandoned_reason"], "operator changed their mind")

        # A FAILED revision is frozen like SUPERSEDED -- no further edits.
        claim = rev1.claims.first()
        if claim is not None:
            with self.assertRaises(Exception):
                lifecycle_service.confirm_claim(claim)

    def test_retry_after_failure_creates_a_fresh_revision(self):
        spec = self._spec()
        with self.assertRaises(RuntimeError):
            with _fail_on_nth_call(_ONE_ITEM_RESPONSE, fail_at_call=1):
                build_revision([spec])

        failed = CandidateMemory.objects.get()
        self.assertEqual(failed.status, CandidateMemory.Status.FAILED)
        self.assertIn("failure_reason", failed.build_summary)

        with scripted_extraction(_ONE_ITEM_RESPONSE):
            retried = build_revision([spec], abandon_existing=True)
        self.assertEqual(retried.status, CandidateMemory.Status.NEEDS_REVIEW)
        self.assertEqual(retried.claims.count(), 1)


class BootstrapPartialFailureTests(TestCase):
    def setUp(self):
        self.tmp_dir = Path(self._testMethodName + "_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: [p.unlink() for p in self.tmp_dir.glob("*")] and self.tmp_dir.rmdir())

    def test_failure_on_second_chunk_marks_revision_failed_not_stuck_building(self):
        # Two chunks' worth of content (chunk_lines default is 80).
        content = "\n".join(f"Line {i}: Built the Ford integration." for i in range(1, 161))
        path = self.tmp_dir / "corpus.md"
        path.write_text(content, encoding="utf-8")
        spec = SourceSpec(
            path=path, logical_source_key="corpus",
            source_role=MemorySourceDocument.SourceRole.ENGLISH_CORPUS, language="en", precedence=2,
        )

        with self.assertRaises(RuntimeError):
            with _fail_on_nth_call(_ONE_ITEM_RESPONSE, fail_at_call=2):
                build_revision([spec])

        rev = CandidateMemory.objects.get()
        self.assertEqual(rev.status, CandidateMemory.Status.FAILED)
        self.assertIn("failure_reason", rev.build_summary)
        # It must never look review-ready.
        self.assertNotEqual(rev.status, CandidateMemory.Status.NEEDS_REVIEW)

    def test_failure_never_touches_the_active_revision(self):
        spec = SourceSpec(
            path=self.tmp_dir / "corpus.md", logical_source_key="corpus",
            source_role=MemorySourceDocument.SourceRole.ENGLISH_CORPUS, language="en", precedence=2,
        )
        spec.path.write_text("Built the Ford integration.\n", encoding="utf-8")
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            active = build_revision([spec])
        claim = active.claims.first()
        lifecycle_service.confirm_claim(claim)
        lifecycle_service.activate_revision(active, acknowledge_zero_employment_coverage=True)
        active.refresh_from_db()
        self.assertEqual(active.status, CandidateMemory.Status.ACTIVE)

        other_spec = SourceSpec(
            path=self.tmp_dir / "corpus2.md", logical_source_key="corpus2",
            source_role=MemorySourceDocument.SourceRole.ENGLISH_CORPUS, language="en", precedence=2,
        )
        other_spec.path.write_text("Something new.\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            with _fail_on_nth_call(_ONE_ITEM_RESPONSE, fail_at_call=1):
                build_revision([other_spec], abandon_existing=True)

        active.refresh_from_db()
        self.assertEqual(active.status, CandidateMemory.Status.ACTIVE)
        self.assertEqual(
            MemoryClaim.objects.filter(candidate_memory=active).count(), 1
        )
