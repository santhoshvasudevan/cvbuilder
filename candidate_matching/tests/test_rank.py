from __future__ import annotations

from django.test import TestCase

from ..services.dedup import DedupedClaim
from ..services.rank import build_request, rank_relevance
from .factories import scripted_ranking


def _claim(claim_id, text, engagement_ids=()):
    return DedupedClaim(
        claim_id=claim_id,
        text=text,
        claim_type="responsibility",
        subject_scope="s",
        approved_engagement_ids=tuple(engagement_ids),
        grouped_claim_ids=(claim_id,),
    )


class BuildRequestTests(TestCase):
    def test_prompt_contains_every_candidate_and_requirement(self):
        request = build_request(
            [_claim("MC-1", "Did the thing.")],
            [{"requirement_id": "JR-001", "text": "Do the thing."}],
        )
        user_message = request.messages[1]["content"]
        self.assertIn("MC-1", user_message)
        self.assertIn("Did the thing.", user_message)
        self.assertIn("JR-001", user_message)

    def test_engagement_note_lists_all_approved_engagements(self):
        request = build_request(
            [_claim("MC-1", "x", engagement_ids=["CE-0001", "CE-0002"])],
            [{"requirement_id": "JR-001", "text": "x"}],
        )
        self.assertIn("CE-0001, CE-0002", request.messages[1]["content"])


class RankRelevanceTests(TestCase):
    def test_no_requirements_short_circuits_with_no_adapter_call(self):
        result = rank_relevance([_claim("MC-1", "x")], [])
        self.assertFalse(result.is_error)
        self.assertEqual(result.content.rankings, [])

    def test_no_candidates_short_circuits_with_no_adapter_call(self):
        result = rank_relevance([], [{"requirement_id": "JR-001", "text": "x"}])
        self.assertFalse(result.is_error)
        self.assertEqual(result.content.rankings[0].relevant_claim_ids, [])

    def test_scripted_ranking_returns_the_scripted_response(self):
        response = {"rankings": [{"requirement_id": "JR-001", "relevant_claim_ids": ["MC-1"]}]}
        with scripted_ranking(response):
            result = rank_relevance([_claim("MC-1", "x")], [{"requirement_id": "JR-001", "text": "x"}])
        self.assertFalse(result.is_error)
        self.assertEqual(result.content.rankings[0].relevant_claim_ids, ["MC-1"])

    def test_malformed_ranking_response_is_a_schema_validation_error(self):
        response = {
            "rankings": [{"requirement_id": "JR-001", "relevant_claim_ids": ["MC-1"], "extra_field": "x"}]
        }
        with scripted_ranking(response):
            result = rank_relevance([_claim("MC-1", "x")], [{"requirement_id": "JR-001", "text": "x"}])
        self.assertTrue(result.is_error)
