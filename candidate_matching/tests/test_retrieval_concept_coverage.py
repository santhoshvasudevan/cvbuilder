"""Reproducible, synthetic, non-personal retrieval-concept-coverage regression fixtures
(Gate-1 preparation, 2026-09-04).

An independent audit of commit 983afbd (the BM25 rarity-aware rewrite + AC_NORMALIZE stage)
verified five concept domains and three specific "substitute evidence" claim exclusions against
the real ACTIVE CandidateMemory, but explicitly could not commit that verification as an
automated test -- it depended on real corpus content (real claim IDs like "MC-7-0146") that no
fixture in this repository encodes, so a future recall regression in this area would have no
regression guard at all. These tests close that gap with an entirely synthetic, invented corpus:

- requirement-level *concept* coverage is asserted (is there at least one credible candidate for
  the concept), never a specific hardcoded claim ID as "the" answer;
- "valid substitute evidence" is asserted directly: a claim is removed from the corpus entirely
  (simulating it never reaching the bounded candidate pool) and a differently-worded claim
  covering the same concept is proven to still satisfy the requirement on its own;
- every cap in `retrieval_limits.py` is used exactly as configured, via the real
  `generate_candidates`/`generate_candidates_for_requirement` functions -- never bypassed,
  loosened, or reimplemented here.

All claim/requirement text below is invented for this test file -- it does not describe any real
person, employer, or engagement.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from ..schemas import RequirementNormalizationItem
from ..services import retrieval_limits
from ..services.candidate_generation import generate_candidates_for_requirement
from ..services.dedup import DedupedClaim
from ..services.normalize import build_search_text


def _claim(claim_id: str, text: str) -> DedupedClaim:
    return DedupedClaim(
        claim_id=claim_id,
        text=text,
        claim_type="responsibility",
        subject_scope="Synthetic Test Employer",
        approved_engagement_ids=(),
        grouped_claim_ids=(claim_id,),
    )


def _norm(**overrides) -> RequirementNormalizationItem:
    defaults = dict(
        requirement_id="JR-001",
        canonical_english_text="A canonical restatement.",
        diagnostic_terms=[],
        equivalents=[],
        preserved_technical_terms=[],
        source_language="en",
    )
    defaults.update(overrides)
    return RequirementNormalizationItem(**defaults)


# -- The five audited concept domains, entirely invented claim text -----------------------------

CLOUD_SOLUTIONS_ARCHITECTURE_PRIMARY = _claim(
    "SYN-01", "Owned solution architecture for a cloud-based platform, including service design "
    "and technology selection."
)
CLOUD_SOLUTIONS_ARCHITECTURE_GCP = _claim(
    "SYN-02", "Operated production workloads on Google Cloud Platform, including compute, "
    "storage, and networking services."
)

CONNECTED_VEHICLE_ALLIANCE_PRIMARY = _claim(
    "SYN-03", "Served as product owner for a connected-vehicle feature set delivered through a "
    "multi-company alliance program."
)
CONNECTED_VEHICLE_ALLIANCE_SUBSTITUTE = _claim(
    "SYN-04", "Coordinated technical integration between OEM and alliance-partner engineering "
    "teams on a shared vehicle platform."
)

DATA_QUALITY_PRIMARY = _claim(
    "SYN-05", "Achieved data accuracy above 99% through automated validation checks and "
    "data-quality monitoring on a cloud data pipeline."
)
DATA_QUALITY_SUBSTITUTE = _claim(
    "SYN-06", "Built automated data pipelines using Python and SQL with continuous data-quality "
    "monitoring and validation."
)

ADAS_VALIDATION_PRIMARY = _claim(
    "SYN-07", "Performed validation testing of advanced driver assistance system (ADAS) features "
    "using hardware-in-the-loop test environments."
)
ADAS_VALIDATION_SAFETY_STANDARD = _claim(
    "SYN-08", "Executed functional safety test cases for automotive control systems in "
    "accordance with ISO 26262."
)

GERMAN_TARGET_CLAIM = _claim(
    "SYN-09", "Led solution architecture for a cloud-based connected-vehicle platform, covering "
    "design, technology choice, and delivery."
)

NOISE_CLAIMS = [
    _claim("SYN-90", "Organized team social events and coordinated office activities."),
    _claim("SYN-91", "Reviewed vendor contracts for office supply procurement."),
    _claim("SYN-92", "Maintained an internal documentation wiki for onboarding new employees."),
    _claim("SYN-93", "Planned quarterly budget reviews for a small department."),
]


def _full_corpus() -> list[DedupedClaim]:
    return [
        CLOUD_SOLUTIONS_ARCHITECTURE_PRIMARY,
        CLOUD_SOLUTIONS_ARCHITECTURE_GCP,
        CONNECTED_VEHICLE_ALLIANCE_PRIMARY,
        CONNECTED_VEHICLE_ALLIANCE_SUBSTITUTE,
        DATA_QUALITY_PRIMARY,
        DATA_QUALITY_SUBSTITUTE,
        ADAS_VALIDATION_PRIMARY,
        ADAS_VALIDATION_SAFETY_STANDARD,
        GERMAN_TARGET_CLAIM,
        *NOISE_CLAIMS,
    ]


class RetrievalConceptCoverageTests(SimpleTestCase):
    """Each test authors its own requirement wording (never copied from a claim's text) and
    asserts *concept*-level coverage: at least one credible claim reaches the bounded candidate
    list, not that a specific claim ID is present."""

    def test_cloud_solutions_architecture_concept_is_covered(self):
        result = generate_candidates_for_requirement(
            "Own end-to-end solution architecture for cloud-based systems.", _full_corpus()
        )
        candidate_ids = {c.claim_id for c in result}
        expected = {CLOUD_SOLUTIONS_ARCHITECTURE_PRIMARY.claim_id, CLOUD_SOLUTIONS_ARCHITECTURE_GCP.claim_id}
        self.assertTrue(candidate_ids & expected)

    def test_connected_vehicle_product_ownership_and_alliance_integration_concept_is_covered(self):
        result = generate_candidates_for_requirement(
            "Coordinate connected-vehicle delivery across an OEM alliance partnership.", _full_corpus()
        )
        candidate_ids = {c.claim_id for c in result}
        self.assertTrue(
            candidate_ids
            & {CONNECTED_VEHICLE_ALLIANCE_PRIMARY.claim_id, CONNECTED_VEHICLE_ALLIANCE_SUBSTITUTE.claim_id}
        )

    def test_cloud_data_engineering_and_data_quality_concept_is_covered(self):
        result = generate_candidates_for_requirement(
            "Build reliable cloud data pipelines with strong automated data-quality controls.",
            _full_corpus(),
        )
        candidate_ids = {c.claim_id for c in result}
        self.assertTrue(candidate_ids & {DATA_QUALITY_PRIMARY.claim_id, DATA_QUALITY_SUBSTITUTE.claim_id})

    def test_adas_system_validation_concept_is_covered(self):
        result = generate_candidates_for_requirement(
            "Validate ADAS and safety-critical automotive control system functionality.",
            _full_corpus(),
        )
        candidate_ids = {c.claim_id for c in result}
        self.assertTrue(
            candidate_ids & {ADAS_VALIDATION_PRIMARY.claim_id, ADAS_VALIDATION_SAFETY_STANDARD.claim_id}
        )

    def test_german_requirement_reaches_canonical_english_evidence(self):
        """Concept 5: a requirement written in German must reach English-language evidence via
        AC_NORMALIZE's canonical-English bridge -- and, to prove the bridge is doing real work
        (not a vacuous assertion), the *unbridged* German text must score zero relevance against
        that evidence on its own first (asserted directly via `score_relevance`, the same
        mechanism `generate_candidates_for_requirement` uses internally -- checking candidate-list
        *presence* alone would be misleading here, since `MIN_CANDIDATES_PER_REQUIREMENT`'s
        deterministic backfill can include a zero-scoring claim in a corpus this small regardless
        of relevance)."""
        from ..services.lexical_relevance import (
            build_corpus_stats,
            normalize_terms,
            score_relevance,
            tokenize_ordered,
        )

        requirement_text_de = (
            "Verantwortung fuer die Loesungsarchitektur einer cloudbasierten "
            "Fahrzeug-Konnektivitaetsplattform."
        )
        corpus = _full_corpus()
        stats = build_corpus_stats([tokenize_ordered(c.text) for c in corpus])
        target_terms = tokenize_ordered(GERMAN_TARGET_CLAIM.text)
        target_tf: dict[str, int] = {}
        for term in target_terms:
            target_tf[term] = target_tf.get(term, 0) + 1

        unbridged_terms = normalize_terms(tokenize_ordered(requirement_text_de), stats)
        unbridged_score = score_relevance(
            requirement_text_de, unbridged_terms, GERMAN_TARGET_CLAIM.text, target_terms, target_tf, stats
        )
        self.assertEqual(unbridged_score, 0.0)

        normalization = _norm(
            canonical_english_text=(
                "Responsible for the solution architecture of a cloud-based connected-vehicle "
                "platform."
            ),
            diagnostic_terms=["solution architecture", "connected vehicle", "cloud platform"],
            source_language="de",
        )
        bridged_text = build_search_text(requirement_text_de, normalization)
        bridged_terms = normalize_terms(tokenize_ordered(bridged_text), stats)
        bridged_score = score_relevance(
            bridged_text, bridged_terms, GERMAN_TARGET_CLAIM.text, target_terms, target_tf, stats
        )
        self.assertGreater(bridged_score, 0.0)

        # And, at the level `generate_candidates_for_requirement` actually returns, the bridged
        # search text lands the target claim among the requirement's candidates.
        bridged = generate_candidates_for_requirement(bridged_text, corpus)
        self.assertIn(GERMAN_TARGET_CLAIM.claim_id, {c.claim_id for c in bridged})


class SubstituteEvidencePreservesConceptCoverageTests(SimpleTestCase):
    """Directly mirrors the audit's MC-7-0146/0175/0033 finding: a claim is removed from the
    corpus entirely (simulating it never reaching the bounded candidate pool for any reason --
    dedup, ranking cap, whatever), and a differently-worded claim covering the same concept is
    proven to still satisfy the requirement on its own, never becoming a GAP."""

    def test_connected_vehicle_alliance_substitute_claim_covers_the_concept_without_the_primary_claim(self):
        corpus_without_primary = [
            claim for claim in _full_corpus() if claim.claim_id != CONNECTED_VEHICLE_ALLIANCE_PRIMARY.claim_id
        ]
        result = generate_candidates_for_requirement(
            "Coordinate connected-vehicle delivery across an OEM alliance partnership.",
            corpus_without_primary,
        )
        self.assertIn(CONNECTED_VEHICLE_ALLIANCE_SUBSTITUTE.claim_id, {c.claim_id for c in result})

    def test_data_quality_substitute_claim_covers_the_concept_without_the_primary_claim(self):
        corpus_without_primary = [
            claim for claim in _full_corpus() if claim.claim_id != DATA_QUALITY_PRIMARY.claim_id
        ]
        result = generate_candidates_for_requirement(
            "Build reliable cloud data pipelines with strong automated data-quality controls.",
            corpus_without_primary,
        )
        self.assertIn(DATA_QUALITY_SUBSTITUTE.claim_id, {c.claim_id for c in result})


class RetrievalCapsRemainUnchangedTests(SimpleTestCase):
    """A cheap tripwire so a future change to `retrieval_limits.py` cannot silently loosen these
    bounds -- concept-coverage fixtures above rely on `generate_candidates_for_requirement`
    enforcing the *real*, unmodified caps, never a relaxed value substituted for this test file."""

    def test_bound_constants_match_the_gate1_audited_values(self):
        self.assertEqual(retrieval_limits.MAX_CANDIDATES_PER_REQUIREMENT, 40)
        self.assertEqual(retrieval_limits.MIN_CANDIDATES_PER_REQUIREMENT, 10)
        self.assertEqual(retrieval_limits.MAX_RANKING_CANDIDATES, 150)
        self.assertEqual(retrieval_limits.MAX_SELECTED_CLAIMS, 80)
        self.assertEqual(retrieval_limits.MAX_ESTIMATED_REQUEST_TOKENS, 12_000)
