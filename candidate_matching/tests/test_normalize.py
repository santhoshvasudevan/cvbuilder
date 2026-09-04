from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from candidate_memory.models import CandidateMemory

from ..schemas import RequirementNormalizationItem
from ..services.candidate_generation import generate_candidates_for_requirement
from ..services.dedup import DedupedClaim, deduplicate_claims
from ..services.normalization_limits import MAX_DIAGNOSTIC_TERMS, MAX_TERM_CHARS, MAX_TEXT_CHARS
from ..services.normalize import (
    NormalizationFailedError,
    build_request,
    build_search_text,
    expand_requirements_for_search,
)
from .factories import freeze_revision, make_narrative_claim, make_revision, scripted_normalization


def _claim(claim_id, text, engagement_ids=()):
    return DedupedClaim(
        claim_id=claim_id,
        text=text,
        claim_type="responsibility",
        subject_scope="s",
        approved_engagement_ids=tuple(engagement_ids),
        grouped_claim_ids=(claim_id,),
    )


def _norm(**overrides):
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


class BuildRequestOnlyReceivesRequirementContentTests(SimpleTestCase):
    """Hard boundary (module docstring): AC_NORMALIZE must never see a MemoryClaim, a
    CareerEngagement, or any candidate/employment data -- only requirement_id/text and the shared
    posting language."""

    def test_request_contains_only_requirement_id_text_and_language(self):
        requirements = [{"requirement_id": "JR-001", "text": "Own the payments service."}]
        request = build_request(requirements, posting_language="en")
        user_message = request.messages[1]["content"]
        self.assertIn("JR-001", user_message)
        self.assertIn("Own the payments service.", user_message)
        self.assertIn("en", user_message)

    def test_extra_keys_on_a_requirement_dict_never_reach_the_prompt(self):
        """Even if a caller carelessly passed extra keys (an employer, a claim id, a candidate
        name) alongside requirement_id/text, `build_request` only ever reads those two keys --
        proving the stage cannot leak anything else even if a future caller regresses the
        boundary `bounded_retrieval.py` enforces today."""
        requirements = [
            {
                "requirement_id": "JR-001",
                "text": "Own the payments service.",
                "employer": "Globex Corporation",
                "candidate_name": "Jane Candidate",
                "supporting_memory_claim_ids": ["MC-1", "MC-2"],
            }
        ]
        request = build_request(requirements, posting_language="en")
        user_message = request.messages[1]["content"]
        self.assertNotIn("Globex", user_message)
        self.assertNotIn("Jane Candidate", user_message)
        self.assertNotIn("MC-1", user_message)


class ExpandRequirementsForSearchTests(TestCase):
    def test_scripted_normalization_returns_one_item_per_requirement_id(self):
        response = {
            "items": [
                {
                    "requirement_id": "JR-001",
                    "canonical_english_text": "Own an equivalent service end to end.",
                    "diagnostic_terms": ["payments"],
                    "equivalents": ["service ownership"],
                    "preserved_technical_terms": [],
                    "source_language": "en",
                }
            ]
        }
        with scripted_normalization(response):
            result = expand_requirements_for_search(
                [{"requirement_id": "JR-001", "text": "Own the payments service."}],
                posting_language="en",
            )
        self.assertEqual(result["JR-001"].canonical_english_text, "Own an equivalent service end to end.")
        self.assertEqual(result["JR-001"].diagnostic_terms, ["payments"])

    def test_no_requirements_short_circuits_with_no_adapter_call(self):
        result = expand_requirements_for_search([], posting_language="en")
        self.assertEqual(result, {})

    def test_provider_error_fails_closed(self):
        # Missing the required `items` key fails schema validation -> is_error.
        with scripted_normalization({}):
            with self.assertRaises(NormalizationFailedError):
                expand_requirements_for_search(
                    [{"requirement_id": "JR-001", "text": "x"}], posting_language="en"
                )

    def test_a_dropped_requirement_id_fails_closed_never_silently_degrades(self):
        response = {
            "items": [
                {
                    "requirement_id": "JR-001",
                    "canonical_english_text": "x",
                    "diagnostic_terms": [],
                    "equivalents": [],
                    "preserved_technical_terms": [],
                    "source_language": "en",
                }
            ]
        }
        with scripted_normalization(response):
            with self.assertRaises(NormalizationFailedError):
                expand_requirements_for_search(
                    [
                        {"requirement_id": "JR-001", "text": "x"},
                        {"requirement_id": "JR-002", "text": "y"},
                    ],
                    posting_language="en",
                )

    def test_a_renamed_requirement_id_fails_closed(self):
        """The stage must never substitute a different id for the one it was given -- a renamed
        id is indistinguishable from a dropped-plus-added id and must fail the same way."""
        response = {
            "items": [
                {
                    "requirement_id": "JR-999-RENAMED",
                    "canonical_english_text": "x",
                    "diagnostic_terms": [],
                    "equivalents": [],
                    "preserved_technical_terms": [],
                    "source_language": "en",
                }
            ]
        }
        with scripted_normalization(response):
            with self.assertRaises(NormalizationFailedError):
                expand_requirements_for_search(
                    [{"requirement_id": "JR-001", "text": "x"}], posting_language="en"
                )

    def test_an_added_unrequested_requirement_id_fails_closed(self):
        response = {
            "items": [
                {
                    "requirement_id": "JR-001",
                    "canonical_english_text": "x",
                    "diagnostic_terms": [],
                    "equivalents": [],
                    "preserved_technical_terms": [],
                    "source_language": "en",
                },
                {
                    "requirement_id": "JR-EXTRA",
                    "canonical_english_text": "y",
                    "diagnostic_terms": [],
                    "equivalents": [],
                    "preserved_technical_terms": [],
                    "source_language": "en",
                },
            ]
        }
        with scripted_normalization(response):
            with self.assertRaises(NormalizationFailedError):
                expand_requirements_for_search(
                    [{"requirement_id": "JR-001", "text": "x"}], posting_language="en"
                )

    def test_excessive_diagnostic_terms_is_a_schema_validation_failure(self):
        """More than `MAX_DIAGNOSTIC_TERMS` entries is malformed output -- rejected by
        `RequirementNormalizationItem`'s own field constraint (schema validation -> is_error ->
        `NormalizationFailedError`), never silently truncated to the first N."""
        response = {
            "items": [
                {
                    "requirement_id": "JR-001",
                    "canonical_english_text": "x",
                    "diagnostic_terms": [f"term-{i}" for i in range(MAX_DIAGNOSTIC_TERMS + 1)],
                    "equivalents": [],
                    "preserved_technical_terms": [],
                    "source_language": "en",
                }
            ]
        }
        with scripted_normalization(response):
            with self.assertRaises(NormalizationFailedError):
                expand_requirements_for_search(
                    [{"requirement_id": "JR-001", "text": "x"}], posting_language="en"
                )

    def test_oversized_canonical_text_is_a_schema_validation_failure(self):
        response = {
            "items": [
                {
                    "requirement_id": "JR-001",
                    "canonical_english_text": "x" * (MAX_TEXT_CHARS + 1),
                    "diagnostic_terms": [],
                    "equivalents": [],
                    "preserved_technical_terms": [],
                    "source_language": "en",
                }
            ]
        }
        with scripted_normalization(response):
            with self.assertRaises(NormalizationFailedError):
                expand_requirements_for_search(
                    [{"requirement_id": "JR-001", "text": "x"}], posting_language="en"
                )

    def test_oversized_individual_term_is_a_schema_validation_failure(self):
        response = {
            "items": [
                {
                    "requirement_id": "JR-001",
                    "canonical_english_text": "x",
                    "diagnostic_terms": ["y" * (MAX_TERM_CHARS + 1)],
                    "equivalents": [],
                    "preserved_technical_terms": [],
                    "source_language": "en",
                }
            ]
        }
        with scripted_normalization(response):
            with self.assertRaises(NormalizationFailedError):
                expand_requirements_for_search(
                    [{"requirement_id": "JR-001", "text": "x"}], posting_language="en"
                )

    def test_extra_field_on_an_item_is_a_schema_validation_failure(self):
        """`extra="forbid"` -- a provider response that adds any field beyond the contract
        (e.g. an invented `matched_claim_id`) is malformed, not tolerated."""
        response = {
            "items": [
                {
                    "requirement_id": "JR-001",
                    "canonical_english_text": "x",
                    "diagnostic_terms": [],
                    "equivalents": [],
                    "preserved_technical_terms": [],
                    "source_language": "en",
                    "matched_claim_id": "MC-1",
                }
            ]
        }
        with scripted_normalization(response):
            with self.assertRaises(NormalizationFailedError):
                expand_requirements_for_search(
                    [{"requirement_id": "JR-001", "text": "x"}], posting_language="en"
                )


class BuildSearchTextTests(SimpleTestCase):
    def test_union_includes_original_canonical_and_bounded_terms(self):
        norm = _norm(
            canonical_english_text="Canonical restatement of the meaning.",
            diagnostic_terms=["Kubernetes"],
            equivalents=["container orchestration"],
            preserved_technical_terms=["GKE"],
        )
        text = build_search_text("Original requirement text.", norm)
        for expected in [
            "Original requirement text.",
            "Canonical restatement of the meaning.",
            "Kubernetes",
            "container orchestration",
            "GKE",
        ]:
            self.assertIn(expected, text)

    def test_near_duplicate_canonical_text_is_not_repeated(self):
        """A canonical restatement that is the same content as the original (after whitespace/
        case normalization -- the same check `dedup.py` uses to decide two claims are the same
        content) is not concatenated a second time: repeating it adds no new vocabulary, only
        spurious sentence-boundary artifacts for the phrase-bonus scorer (2026-09-04 finding)."""
        original = "Product ownership for connected vehicle services"
        norm = _norm(canonical_english_text="  PRODUCT ownership for connected vehicle services  ")
        text = build_search_text(original, norm)
        self.assertEqual(text.count("ownership"), 1)

    def test_genuinely_different_canonical_text_is_included(self):
        original = "Erfahrung mit Kubernetes"
        norm = _norm(canonical_english_text="Experience with Kubernetes")
        text = build_search_text(original, norm)
        self.assertIn("Erfahrung", text)
        self.assertIn("Experience with Kubernetes", text)


class ExpansionNeverBecomesEvidenceTests(SimpleTestCase):
    """Retrieval hints must never be mistaken for resume-eligible evidence: `RequirementNormalizationItem`
    has no field that could carry a claim id, and `DedupedClaim` (what actually becomes evidence
    downstream) has no field populated from normalization output at all."""

    def test_normalization_item_has_no_claim_or_evidence_field(self):
        norm = _norm()
        field_names = set(norm.model_fields)
        for forbidden in ("claim_id", "supporting_memory_claim_ids", "supporting_engagement_ids", "evidence"):
            self.assertNotIn(forbidden, field_names)

    def test_deduped_claim_produced_by_candidate_generation_is_never_a_normalization_term(self):
        """`generate_candidates_for_requirement`'s search text is built from normalization output,
        but its *return value* is always a `DedupedClaim` drawn from the real claim pool -- a
        diagnostic term/equivalent can never itself become a "claim" simply by being part of the
        scored query."""
        claims = [_claim("MC-1", "Owned the payments service end to end.")]
        norm = _norm(diagnostic_terms=["totally-fabricated-term-xyz"])
        search_text = build_search_text("Own the payments service.", norm)
        candidates = generate_candidates_for_requirement(search_text, claims)
        self.assertEqual([c.claim_id for c in candidates], ["MC-1"])
        for candidate in candidates:
            self.assertNotEqual(candidate.text, "totally-fabricated-term-xyz")


class DeterministicSearchScoringTests(TestCase):
    """Real-corpus-shaped determinism check: identical normalized search text must always produce
    the identical candidate list and order, run after run."""

    def test_identical_normalized_input_produces_identical_candidates(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        make_narrative_claim(rev, canonical_text_en="Operated Kubernetes clusters for automotive platforms.")
        make_narrative_claim(rev, canonical_text_en="Organized team lunches.")
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        from ..services.retrieve import retrieve_eligible_pool

        eligible = retrieve_eligible_pool(rev)
        deduped = deduplicate_claims(eligible.claims)

        norm = _norm(
            canonical_english_text="Kubernetes for automotive platforms.",
            diagnostic_terms=["Kubernetes"],
        )
        search_text = build_search_text("Kubernetes automotive platform experience", norm)

        run1 = [c.claim_id for c in generate_candidates_for_requirement(search_text, deduped)]
        run2 = [c.claim_id for c in generate_candidates_for_requirement(search_text, deduped)]
        self.assertEqual(run1, run2)


class ParaphraseAndCrossLanguageBridgeTests(SimpleTestCase):
    """Synthetic, non-real-corpus fixtures proving the *mechanism* bridges a genuine vocabulary
    gap -- independent of whether any specific real claim in the production CandidateMemory
    happens to be reachable within the existing per-requirement cap."""

    def test_paraphrase_with_zero_initial_token_overlap_is_reached_via_normalization(self):
        requirement_text = "Own the checkout experience end to end"
        claim = _claim("MC-1", "Directed a cross-functional squad delivering the purchase flow.")
        # No shared vocabulary at all between requirement and claim text.
        norm = _norm(
            canonical_english_text="Lead the purchase flow end to end.",
            diagnostic_terms=["purchase flow", "checkout"],
            equivalents=["cross-functional squad", "directed a team"],
        )
        search_text = build_search_text(requirement_text, norm)
        candidates = generate_candidates_for_requirement(
            search_text, [claim, _claim("MC-2", "Completely unrelated content about lunch.")]
        )
        self.assertEqual(candidates[0].claim_id, "MC-1")

    def test_german_requirement_reaches_english_evidence_via_canonical_translation(self):
        from ..services.lexical_relevance import (
            build_corpus_stats,
            normalize_terms,
            score_relevance,
            tokenize_ordered,
        )

        # "Fahrzeugvalidierung" (vehicle validation) has no English loanword overlap at all --
        # unlike a term such as "Cloud", which German technical writing commonly borrows as-is.
        requirement_text = "Erfahrung mit Fahrzeugvalidierung und automatisierten Testablaeufen"
        claim_text = "Performed vehicle validation testing using automated test execution methods."
        other_claim_text = "Organized team lunches and social events for the office."
        norm = _norm(
            canonical_english_text="Experience with vehicle validation and automated test procedures.",
            diagnostic_terms=["vehicle validation", "automated testing"],
            source_language="de",
        )
        bridged_text = build_search_text(requirement_text, norm)

        stats = build_corpus_stats(
            [tokenize_ordered(claim_text), tokenize_ordered(other_claim_text)]
        )
        claim_terms = tokenize_ordered(claim_text)
        tf: dict[str, int] = {}
        for term in claim_terms:
            tf[term] = tf.get(term, 0) + 1

        no_bridge_terms = normalize_terms(tokenize_ordered(requirement_text), stats)
        bridged_terms = normalize_terms(tokenize_ordered(bridged_text), stats)
        no_bridge_score = score_relevance(
            requirement_text, no_bridge_terms, claim_text, claim_terms, tf, stats
        )
        bridged_score = score_relevance(bridged_text, bridged_terms, claim_text, claim_terms, tf, stats)

        # The German-only original text shares no vocabulary at all with the English claim; the
        # canonical English bridge plus diagnostic terms scores substantially higher.
        self.assertEqual(no_bridge_score, 0.0)
        self.assertGreater(bridged_score, 0.0)

    def test_acronym_and_technical_proper_noun_preserved_verbatim_boosts_the_right_claim(self):
        requirement_text = "Requires ADAS validation expertise"
        adas_claim = _claim("MC-1", "Validated ADAS features including adaptive cruise control.")
        generic_claim = _claim("MC-2", "Validated general vehicle software quality.")
        norm = _norm(canonical_english_text=requirement_text, preserved_technical_terms=["ADAS"])
        search_text = build_search_text(requirement_text, norm)
        candidates = generate_candidates_for_requirement(search_text, [adas_claim, generic_claim])
        self.assertEqual(candidates[0].claim_id, "MC-1")
