"""Deterministic, reviewable MemoryClaim<->CareerEngagement mapping workflow (D-019, refined
2026-09-03: only narrative claims -- responsibilities, achievements, projects, role-specific
skills -- are ever eligible; static engagement claims (employment_dates/employment_location/
position/position_title) are owned by CareerEngagement and must never be proposed or approved).
No LLM call, no source re-extraction, anywhere in this file."""

from __future__ import annotations

from django.test import TestCase

from ..models import CandidateMemory, CareerEngagement, ClaimEngagementMapping, MemoryClaim
from ..services.engagement_mapping import (
    STATIC_ENGAGEMENT_CLAIM_TYPES,
    StaticClaimMappingError,
    approve_mapping,
    approved_narrative_claim_ids_for_engagement,
    find_mappings_needing_review,
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


def _make_narrative_claim(rev, **kwargs):
    defaults = dict(
        claim_type="responsibility",
        confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        resume_eligible=True,
    )
    defaults.update(kwargs)
    return make_claim(rev, **defaults)


class StaticEngagementClaimTypesTests(TestCase):
    def test_the_four_evidence_based_static_types_are_defined(self):
        self.assertEqual(
            STATIC_ENGAGEMENT_CLAIM_TYPES,
            frozenset({"employment_dates", "employment_location", "position", "position_title"}),
        )


class ProposeClaimEngagementMappingsTests(TestCase):
    def test_exact_match_on_legal_employer_and_client_organization_proposes_a_mapping(self):
        rev = make_revision()
        engagement = _make_engagement()
        claim = _make_narrative_claim(
            rev, legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
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
        _make_narrative_claim(
            rev,
            legal_employer="  AMBIGAI consultancy   services", client_organization="ford   motor company",
        )
        summary = propose_claim_engagement_mappings(rev)
        self.assertEqual(summary.proposed, 1)

    def test_falls_back_to_subject_scope_when_legal_employer_and_client_are_blank(self):
        rev = make_revision()
        _make_engagement(legal_employer="Direct Employer Inc", client_organization="")
        _make_narrative_claim(rev, subject_scope="organization:direct employer inc")
        summary = propose_claim_engagement_mappings(rev)
        self.assertEqual(summary.proposed, 1)

    def test_no_matching_engagement_is_left_unresolved_not_guessed(self):
        rev = make_revision()
        _make_engagement(legal_employer="Some Other Employer", client_organization="")
        _make_narrative_claim(
            rev, legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
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
        _make_narrative_claim(
            rev, legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
        )
        summary = propose_claim_engagement_mappings(rev)
        self.assertEqual(summary.proposed, 0)
        self.assertEqual(summary.ambiguous, 1)
        self.assertEqual(ClaimEngagementMapping.objects.count(), 0)

    def test_unconfirmed_claims_are_never_considered(self):
        rev = make_revision()
        _make_engagement()
        _make_narrative_claim(
            rev,
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            confirmation_status=MemoryClaim.ConfirmationStatus.UNCONFIRMED,
        )
        summary = propose_claim_engagement_mappings(rev)
        self.assertEqual(summary.claims_considered, 0)
        self.assertEqual(ClaimEngagementMapping.objects.count(), 0)

    def test_non_resume_eligible_claims_are_never_considered(self):
        rev = make_revision()
        _make_engagement()
        _make_narrative_claim(
            rev,
            legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            resume_eligible=False,
        )
        summary = propose_claim_engagement_mappings(rev)
        self.assertEqual(summary.claims_considered, 0)
        self.assertEqual(ClaimEngagementMapping.objects.count(), 0)

    def test_rerunning_the_proposal_is_idempotent(self):
        rev = make_revision()
        _make_engagement()
        _make_narrative_claim(
            rev, legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
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
        claim = _make_narrative_claim(
            rev, legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
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

    def test_static_engagement_claim_types_are_never_proposed_even_on_an_exact_match(self):
        rev = make_revision()
        _make_engagement()
        for claim_type in STATIC_ENGAGEMENT_CLAIM_TYPES:
            _make_narrative_claim(
                rev, claim_type=claim_type,
                legal_employer="Ambigai Consultancy Services", client_organization="Ford Motor Company",
            )

        summary = propose_claim_engagement_mappings(rev)

        self.assertEqual(summary.claims_considered, 0)
        self.assertEqual(summary.proposed, 0)
        self.assertEqual(ClaimEngagementMapping.objects.count(), 0)

    def test_global_career_level_claim_is_never_matched(self):
        rev = make_revision()
        _make_engagement()
        _make_narrative_claim(rev, subject_scope="career")
        summary = propose_claim_engagement_mappings(rev)
        self.assertEqual(summary.proposed, 0)
        self.assertEqual(summary.unresolved, 1)
        self.assertEqual(ClaimEngagementMapping.objects.count(), 0)


class ApproveRejectMappingTests(TestCase):
    def test_reject_mapping_sets_status_and_reviewed_at(self):
        rev = make_revision()
        engagement = _make_engagement()
        claim = _make_narrative_claim(rev)
        mapping = ClaimEngagementMapping.objects.create(memory_claim=claim, career_engagement=engagement)

        rejected = reject_mapping(mapping)

        self.assertEqual(rejected.status, ClaimEngagementMapping.Status.REJECTED)
        self.assertIsNotNone(rejected.reviewed_at)

    def test_approve_mapping_succeeds_for_a_narrative_claim(self):
        rev = make_revision()
        engagement = _make_engagement()
        claim = _make_narrative_claim(rev)
        mapping = ClaimEngagementMapping.objects.create(memory_claim=claim, career_engagement=engagement)

        approved = approve_mapping(mapping)

        self.assertEqual(approved.status, ClaimEngagementMapping.Status.APPROVED)

    def test_approve_mapping_refuses_a_static_engagement_claim(self):
        rev = make_revision()
        engagement = _make_engagement()
        claim = make_claim(rev, claim_type="employment_dates")
        mapping = ClaimEngagementMapping.objects.create(memory_claim=claim, career_engagement=engagement)

        with self.assertRaises(StaticClaimMappingError):
            approve_mapping(mapping)

        mapping.refresh_from_db()
        self.assertEqual(mapping.status, ClaimEngagementMapping.Status.PROPOSED)
        self.assertIsNone(mapping.reviewed_at)


class FindMappingsNeedingReviewTests(TestCase):
    def test_categorizes_static_and_narrative_mappings_correctly(self):
        rev = make_revision()
        engagement = _make_engagement()
        static_claim = make_claim(rev, claim_type="employment_dates")
        narrative_claim = _make_narrative_claim(rev)
        static_mapping = ClaimEngagementMapping.objects.create(
            memory_claim=static_claim, career_engagement=engagement,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        narrative_mapping = ClaimEngagementMapping.objects.create(
            memory_claim=narrative_claim, career_engagement=engagement,
        )

        report = find_mappings_needing_review(rev)

        self.assertEqual([m.pk for m in report.to_reject], [static_mapping.pk])
        self.assertEqual([m.pk for m in report.to_retain], [narrative_mapping.pk])

    def test_never_writes_anything(self):
        rev = make_revision()
        engagement = _make_engagement()
        claim = make_claim(rev, claim_type="employment_dates")
        mapping = ClaimEngagementMapping.objects.create(memory_claim=claim, career_engagement=engagement)

        find_mappings_needing_review(rev)

        mapping.refresh_from_db()
        self.assertEqual(mapping.status, ClaimEngagementMapping.Status.PROPOSED)

    def test_never_touches_a_different_revision(self):
        other_rev = make_revision()
        other_engagement = _make_engagement()
        other_claim = make_claim(other_rev, claim_type="employment_dates")
        ClaimEngagementMapping.objects.create(memory_claim=other_claim, career_engagement=other_engagement)

        rev = make_revision()
        report = find_mappings_needing_review(rev)

        self.assertEqual(report.to_reject, [])
        self.assertEqual(report.to_retain, [])


class ApprovedNarrativeClaimIdsForEngagementTests(TestCase):
    def test_returns_only_approved_narrative_mappings(self):
        rev = make_revision()
        engagement = _make_engagement()
        approved_claim = _make_narrative_claim(rev)
        proposed_claim = _make_narrative_claim(rev)
        ClaimEngagementMapping.objects.create(
            memory_claim=approved_claim, career_engagement=engagement,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        ClaimEngagementMapping.objects.create(
            memory_claim=proposed_claim, career_engagement=engagement,
            status=ClaimEngagementMapping.Status.PROPOSED,
        )

        claim_ids = approved_narrative_claim_ids_for_engagement(engagement)

        self.assertEqual(claim_ids, [approved_claim.claim_id])
