"""Deterministic snapshot generation (D-015): active-revision-only, human-reference-only,
excludes unconfirmed/ineligible claims, never mutates CandidateMemory."""

from __future__ import annotations

from pathlib import Path

from django.test import TestCase

from ..models import CandidateMemory, MemoryClaim, MemoryClaimSupport, MemoryConflict
from ..services.snapshot_export import NoActiveRevisionError, export_snapshot, generate_snapshot_markdown
from .factories import freeze_revision, make_claim, make_revision, make_source


class SnapshotGenerationTests(TestCase):
    def test_generation_requires_active_revision(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        with self.assertRaises(NoActiveRevisionError):
            generate_snapshot_markdown(rev)

    def test_confirmed_eligible_claim_is_included(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Built the Ford integration.\n")
        claim = make_claim(
            rev, canonical_text_en="Built the Ford integration.", resume_eligible=True,
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )
        MemoryClaimSupport.objects.create(
            memory_claim=claim, memory_source_document=source,
            quotation="Built the Ford integration.", start_line=1, end_line=1,
            source_language="en", support_role=MemoryClaimSupport.SupportRole.PRIMARY,
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        markdown = generate_snapshot_markdown(rev)
        self.assertIn("Built the Ford integration.", markdown)
        self.assertIn(claim.claim_id, markdown)

    def test_unconfirmed_claim_is_excluded(self):
        rev = make_revision()
        make_claim(
            rev, canonical_text_en="Unconfirmed fact.", resume_eligible=True,
            confirmation_status=MemoryClaim.ConfirmationStatus.UNCONFIRMED,
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        markdown = generate_snapshot_markdown(rev)
        self.assertNotIn("Unconfirmed fact.", markdown)

    def test_not_resume_eligible_claim_is_excluded(self):
        rev = make_revision()
        make_claim(
            rev, canonical_text_en="Not eligible fact.", resume_eligible=False,
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        markdown = generate_snapshot_markdown(rev)
        self.assertNotIn("Not eligible fact.", markdown)

    def test_unresolved_conflicts_are_summarized_concisely(self):
        rev = make_revision()
        claim = make_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT)
        conflict = MemoryConflict.objects.create(
            candidate_memory=rev, conflict_key="continental_dates", description="Conflicting dates.",
            status=MemoryConflict.Status.OPEN,
        )
        conflict.involved_claims.set([claim])
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        markdown = generate_snapshot_markdown(rev)
        self.assertIn("continental_dates", markdown)

    def test_regenerating_twice_against_same_revision_is_byte_identical(self):
        rev = make_revision(status=CandidateMemory.Status.ACTIVE)
        first = generate_snapshot_markdown(rev)
        second = generate_snapshot_markdown(rev)
        self.assertEqual(first, second)

    def test_header_marks_snapshot_as_non_ingestible_reference_only(self):
        rev = make_revision(status=CandidateMemory.Status.ACTIVE)
        markdown = generate_snapshot_markdown(rev)
        self.assertIn("GENERATED_REFERENCE_ONLY: true", markdown)
        self.assertIn("CANDIDATE_MEMORY_EVIDENCE_SOURCE: false", markdown)
        self.assertIn("REINGESTION_ALLOWED: false", markdown)


class PresentationModeRenderingTests(TestCase):
    """Audit repair: presentation_mode must actually change what the snapshot displays, and must
    never make the underlying legal-employer/client facts disappear from PostgreSQL."""

    def _ford_claim(self, presentation_mode):
        rev = make_revision()
        claim = make_claim(
            rev, canonical_text_en="Delivered the Ford integration project as Solutions Architect.",
            subject_scope="Ford Motor Company", resume_eligible=True,
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
            legal_employer="Ambigai Consultancy Services",
            client_organization="Ford Motor Company",
            presentation_mode=presentation_mode,
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        return rev, claim

    def test_client_centric_shows_client_and_hides_legal_employer_by_default(self):
        rev, claim = self._ford_claim(MemoryClaim.PresentationMode.CLIENT_CENTRIC)
        markdown = generate_snapshot_markdown(rev)
        self.assertIn("Ford Motor Company", markdown)  # section heading = subject_scope
        self.assertIn("client assignment", markdown)
        self.assertNotIn("Ambigai", markdown)
        # The underlying fact is untouched in PostgreSQL regardless of the display choice.
        claim.refresh_from_db()
        self.assertEqual(claim.legal_employer, "Ambigai Consultancy Services")

    def test_legal_employer_explicit_shows_ambigai_and_client_assignment(self):
        rev, claim = self._ford_claim(MemoryClaim.PresentationMode.LEGAL_EMPLOYER_EXPLICIT)
        markdown = generate_snapshot_markdown(rev)
        self.assertIn("Ambigai Consultancy Services", markdown)
        self.assertIn("Ford Motor Company", markdown)
        claim.refresh_from_db()
        self.assertEqual(claim.client_organization, "Ford Motor Company")

    def test_combined_shows_both_legal_employer_and_client(self):
        rev, claim = self._ford_claim(MemoryClaim.PresentationMode.COMBINED)
        markdown = generate_snapshot_markdown(rev)
        self.assertIn("Ambigai Consultancy Services", markdown)
        self.assertIn("Ford Motor Company", markdown)
        self.assertIn("client assignment", markdown)
        claim.refresh_from_db()
        self.assertEqual(claim.legal_employer, "Ambigai Consultancy Services")
        self.assertEqual(claim.client_organization, "Ford Motor Company")

    def test_switching_presentation_mode_never_mutates_underlying_facts(self):
        rev = make_revision()
        claim = make_claim(
            rev, subject_scope="Continental", resume_eligible=True,
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
            legal_employer="Ambigai Consultancy Services", client_organization="Continental",
            presentation_mode=MemoryClaim.PresentationMode.CLIENT_CENTRIC,
        )
        from ..services import lifecycle as lifecycle_service

        lifecycle_service.correct_claim(
            claim, presentation_mode=MemoryClaim.PresentationMode.LEGAL_EMPLOYER_EXPLICIT
        )
        claim.refresh_from_db()
        self.assertEqual(claim.legal_employer, "Ambigai Consultancy Services")
        self.assertEqual(claim.client_organization, "Continental")
        self.assertEqual(claim.presentation_mode, MemoryClaim.PresentationMode.LEGAL_EMPLOYER_EXPLICIT)

    def test_ordinary_claim_without_legal_employer_split_is_unaffected(self):
        rev = make_revision()
        make_claim(
            rev, canonical_text_en="Built an internal tool.", subject_scope="Acme Corp",
            resume_eligible=True, confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        markdown = generate_snapshot_markdown(rev)
        self.assertNotIn("client assignment", markdown)


class SnapshotExportTests(TestCase):
    def test_export_requires_active_revision(self):
        make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        with self.assertRaises(NoActiveRevisionError):
            export_snapshot(Path("export_test_snapshot.md"))

    def test_export_writes_file_and_does_not_mutate_candidate_memory(self):
        rev = make_revision(status=CandidateMemory.Status.ACTIVE)
        original_build_summary = dict(rev.build_summary)
        target = Path("export_test_snapshot.md")
        self.addCleanup(lambda: target.unlink(missing_ok=True))

        result_path = export_snapshot(target)

        self.assertTrue(result_path.exists())
        rev.refresh_from_db()
        self.assertEqual(rev.status, CandidateMemory.Status.ACTIVE)
        self.assertEqual(rev.build_summary, original_build_summary)

    def test_export_leaves_no_temp_file_behind_on_success(self):
        make_revision(status=CandidateMemory.Status.ACTIVE)
        target = Path("export_test_snapshot_tmp_check.md")
        self.addCleanup(lambda: target.unlink(missing_ok=True))
        export_snapshot(target)
        leftover_temp_files = list(target.parent.glob(f".{target.name}.*.tmp"))
        self.assertEqual(leftover_temp_files, [])

    def test_export_twice_produces_deterministic_replacement(self):
        make_revision(status=CandidateMemory.Status.ACTIVE)
        target = Path("export_test_snapshot_replace.md")
        self.addCleanup(lambda: target.unlink(missing_ok=True))

        export_snapshot(target)
        first_content = target.read_text(encoding="utf-8")
        export_snapshot(target)
        second_content = target.read_text(encoding="utf-8")
        self.assertEqual(first_content, second_content)

    def test_simulated_failure_mid_write_does_not_corrupt_previous_snapshot(self):
        from unittest import mock

        make_revision(status=CandidateMemory.Status.ACTIVE)
        target = Path("export_test_snapshot_failure.md")
        self.addCleanup(lambda: target.unlink(missing_ok=True))
        self.addCleanup(
            lambda: [p.unlink() for p in target.parent.glob(f".{target.name}.*.tmp")]
        )

        export_snapshot(target)
        original_content = target.read_text(encoding="utf-8")

        with mock.patch("os.replace", side_effect=OSError("simulated failure during replace")):
            with self.assertRaises(OSError):
                export_snapshot(target)

        # The previous snapshot is untouched -- no partial/corrupt overwrite.
        self.assertEqual(target.read_text(encoding="utf-8"), original_content)
        # No leaked temp file after the failure.
        self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])
