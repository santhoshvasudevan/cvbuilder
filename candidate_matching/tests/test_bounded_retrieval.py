from __future__ import annotations

from django.test import TestCase

from candidate_memory.models import CandidateMemory, ClaimEngagementMapping

from ..services.bounded_retrieval import RankingFailedError, build_bounded_context
from ..services.normalize import NormalizationFailedError
from ..services.retrieval_limits import (
    MAX_ESTIMATED_REQUEST_TOKENS,
    MAX_RANKING_CANDIDATES,
    MAX_SELECTED_CLAIMS,
)
from .factories import (
    freeze_revision,
    make_engagement,
    make_narrative_claim,
    make_revision,
    scripted_normalization,
    scripted_ranking,
    scripted_ranking_selecting_all,
)


class BuildBoundedContextTests(TestCase):
    def test_no_requirements_makes_no_ranking_call_and_selects_nothing(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        make_narrative_claim(rev)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        context, manifest = build_bounded_context(rev, [])
        self.assertEqual(context.claims, [])
        self.assertEqual(manifest.candidate_pool_count, 0)
        self.assertEqual(manifest.selected_count, 0)

    def test_ranking_selects_only_relevant_claims_per_requirement(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        relevant = make_narrative_claim(rev, canonical_text_en="Validated electronic control systems.")
        irrelevant = make_narrative_claim(rev, canonical_text_en="Organized team lunches.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        response = {
            "rankings": [
                {"requirement_id": "JR-001", "relevant_claim_ids": [relevant.claim_id]},
            ]
        }
        with scripted_ranking(response):
            context, manifest = build_bounded_context(
                rev, [{"requirement_id": "JR-001", "text": "Validate electronic control systems"}]
            )

        self.assertEqual(context.claim_ids, [relevant.claim_id])
        self.assertNotIn(irrelevant.claim_id, context.claim_ids)
        self.assertEqual(manifest.per_requirement_selected_claim_ids["JR-001"], [relevant.claim_id])

    def test_a_requirement_with_no_relevant_evidence_gets_an_explicit_empty_result(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        make_narrative_claim(rev, canonical_text_en="Something unrelated.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        response = {"rankings": [{"requirement_id": "JR-001", "relevant_claim_ids": []}]}
        with scripted_ranking(response):
            context, manifest = build_bounded_context(
                rev, [{"requirement_id": "JR-001", "text": "Kubernetes orchestration"}]
            )

        self.assertEqual(context.claims, [])
        self.assertEqual(manifest.per_requirement_selected_claim_ids["JR-001"], [])

    def test_ranking_result_missing_a_requirement_is_treated_as_explicit_empty(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        make_narrative_claim(rev, canonical_text_en="Something.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        with scripted_ranking({"rankings": []}):
            context, manifest = build_bounded_context(
                rev, [{"requirement_id": "JR-001", "text": "Something"}]
            )

        self.assertEqual(context.claims, [])
        self.assertEqual(manifest.excluded_counts.get("ranking_missing_requirement"), 1)

    def test_fabricated_ranking_claim_id_is_discarded_not_trusted(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        real = make_narrative_claim(rev, canonical_text_en="Real claim about Kubernetes.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        response = {
            "rankings": [
                {"requirement_id": "JR-001", "relevant_claim_ids": [real.claim_id, "MC-fabricated-9999"]}
            ]
        }
        with scripted_ranking(response):
            context, manifest = build_bounded_context(
                rev, [{"requirement_id": "JR-001", "text": "Kubernetes"}]
            )

        self.assertEqual(context.claim_ids, [real.claim_id])
        self.assertEqual(manifest.excluded_counts.get("ranking_fabricated_ids"), 1)

    def test_ranking_provider_error_fails_closed_never_falls_back_to_full_pool(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        for i in range(5):
            make_narrative_claim(rev, canonical_text_en=f"Claim {i} about Kubernetes.", stable_key=f"k{i}")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        # A response missing the required `rankings` key fails schema validation -> is_error.
        with scripted_ranking({}):
            with self.assertRaises(RankingFailedError):
                build_bounded_context(rev, [{"requirement_id": "JR-001", "text": "Kubernetes"}])

    def test_engagement_mapped_claim_is_tagged_with_its_engagement_after_selection(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service.")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        with scripted_ranking_selecting_all():
            context, _manifest = build_bounded_context(
                rev, [{"requirement_id": "JR-001", "text": "Owned the payments service."}]
            )

        self.assertEqual(context.claims[0].approved_engagement_ids, (engagement.engagement_id,))

    def test_normalization_failure_fails_closed_never_falls_back_to_unexpanded_retrieval(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        make_narrative_claim(rev, canonical_text_en="Something about Kubernetes.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        # A response missing the required `items` key fails schema validation -> is_error.
        with scripted_normalization({}):
            with self.assertRaises(NormalizationFailedError):
                build_bounded_context(rev, [{"requirement_id": "JR-001", "text": "Kubernetes"}])

    def test_manifest_records_the_normalization_used_for_each_requirement(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        make_narrative_claim(rev, canonical_text_en="Operated Kubernetes clusters.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        normalization_response = {
            "items": [
                {
                    "requirement_id": "JR-001",
                    "canonical_english_text": "Kubernetes orchestration experience.",
                    "diagnostic_terms": ["Kubernetes"],
                    "equivalents": [],
                    "preserved_technical_terms": ["Kubernetes"],
                    "source_language": "en",
                }
            ]
        }

        from unittest import mock

        from llm_provider.types import NormalizedLLMResult

        from ..schemas import RelevanceRankingOutput

        def _select_all(
            candidate_pool,
            requirements,
            *,
            requested_model_id=None,
            requested_reasoning_effort=None,
            correlation_id=None,
        ):
            return NormalizedLLMResult(
                content=RelevanceRankingOutput(
                    rankings=[
                        {
                            "requirement_id": requirement["requirement_id"],
                            "relevant_claim_ids": [claim.claim_id for claim in candidate_pool],
                        }
                        for requirement in requirements
                    ]
                )
            )

        with scripted_normalization(normalization_response):
            with mock.patch("candidate_matching.services.bounded_retrieval.rank_relevance", _select_all):
                _context, manifest = build_bounded_context(
                    rev, [{"requirement_id": "JR-001", "text": "Kubernetes experience"}]
                )

        entry = manifest.requirement_normalization["JR-001"]
        self.assertEqual(entry["original_text"], "Kubernetes experience")
        self.assertEqual(entry["canonical_english_text"], "Kubernetes orchestration experience.")
        self.assertEqual(entry["diagnostic_terms"], ["Kubernetes"])
        self.assertEqual(entry["source_language"], "en")


class BoundedRetrievalAtScaleTests(TestCase):
    """Real-scale deterministic tests using a synthetic corpus comparable to the real activated
    revision 2 (~1,122 eligible narrative claims, ~359 rules), not just four-claim fixtures --
    proving the bound is real, not merely present in a tiny test."""

    def _build_large_corpus(self, num_claims=1200, num_rules=360):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        topics = [
            "Kubernetes orchestration and container platforms",
            "Python backend service development",
            "Electronic control systems validation",
            "Cloud infrastructure and CI/CD pipelines",
            "Team leadership and stakeholder communication",
        ]
        claims = []
        for i in range(num_claims):
            topic = topics[i % len(topics)]
            claim = make_narrative_claim(
                rev,
                canonical_text_en=f"{topic} -- distinct responsibility number {i}.",
                stable_key=f"scale-{i}",
            )
            claims.append(claim)
            if i % 7 == 0:
                ClaimEngagementMapping.objects.create(
                    memory_claim=claim,
                    career_engagement=engagement,
                    status=ClaimEngagementMapping.Status.APPROVED,
                )
        from candidate_memory.models import CandidateRule

        # Ratio matches the real activated CandidateMemory's own rule_type distribution
        # (2026-09-03: 285 POSITIONING, 65 PREFERENCE, 9 LEARNING_STATUS out of 359 total --
        # mandatory types are a small minority, not the majority a naive 2:1 split would suggest).
        for i in range(num_rules):
            if i % 40 == 0:
                rule_type = CandidateRule.RuleType.LEARNING_STATUS
            elif i % 5 == 0:
                rule_type = CandidateRule.RuleType.PREFERENCE
            else:
                rule_type = CandidateRule.RuleType.POSITIONING
            CandidateRule.objects.create(
                candidate_memory=rev,
                rule_type=rule_type,
                text=f"Positioning/preference guidance number {i}.",
            )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        return rev

    def test_candidate_pool_and_selection_stay_within_configured_caps_at_real_corpus_scale(self):
        rev = self._build_large_corpus()
        requirements = [
            {"requirement_id": f"JR-{n:03d}", "text": text}
            for n, text in enumerate(
                [
                    "Kubernetes orchestration experience required",
                    "Strong Python backend development skills",
                    "Electronic control systems validation background",
                    "Cloud infrastructure and CI/CD experience",
                    "Proven team leadership ability",
                ],
                start=1,
            )
        ]

        with scripted_ranking_selecting_all():
            context, manifest = build_bounded_context(rev, requirements)

        self.assertEqual(manifest.eligible_count, 1200)
        self.assertLessEqual(manifest.candidate_pool_count, MAX_RANKING_CANDIDATES)
        self.assertLessEqual(manifest.selected_count, MAX_SELECTED_CLAIMS)
        self.assertLessEqual(len(context.claims), MAX_SELECTED_CLAIMS)
        self.assertLessEqual(manifest.rules_selected_count, 30)
        self.assertLessEqual(manifest.estimated_request_tokens, MAX_ESTIMATED_REQUEST_TOKENS)
        # This is the exact scenario the pre-hardening audit measured at ~50,785 estimated tokens
        # for sending the *entire* 1,122-claim/359-rule pool -- confirm the bounded pipeline stays
        # dramatically smaller than that for a comparable corpus.
        self.assertLess(manifest.estimated_request_tokens, 10_000)

    def test_every_requirement_gets_a_real_selection_not_silently_empty(self):
        rev = self._build_large_corpus(num_claims=200, num_rules=30)
        requirements = [{"requirement_id": "JR-001", "text": "Kubernetes orchestration experience required"}]

        with scripted_ranking_selecting_all():
            _context, manifest = build_bounded_context(rev, requirements)

        self.assertGreater(len(manifest.per_requirement_selected_claim_ids["JR-001"]), 0)
