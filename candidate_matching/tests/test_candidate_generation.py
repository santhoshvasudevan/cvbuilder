from __future__ import annotations

from django.test import SimpleTestCase

from ..services.candidate_generation import (
    generate_candidates,
    generate_candidates_for_requirement,
    union_candidate_pool,
)
from ..services.dedup import DedupedClaim
from ..services.retrieval_limits import MAX_CANDIDATES_PER_REQUIREMENT, MIN_CANDIDATES_PER_REQUIREMENT


def _claim(claim_id, text):
    return DedupedClaim(
        claim_id=claim_id, text=text, claim_type="responsibility", subject_scope="s",
        approved_engagement_ids=(), grouped_claim_ids=(claim_id,),
    )


class GenerateCandidatesForRequirementTests(SimpleTestCase):
    def test_lexically_overlapping_claim_scores_above_a_non_overlapping_one(self):
        claims = [
            _claim("MC-1", "Designed and validated electronic control systems for airbags."),
            _claim("MC-2", "Ran the weekly team lunch schedule."),
        ] + [_claim(f"MC-pad-{i}", "Unrelated padding text about nothing in particular.") for i in range(15)]
        result = generate_candidates_for_requirement(
            "Experience validating electronic control systems", claims
        )
        self.assertIn("MC-1", [c.claim_id for c in result])
        self.assertLess(result.index(next(c for c in result if c.claim_id == "MC-1")), len(result))

    def test_never_returns_fewer_than_the_minimum_floor_even_with_zero_lexical_overlap(self):
        claims = [_claim(f"MC-{i}", f"Completely unrelated claim number {i}.") for i in range(30)]
        result = generate_candidates_for_requirement("Something entirely different", claims)
        self.assertGreaterEqual(len(result), min(MIN_CANDIDATES_PER_REQUIREMENT, len(claims)))

    def test_never_exceeds_the_per_requirement_cap(self):
        claims = [_claim(f"MC-{i}", "Python Django backend engineer service") for i in range(200)]
        result = generate_candidates_for_requirement("Python Django backend engineer service", claims)
        self.assertLessEqual(len(result), MAX_CANDIDATES_PER_REQUIREMENT)

    def test_deterministic_tie_breaking_by_claim_id(self):
        claims = [_claim("MC-2", "Python engineer"), _claim("MC-1", "Python engineer")]
        result1 = generate_candidates_for_requirement("Python engineer", claims)
        result2 = generate_candidates_for_requirement("Python engineer", list(reversed(claims)))
        self.assertEqual([c.claim_id for c in result1], [c.claim_id for c in result2])


class UnionCandidatePoolTests(SimpleTestCase):
    def test_union_deduplicates_across_requirements(self):
        shared = _claim("MC-1", "Python Django")
        groups = generate_candidates(
            [
                {"requirement_id": "JR-001", "text": "Python Django"},
                {"requirement_id": "JR-002", "text": "Python Django"},
            ],
            [shared] + [_claim(f"MC-pad-{i}", "padding") for i in range(15)],
        )
        pool = union_candidate_pool(groups)
        self.assertEqual(len(pool), len({c.claim_id for group in groups for c in group.candidates}))

    def test_union_is_sorted_by_claim_id(self):
        groups = generate_candidates(
            [{"requirement_id": "JR-001", "text": "x"}],
            [_claim("MC-3", "x"), _claim("MC-1", "x"), _claim("MC-2", "x")],
        )
        pool = union_candidate_pool(groups)
        self.assertEqual([c.claim_id for c in pool], sorted(c.claim_id for c in pool))
