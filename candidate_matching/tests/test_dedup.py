from __future__ import annotations

from django.test import SimpleTestCase

from ..services.dedup import deduplicate_claims
from ..services.retrieve import RetrievedClaim


def _claim(claim_id, text, duplicate_group_key="", engagement_ids=()):
    return RetrievedClaim(
        claim_id=claim_id, text=text, claim_type="responsibility", subject_scope="s",
        approved_engagement_ids=tuple(engagement_ids), duplicate_group_key=duplicate_group_key,
    )


class DeduplicateClaimsTests(SimpleTestCase):
    def test_distinct_claims_are_never_merged(self):
        claims = [_claim("MC-1", "Did A."), _claim("MC-2", "Did B.")]
        deduped = deduplicate_claims(claims)
        self.assertEqual(len(deduped), 2)

    def test_identical_normalized_text_is_merged(self):
        claims = [_claim("MC-2", "  Did   A.  "), _claim("MC-1", "did a.")]
        deduped = deduplicate_claims(claims)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].claim_id, "MC-1")  # smallest claim_id wins, deterministic
        self.assertEqual(deduped[0].grouped_claim_ids, ("MC-1", "MC-2"))

    def test_explicit_duplicate_group_key_merges_even_with_different_text(self):
        claims = [
            _claim("MC-2", "Different wording of the same fact.", duplicate_group_key="g1"),
            _claim("MC-1", "Same fact, worded differently.", duplicate_group_key="g1"),
        ]
        deduped = deduplicate_claims(claims)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].claim_id, "MC-1")

    def test_similar_but_not_identical_text_is_never_merged_no_fuzzy_matching(self):
        claims = [_claim("MC-1", "Built the payments service."), _claim("MC-2", "Built the payment service.")]
        deduped = deduplicate_claims(claims)
        self.assertEqual(len(deduped), 2)

    def test_engagement_ids_are_unioned_across_a_merged_group(self):
        claims = [
            _claim("MC-1", "Same fact.", duplicate_group_key="g1", engagement_ids=["CE-0001"]),
            _claim("MC-2", "Same fact.", duplicate_group_key="g1", engagement_ids=["CE-0002"]),
        ]
        deduped = deduplicate_claims(claims)
        self.assertEqual(deduped[0].approved_engagement_ids, ("CE-0001", "CE-0002"))

    def test_ordering_is_deterministic_regardless_of_input_order(self):
        claims_a = [_claim("MC-3", "C"), _claim("MC-1", "A"), _claim("MC-2", "B")]
        claims_b = [_claim("MC-1", "A"), _claim("MC-2", "B"), _claim("MC-3", "C")]
        self.assertEqual(
            [c.claim_id for c in deduplicate_claims(claims_a)],
            [c.claim_id for c in deduplicate_claims(claims_b)],
        )

    def test_empty_input_produces_empty_output(self):
        self.assertEqual(deduplicate_claims([]), [])
