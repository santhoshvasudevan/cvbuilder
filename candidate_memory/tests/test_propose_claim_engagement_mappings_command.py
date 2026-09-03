"""The propose_claim_engagement_mappings management command (D-019 operator entry point). No LLM
call anywhere in this file."""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ..models import CareerEngagement, ClaimEngagementMapping, MemoryClaim
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
    call_command("propose_claim_engagement_mappings", *args, stdout=out)
    return out.getvalue()


class ProposeClaimEngagementMappingsCommandTests(TestCase):
    def test_command_is_registered_and_discoverable(self):
        # Regression guard for the exact bug this command fixes: manage.py must recognize this as
        # a real command at all (a genuinely unregistered command raises CommandError with
        # "Unknown command", not the CommandError this test expects for a *known* command given a
        # bad argument value).
        rev = make_revision()
        output = _run("--candidate-memory-id", str(rev.pk))
        self.assertIn("Claims considered: 0", output)

    def test_unknown_candidate_memory_id_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command("propose_claim_engagement_mappings", "--candidate-memory-id", "999999")

    def test_creates_proposed_mappings_for_exact_matches(self):
        rev = make_revision()
        engagement = _make_engagement()
        claim = make_claim(
            rev, claim_type="employment_dates",
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )

        output = _run("--candidate-memory-id", str(rev.pk))

        mapping = ClaimEngagementMapping.objects.get(memory_claim=claim, career_engagement=engagement)
        self.assertEqual(mapping.status, ClaimEngagementMapping.Status.PROPOSED)
        self.assertIn("Proposed: 1", output)
        self.assertIn("Already existing (idempotent skip): 0", output)
        self.assertIn("Ambiguous", output)
        self.assertIn("Unresolved/unmapped", output)

    def test_never_creates_a_mapping_beyond_proposed_status(self):
        rev = make_revision()
        _make_engagement()
        make_claim(
            rev, claim_type="employment_dates",
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )

        _run("--candidate-memory-id", str(rev.pk))

        self.assertEqual(ClaimEngagementMapping.objects.count(), 1)
        self.assertTrue(
            ClaimEngagementMapping.objects.filter(status=ClaimEngagementMapping.Status.PROPOSED).exists()
        )
        self.assertFalse(
            ClaimEngagementMapping.objects.filter(status=ClaimEngagementMapping.Status.APPROVED).exists()
        )

    def test_dry_run_reports_counts_but_writes_nothing(self):
        rev = make_revision()
        _make_engagement()
        make_claim(
            rev, claim_type="employment_dates",
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )

        output = _run("--candidate-memory-id", str(rev.pk), "--dry-run")

        self.assertIn("Proposed: 1", output)
        self.assertIn("no mappings were actually written", output)
        self.assertEqual(ClaimEngagementMapping.objects.count(), 0)

    def test_rerunning_without_dry_run_is_idempotent(self):
        rev = make_revision()
        _make_engagement()
        make_claim(
            rev, claim_type="employment_dates",
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )

        _run("--candidate-memory-id", str(rev.pk))
        second_output = _run("--candidate-memory-id", str(rev.pk))

        self.assertEqual(ClaimEngagementMapping.objects.count(), 1)
        self.assertIn("Proposed: 0", second_output)
        self.assertIn("Already existing (idempotent skip): 1", second_output)

    def test_ambiguous_and_unresolved_claims_never_produce_a_mapping(self):
        rev = make_revision()
        make_claim(
            rev, claim_type="employment_dates",
            legal_employer="Some Employer With No Engagement Yet", client_organization="",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )

        output = _run("--candidate-memory-id", str(rev.pk))

        self.assertEqual(ClaimEngagementMapping.objects.count(), 0)
        self.assertIn("Proposed: 0", output)
        self.assertIn("Unresolved/unmapped (no matching APPROVED engagement): 1", output)
