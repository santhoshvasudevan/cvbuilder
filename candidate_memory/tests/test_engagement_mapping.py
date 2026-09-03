"""Deterministic, reviewable MemoryClaim<->CareerEngagement mapping workflow (D-019). No LLM call,
no source re-extraction, anywhere in this file."""

from __future__ import annotations

from django.test import TestCase

from ..models import CandidateMemory, CareerEngagement, ClaimEngagementMapping, MemoryClaim
from ..services.engagement_mapping import (
    approve_mapping,
    propose_claim_engagement_mappings,
    reject_mapping,
)
from .factories import freeze_revision, make_claim, make_revision


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


class ProposeClaimEngagementMappingsTests(TestCase):
    def test_exact_match_on_legal_employer_and_client_organization_proposes_a_mapping(self):
        rev = make_revision()
        engagement = _make_engagement()
        claim = make_claim(
            rev, claim_type="employment_dates",
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )

        summary = propose_claim_engagement_mappings(rev)

        self.assertEqual(summary.claims_considered, 1)
        self.assertEqual(summary.proposed, 1)
        self.assertEqual(summary.ambiguous, 0)
        self.assertEqual(summary.unresolved, 0)
        mapping = ClaimEngagementMapping.objects.get(memory_claim=claim, career_engagement=engagement)
        self.assertEqual(mapping.status, ClaimEngagementMapping.Status.PROPOSED)

    def test_matching_normalization_is_case_and_whitespace_insensitive(self):
        rev = make_revision()
        _make_engagement(
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company"
        )
        make_claim(
            rev, claim_type="employment_dates",
            legal_employer="  AMBIGAI consultancy   services", client_organization="ford   motor company",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )
        summary = propose_claim_engagement_mappings(rev)
        self.assertEqual(summary.proposed, 1)

    def test_falls_back_to_subject_scope_when_legal_employer_and_client_are_blank(self):
        rev = make_revision()
        _make_engagement(legal_employer="Direct Employer Inc", client_organization="")
        make_claim(
            rev, claim_type="employment_dates", subject_scope="organization:direct employer inc",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )
        summary = propose_claim_engagement_mappings(rev)
        self.assertEqual(summary.proposed, 1)

    def test_no_matching_engagement_is_left_unresolved_not_guessed(self):
        rev = make_revision()
        _make_engagement(legal_employer="Some Other Employer", client_organization="")
        make_claim(
            rev, claim_type="employment_dates",
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )
        summary = propose_claim_engagement_mappings(rev)
        self.assertEqual(summary.proposed, 0)
        self.assertEqual(summary.unresolved, 1)
        self.assertEqual(ClaimEngagementMapping.objects.count(), 0)

    def test_ambiguous_when_more_than_one_approved_engagement_shares_the_identity_key(self):
        rev = make_revision()
        _make_engagement(
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company"
        )
        _make_engagement(
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company"
        )
        make_claim(
            rev, claim_type="employment_dates",
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )
        summary = propose_claim_engagement_mappings(rev)
        self.assertEqual(summary.proposed, 0)
        self.assertEqual(summary.ambiguous, 1)
        self.assertEqual(ClaimEngagementMapping.objects.count(), 0)

    def test_unconfirmed_claims_are_never_considered(self):
        rev = make_revision()
        _make_engagement()
        make_claim(
            rev, claim_type="employment_dates",
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            confirmation_status=MemoryClaim.ConfirmationStatus.UNCONFIRMED,
        )
        summary = propose_claim_engagement_mappings(rev)
        self.assertEqual(summary.claims_considered, 0)
        self.assertEqual(ClaimEngagementMapping.objects.count(), 0)

    def test_rerunning_the_proposal_is_idempotent(self):
        rev = make_revision()
        _make_engagement()
        make_claim(
            rev, claim_type="employment_dates",
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )
        first = propose_claim_engagement_mappings(rev)
        second = propose_claim_engagement_mappings(rev)
        self.assertEqual(first.proposed, 1)
        self.assertEqual(second.proposed, 0)
        self.assertEqual(second.already_mapped, 1)
        self.assertEqual(ClaimEngagementMapping.objects.count(), 1)

    def test_mapping_can_be_proposed_and_approved_for_a_claim_on_an_active_revision(self):
        """Proving the core design goal: proposing/approving a mapping never edits the MemoryClaim
        row, so it works even after the owning revision (and therefore the claim) is frozen."""
        rev = make_revision()
        engagement = _make_engagement()
        claim = make_claim(
            rev, claim_type="employment_dates",
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        summary = propose_claim_engagement_mappings(rev)
        self.assertEqual(summary.proposed, 1)

        mapping = ClaimEngagementMapping.objects.get(memory_claim=claim, career_engagement=engagement)
        approved = approve_mapping(mapping)
        self.assertEqual(approved.status, ClaimEngagementMapping.Status.APPROVED)
        self.assertIsNotNone(approved.reviewed_at)

        claim.refresh_from_db()
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)


class ApproveRejectMappingTests(TestCase):
    def test_reject_mapping_sets_status_and_reviewed_at(self):
        rev = make_revision()
        engagement = _make_engagement()
        claim = make_claim(rev, claim_type="employment_dates")
        mapping = ClaimEngagementMapping.objects.create(memory_claim=claim, career_engagement=engagement)

        rejected = reject_mapping(mapping)

        self.assertEqual(rejected.status, ClaimEngagementMapping.Status.REJECTED)
        self.assertIsNotNone(rejected.reviewed_at)
