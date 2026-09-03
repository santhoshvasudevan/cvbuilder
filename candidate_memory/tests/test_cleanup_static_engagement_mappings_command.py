"""The cleanup_static_engagement_mappings management command (D-019 refinement, 2026-09-03). No
LLM call anywhere in this file."""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ..models import CareerEngagement, ClaimEngagementMapping
from .factories import make_claim, make_revision


def _make_engagement(**kwargs) -> CareerEngagement:
    defaults = dict(
        legal_employer="Ambigai Consultancy Services",
        client_organization="Ford Motor Company",
        approved_role_title="Senior Cloud Engineer",
        start_year=2017,
        start_month=7,
        end_status=CareerEngagement.EndStatus.PRESENT,
        approval_status=CareerEngagement.ApprovalStatus.APPROVED,
    )
    defaults.update(kwargs)
    return CareerEngagement.objects.create(**defaults)


def _run(*args):
    out = StringIO()
    call_command("cleanup_static_engagement_mappings", *args, stdout=out)
    return out.getvalue()


class CleanupStaticEngagementMappingsCommandTests(TestCase):
    def test_unknown_candidate_memory_id_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command("cleanup_static_engagement_mappings", "--candidate-memory-id", "999999")

    def test_dry_run_by_default_reports_but_never_writes(self):
        rev = make_revision()
        engagement = _make_engagement()
        static_claim = make_claim(rev, claim_type="employment_dates")
        static_mapping = ClaimEngagementMapping.objects.create(
            memory_claim=static_claim, career_engagement=engagement,
            status=ClaimEngagementMapping.Status.APPROVED,
        )

        output = _run("--candidate-memory-id", str(rev.pk))

        self.assertIn("Static mappings to reject (1)", output)
        self.assertIn(static_claim.claim_id, output)
        self.assertIn("--apply not given", output)
        static_mapping.refresh_from_db()
        self.assertEqual(static_mapping.status, ClaimEngagementMapping.Status.APPROVED)

    def test_reports_narrative_mappings_separately_as_retained(self):
        rev = make_revision()
        engagement = _make_engagement()
        narrative_claim = make_claim(rev, claim_type="responsibility")
        ClaimEngagementMapping.objects.create(memory_claim=narrative_claim, career_engagement=engagement)

        output = _run("--candidate-memory-id", str(rev.pk))

        self.assertIn("Static mappings to reject (0)", output)
        self.assertIn("Narrative mappings to retain (1)", output)
        self.assertIn(narrative_claim.claim_id, output)

    def test_apply_rejects_static_mappings_only(self):
        rev = make_revision()
        engagement = _make_engagement()
        static_claim = make_claim(rev, claim_type="employment_dates")
        narrative_claim = make_claim(rev, claim_type="responsibility")
        static_mapping = ClaimEngagementMapping.objects.create(
            memory_claim=static_claim, career_engagement=engagement,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        narrative_mapping = ClaimEngagementMapping.objects.create(
            memory_claim=narrative_claim, career_engagement=engagement,
        )

        output = _run("--candidate-memory-id", str(rev.pk), "--apply")

        static_mapping.refresh_from_db()
        narrative_mapping.refresh_from_db()
        self.assertEqual(static_mapping.status, ClaimEngagementMapping.Status.REJECTED)
        self.assertIsNotNone(static_mapping.reviewed_at)
        self.assertEqual(narrative_mapping.status, ClaimEngagementMapping.Status.PROPOSED)
        self.assertIn("Rejected 1 static mapping(s).", output)

    def test_apply_is_idempotent(self):
        rev = make_revision()
        engagement = _make_engagement()
        static_claim = make_claim(rev, claim_type="position_title")
        ClaimEngagementMapping.objects.create(memory_claim=static_claim, career_engagement=engagement)

        _run("--candidate-memory-id", str(rev.pk), "--apply")
        second_output = _run("--candidate-memory-id", str(rev.pk), "--apply")

        # Still finds it (it's REJECTED, but claim_type is still static -- to_reject is
        # status-independent) and re-rejecting an already-REJECTED mapping is a harmless no-op.
        self.assertIn("Rejected 1 static mapping(s).", second_output)
        self.assertEqual(ClaimEngagementMapping.objects.count(), 1)

    def test_never_touches_a_different_revision(self):
        other_rev = make_revision()
        other_engagement = _make_engagement()
        other_claim = make_claim(other_rev, claim_type="employment_dates")
        other_mapping = ClaimEngagementMapping.objects.create(
            memory_claim=other_claim, career_engagement=other_engagement,
            status=ClaimEngagementMapping.Status.APPROVED,
        )

        rev = make_revision()
        _run("--candidate-memory-id", str(rev.pk), "--apply")

        other_mapping.refresh_from_db()
        self.assertEqual(other_mapping.status, ClaimEngagementMapping.Status.APPROVED)
