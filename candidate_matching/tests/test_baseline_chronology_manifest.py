"""D-037 unit tests for the manifest schema itself (`services/baseline_chronology.py`):
`build_baseline_manifest`/`build_manifest_for_job_relevant_claim_ids`, `validate_manifest`, and
`reconstruct_retrieved_claims` -- independent of `resume_builder.services.context.
build_builder_context`, which is covered separately in `resume_builder/tests/test_context.py`.
"""

from __future__ import annotations

from django.test import TestCase

from candidate_memory.models import CandidateMemory, ClaimEngagementMapping, MemoryClaim

from ..services.baseline_chronology import (
    InvalidBaselineManifestError,
    build_manifest_for_job_relevant_claim_ids,
    reconstruct_retrieved_claims,
    validate_manifest,
)
from .factories import freeze_revision, make_engagement, make_narrative_claim, make_revision


class ValidateManifestTests(TestCase):
    def setUp(self):
        self.rev = make_revision(status=CandidateMemory.Status.BUILDING)
        self.engagement = make_engagement()
        self.claim = make_narrative_claim(self.rev, canonical_text_en="x")
        ClaimEngagementMapping.objects.create(
            memory_claim=self.claim, career_engagement=self.engagement,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        freeze_revision(self.rev, CandidateMemory.Status.ACTIVE)
        self.manifest = build_manifest_for_job_relevant_claim_ids(
            self.rev, [self.engagement], [self.claim.claim_id]
        )

    def test_a_freshly_built_manifest_validates(self):
        validate_manifest(self.manifest, candidate_memory_id=self.rev.pk)

    def test_empty_manifest_fails_closed(self):
        with self.assertRaises(InvalidBaselineManifestError):
            validate_manifest({}, candidate_memory_id=self.rev.pk)

    def test_non_dict_manifest_fails_closed(self):
        with self.assertRaises(InvalidBaselineManifestError):
            validate_manifest(None, candidate_memory_id=self.rev.pk)

    def test_missing_required_key_fails_closed(self):
        del self.manifest["claim_inclusion_reasons"]
        with self.assertRaises(InvalidBaselineManifestError):
            validate_manifest(self.manifest, candidate_memory_id=self.rev.pk)

    def test_wrong_schema_version_fails_closed(self):
        self.manifest["schema_version"] = 999
        with self.assertRaises(InvalidBaselineManifestError):
            validate_manifest(self.manifest, candidate_memory_id=self.rev.pk)

    def test_candidate_memory_id_mismatch_fails_closed(self):
        other_rev = make_revision(status=CandidateMemory.Status.BUILDING)
        with self.assertRaises(InvalidBaselineManifestError):
            validate_manifest(self.manifest, candidate_memory_id=other_rev.pk)

    def test_no_evidence_engagement_not_in_roster_fails_closed(self):
        self.manifest["engagements_with_no_eligible_evidence"] = ["CE-9999"]
        with self.assertRaises(InvalidBaselineManifestError):
            validate_manifest(self.manifest, candidate_memory_id=self.rev.pk)

    def test_anchor_engagement_not_in_roster_fails_closed(self):
        self.manifest["anchor_claim_ids_by_engagement"]["CE-9999"] = [self.claim.claim_id]
        with self.assertRaises(InvalidBaselineManifestError):
            validate_manifest(self.manifest, candidate_memory_id=self.rev.pk)

    def test_mismatched_claim_dict_keys_fails_closed(self):
        self.manifest["claim_approved_engagement_ids"] = {}
        with self.assertRaises(InvalidBaselineManifestError):
            validate_manifest(self.manifest, candidate_memory_id=self.rev.pk)

    def test_unrecognized_inclusion_reason_fails_closed(self):
        self.manifest["claim_inclusion_reasons"][self.claim.claim_id] = ["NOT_A_REAL_REASON"]
        with self.assertRaises(InvalidBaselineManifestError):
            validate_manifest(self.manifest, candidate_memory_id=self.rev.pk)

    def test_empty_inclusion_reason_list_fails_closed(self):
        self.manifest["claim_inclusion_reasons"][self.claim.claim_id] = []
        with self.assertRaises(InvalidBaselineManifestError):
            validate_manifest(self.manifest, candidate_memory_id=self.rev.pk)

    def test_anchor_claim_id_not_listed_in_inclusion_reasons_fails_closed(self):
        self.manifest["anchor_claim_ids_by_engagement"][self.engagement.engagement_id].append("MC-9999-0001")
        with self.assertRaises(InvalidBaselineManifestError):
            validate_manifest(self.manifest, candidate_memory_id=self.rev.pk)


class ReconstructRetrievedClaimsTests(TestCase):
    def test_reconstructs_claim_text_type_and_scope_fresh(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        claim = make_narrative_claim(
            rev, canonical_text_en="Owned the payments service.", claim_type="responsibility",
            subject_scope="Acme Corp",
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        manifest = build_manifest_for_job_relevant_claim_ids(rev, [], [claim.claim_id])

        claims = reconstruct_retrieved_claims(manifest, rev.pk)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].claim_id, claim.claim_id)
        self.assertEqual(claims[0].text, "Owned the payments service.")
        self.assertEqual(claims[0].claim_type, "responsibility")
        self.assertEqual(claims[0].subject_scope, "Acme Corp")

    def test_claim_id_that_no_longer_resolves_fails_closed(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        claim = make_narrative_claim(rev, canonical_text_en="x")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        manifest = build_manifest_for_job_relevant_claim_ids(rev, [], [claim.claim_id])

        MemoryClaim.objects.filter(pk=claim.pk).update(resume_eligible=False)

        with self.assertRaises(InvalidBaselineManifestError):
            reconstruct_retrieved_claims(manifest, rev.pk)

    def test_empty_manifest_reconstructs_to_no_claims(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        manifest = build_manifest_for_job_relevant_claim_ids(rev, [], [])
        self.assertEqual(reconstruct_retrieved_claims(manifest, rev.pk), [])
