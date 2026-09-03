from __future__ import annotations

from django.test import TestCase

from candidate_memory.models import CandidateMemory, CareerEngagement, ClaimEngagementMapping, MemoryClaim

from ..services.retrieve import NoActiveCandidateMemoryError, get_active_candidate_memory, retrieve_context
from .factories import (
    freeze_revision,
    make_active_revision,
    make_engagement,
    make_narrative_claim,
    make_revision,
)


class GetActiveCandidateMemoryTests(TestCase):
    def test_raises_when_no_active_revision_exists(self):
        with self.assertRaises(NoActiveCandidateMemoryError):
            get_active_candidate_memory()

    def test_returns_the_active_revision(self):
        rev = make_active_revision()
        self.assertEqual(get_active_candidate_memory().pk, rev.pk)


class RetrieveContextTests(TestCase):
    def test_excludes_unconfirmed_and_retired_claims(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        make_narrative_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.UNCONFIRMED)
        make_narrative_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.RETIRED)
        confirmed = make_narrative_claim(rev)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        context = retrieve_context(rev)
        self.assertEqual(context.claim_ids, [confirmed.claim_id])

    def test_excludes_non_resume_eligible_claims(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        make_narrative_claim(rev, resume_eligible=False)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        self.assertEqual(retrieve_context(rev).claims, [])

    def test_excludes_static_engagement_claim_types(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        make_narrative_claim(rev, claim_type="employment_dates")
        make_narrative_claim(rev, claim_type="position_title")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        self.assertEqual(retrieve_context(rev).claims, [])

    def test_includes_approved_engagement_mapped_narrative_claim(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service.")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim,
            career_engagement=engagement,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        context = retrieve_context(rev)
        self.assertEqual(len(context.claims), 1)
        self.assertEqual(context.claims[0].engagement_id, engagement.engagement_id)
        self.assertEqual(context.engagement_ids, [engagement.engagement_id])

    def test_includes_global_claim_with_no_mapping_at_all(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        global_claim = make_narrative_claim(rev, claim_type="skill", subject_scope="skill:python")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        context = retrieve_context(rev)
        self.assertEqual(context.claim_ids, [global_claim.claim_id])
        self.assertIsNone(context.claims[0].engagement_id)

    def test_excludes_claim_with_only_a_proposed_or_rejected_mapping(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev)
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.PROPOSED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        self.assertEqual(retrieve_context(rev).claims, [])

    def test_excludes_a_non_approved_engagement_from_context_even_if_mapped(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement(approval_status=CareerEngagement.ApprovalStatus.DRAFT)
        claim = make_narrative_claim(rev)
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        context = retrieve_context(rev)
        self.assertEqual(context.claims, [])
        self.assertEqual(context.engagements, [])

    def test_does_not_send_full_candidate_memory_only_the_bounded_subset(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        for i in range(3):
            make_narrative_claim(rev, canonical_text_en=f"Claim {i}", stable_key=f"k{i}")
        make_narrative_claim(
            rev, confirmation_status=MemoryClaim.ConfirmationStatus.UNCONFIRMED, stable_key="k-unconfirmed"
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        context = retrieve_context(rev)
        self.assertEqual(len(context.claims), 3)
