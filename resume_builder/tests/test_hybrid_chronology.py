"""D-035 hybrid-context correction tests (2026-09-06) -- synthetic Ford/Continental/Maruti/German
fixtures proving the deterministic baseline chronology reaches Agent Builder's context
independently of AC_RANK's job-relevance selection, while relevance still controls tailoring, all
existing eligibility/provenance/no-fabrication invariants stay intact, and the merged context
remains bounded and deterministic.

Every LLM call in this file is routed through the M2 `FakeAdapter` (via
`resume_builder.tests.factories.scripted_generation`) -- zero network, zero live credentials,
matching this project's existing M5/M6 test convention.
"""

from __future__ import annotations

from django.test import TestCase

from candidate_matching.models import FitAssessment
from candidate_matching.services.baseline_chronology import (
    MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT,
    build_baseline_chronology,
    build_manifest_for_job_relevant_claim_ids,
    compute_engagement_anchors,
)
from candidate_matching.services.retrieve import (
    RETRIEVAL_REASON_ENGAGEMENT_ANCHOR,
    RETRIEVAL_REASON_JOB_RELEVANT,
    RETRIEVAL_REASON_LANGUAGE_EVIDENCE,
)
from candidate_memory.models import CandidateMemory, ClaimEngagementMapping, MemoryClaim
from job_applications.models import JobApplication
from job_intake.models import JobRequirementAnalysis

from ..services.context import build_builder_context
from .factories import (
    freeze_revision,
    make_engagement,
    make_narrative_claim,
    make_revision,
    scripted_generation,
)


def _make_jra() -> JobRequirementAnalysis:
    application = JobApplication.objects.create()
    return JobRequirementAnalysis.objects.create(
        job_application=application, version=1,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="x" * 25, extracted_text="x" * 25,
        extracted_text_sha256="0" * 64, posting_language="en",
    )


class HybridChronologyFixtureMixin:
    """Synthetic (never real candidate) Ford/Continental/Maruti engagements + a confirmed German
    language claim + unmapped global evidence + one engagement with metadata but zero eligible
    anchor claims -- the exact shape D-035 diagnosed as reliably omittable under AC_RANK-only
    retrieval."""

    def _build_fixture(self, *, freeze: bool = True):
        self.rev = make_revision(status=CandidateMemory.Status.BUILDING)
        self.ford = make_engagement(
            legal_employer="Ambigai", client_organization="Ford Motor Company",
            approved_role_title="Senior Cloud Engineer",
        )
        self.continental = make_engagement(
            legal_employer="Ambigai", client_organization="Continental AG",
            approved_role_title="Cloud Engineer",
            start_year=2015, start_month=7,
            end_status="KNOWN", end_year=2017, end_month=10,
        )
        self.maruti = make_engagement(
            legal_employer="Maruti Suzuki India", client_organization="",
            approved_role_title="Software Engineer",
            start_year=2012, start_month=8,
            end_status="KNOWN", end_year=2015, end_month=6,
        )
        self.no_evidence_engagement = make_engagement(
            legal_employer="Globex Corporation", client_organization="",
            approved_role_title="Contract Engineer",
            start_year=2011, start_month=1, end_status="KNOWN", end_year=2012, end_month=7,
        )

        self.ford_claim = make_narrative_claim(
            self.rev, canonical_text_en="Owned the Ford telematics integration end to end.",
            stable_key="ford-1",
        )
        self.continental_claim = make_narrative_claim(
            self.rev, canonical_text_en="Delivered Continental's test-automation architecture.",
            stable_key="continental-1",
        )
        self.maruti_claim = make_narrative_claim(
            self.rev, canonical_text_en="Built Maruti's diagnostics tooling.",
            stable_key="maruti-1",
        )
        self.german_claim = make_narrative_claim(
            self.rev, claim_type="language_proficiency", subject_scope="language:german",
            canonical_text_en="German language proficiency: B1 confirmed.", stable_key="german-1",
        )
        self.global_claim = make_narrative_claim(
            self.rev, canonical_text_en="Proficient in Python and Kubernetes.", stable_key="global-1",
        )

        ClaimEngagementMapping.objects.create(
            memory_claim=self.ford_claim, career_engagement=self.ford,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        ClaimEngagementMapping.objects.create(
            memory_claim=self.continental_claim, career_engagement=self.continental,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        ClaimEngagementMapping.objects.create(
            memory_claim=self.maruti_claim, career_engagement=self.maruti,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        # german_claim and global_claim are deliberately left unmapped -- global, per D-019/D-035.

        self.approved_engagements = [self.ford, self.continental, self.maruti, self.no_evidence_engagement]

        if freeze:
            freeze_revision(self.rev, CandidateMemory.Status.ACTIVE)

        self.jra = _make_jra()
        # AC_RANK's own real result for this posting selected only Ford -- Continental, Maruti,
        # and the German claim reached the candidate pool but were never selected (the exact D-035
        # diagnosis for the real JobApplication 9 / FitAssessment 9). retrieved_engagement_ids is
        # deliberately narrowed to Ford alone too, so these tests also prove the baseline
        # chronology never depends on FitAssessment's own stored engagement-id list.
        #
        # D-037: the manifest is built here, once, exactly like the corrected M5 boundary
        # (`candidate_matching.services.fit_assessment.build_fit_assessment`) would -- against
        # `self.approved_engagements` as they stand at this exact moment (only meaningful once the
        # revision is frozen ACTIVE, since a manifest is only ever built at real FitAssessment
        # creation time against the then-ACTIVE revision).
        if freeze:
            manifest = build_manifest_for_job_relevant_claim_ids(
                self.rev, self.approved_engagements, [self.ford_claim.claim_id]
            )
            self.fit_assessment = FitAssessment(
                based_on_jra=self.jra,
                based_on_candidate_memory=self.rev,
                retrieved_claim_ids=[self.ford_claim.claim_id],
                retrieved_engagement_ids=[self.ford.engagement_id],
                baseline_chronology_manifest=manifest,
            )


class BaselineChronologyCompletenessTests(HybridChronologyFixtureMixin, TestCase):
    def setUp(self):
        self._build_fixture()
        self.context = build_builder_context(self.fit_assessment)

    def test_every_approved_engagement_reaches_the_baseline_context(self):
        self.assertEqual(
            set(self.context.engagement_ids),
            {
                self.ford.engagement_id, self.continental.engagement_id,
                self.maruti.engagement_id, self.no_evidence_engagement.engagement_id,
            },
        )

    def test_continental_and_maruti_claims_do_not_depend_on_ac_rank_selection(self):
        claim_ids = set(self.context.claim_ids)
        self.assertIn(self.continental_claim.claim_id, claim_ids)
        self.assertIn(self.maruti_claim.claim_id, claim_ids)

        by_id = {c.claim_id: c for c in self.context.claims}
        self.assertEqual(
            by_id[self.continental_claim.claim_id].retrieval_reasons,
            (RETRIEVAL_REASON_ENGAGEMENT_ANCHOR,),
        )
        self.assertEqual(
            by_id[self.maruti_claim.claim_id].retrieval_reasons,
            (RETRIEVAL_REASON_ENGAGEMENT_ANCHOR,),
        )

    def test_relevance_evidence_still_controls_tailoring(self):
        by_id = {c.claim_id: c for c in self.context.claims}
        ford_reasons = by_id[self.ford_claim.claim_id].retrieval_reasons
        self.assertIn(RETRIEVAL_REASON_JOB_RELEVANT, ford_reasons)
        # Ford's own claim is also its deterministic anchor -- both reasons legitimately apply to
        # the same claim_id, never duplicated as two separate context entries.
        self.assertIn(RETRIEVAL_REASON_ENGAGEMENT_ANCHOR, ford_reasons)
        self.assertEqual(len([c for c in self.context.claims if c.claim_id == self.ford_claim.claim_id]), 1)

    def test_language_evidence_reaches_the_language_context_unconditionally(self):
        by_id = {c.claim_id: c for c in self.context.claims}
        german = by_id[self.german_claim.claim_id]
        self.assertEqual(german.retrieval_reasons, (RETRIEVAL_REASON_LANGUAGE_EVIDENCE,))
        self.assertEqual(german.approved_engagement_ids, ())

    def test_unmapped_global_evidence_is_never_pulled_in_by_the_baseline(self):
        # Not selected by AC_RANK, not a language claim, and never engagement-mapped -- the
        # baseline correction must never widen retrieval into "every eligible global claim".
        self.assertNotIn(self.global_claim.claim_id, self.context.claim_ids)

    def test_global_claims_are_never_misattributed_to_an_engagement(self):
        by_id = {c.claim_id: c for c in self.context.claims}
        self.assertEqual(
            by_id[self.continental_claim.claim_id].approved_engagement_ids,
            (self.continental.engagement_id,),
        )
        self.assertEqual(
            by_id[self.maruti_claim.claim_id].approved_engagement_ids, (self.maruti.engagement_id,)
        )

    def test_engagement_with_no_eligible_anchor_claims_is_flagged_not_fabricated(self):
        self.assertIn(
            self.no_evidence_engagement.engagement_id, self.context.engagements_without_eligible_evidence
        )
        for claim in self.context.claims:
            self.assertNotIn(self.no_evidence_engagement.engagement_id, claim.approved_engagement_ids)

    def test_provenance_ids_resolve_to_real_confirmed_claims(self):
        for claim in self.context.claims:
            real = MemoryClaim.objects.get(claim_id=claim.claim_id)
            self.assertEqual(real.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)
            self.assertTrue(real.resume_eligible)
            self.assertEqual(real.canonical_text_en, claim.text)

    def test_context_size_is_bounded(self):
        # job-relevant (ford) + anchors (ford/continental/maruti, 1 each) + language (german) --
        # merged/deduped, never one row per source.
        self.assertLessEqual(len(self.context.claims), 4)

    def test_ordering_is_deterministic(self):
        first = build_builder_context(self.fit_assessment)
        second = build_builder_context(self.fit_assessment)
        self.assertEqual(first.claim_ids, second.claim_ids)
        self.assertEqual(first.engagement_ids, second.engagement_ids)
        self.assertEqual(first.claim_ids, sorted(first.claim_ids))
        self.assertEqual(first.engagement_ids, sorted(first.engagement_ids))


class AnchorSelectionBoundAndTieBreakTests(HybridChronologyFixtureMixin, TestCase):
    def setUp(self):
        # Unfrozen -- these tests add further claims to the same revision before freezing it
        # themselves, since a revision's content is only mutable while BUILDING/NEEDS_REVIEW.
        self._build_fixture(freeze=False)

    def test_anchor_selection_is_capped_per_engagement(self):
        extra_claims = []
        for i in range(MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT + 3):
            claim = make_narrative_claim(
                self.rev, canonical_text_en=f"Extra Ford responsibility {i}.", stable_key=f"ford-extra-{i}",
                experience_level=MemoryClaim.ExperienceLevel.PROFESSIONAL_DELIVERY,
            )
            ClaimEngagementMapping.objects.create(
                memory_claim=claim, career_engagement=self.ford,
                status=ClaimEngagementMapping.Status.APPROVED,
            )
            extra_claims.append(claim)
        freeze_revision(self.rev, CandidateMemory.Status.ACTIVE)

        anchors, no_evidence = compute_engagement_anchors(
            self.rev.pk, [self.ford, self.continental, self.maruti]
        )
        self.assertEqual(len(anchors[self.ford.engagement_id]), MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT)

    def test_anchor_tie_break_prefers_higher_experience_level_then_claim_id(self):
        low = make_narrative_claim(
            self.rev, canonical_text_en="Awareness-level Ford task.", stable_key="ford-low",
            experience_level=MemoryClaim.ExperienceLevel.AWARENESS,
        )
        mid = make_narrative_claim(
            self.rev, canonical_text_en="Delivery-level Ford task.", stable_key="ford-mid",
            experience_level=MemoryClaim.ExperienceLevel.PROFESSIONAL_DELIVERY,
        )
        high = make_narrative_claim(
            self.rev, canonical_text_en="Leadership-level Ford task.", stable_key="ford-high",
            experience_level=MemoryClaim.ExperienceLevel.LEADERSHIP,
        )
        for claim in (low, mid, high):
            ClaimEngagementMapping.objects.create(
                memory_claim=claim, career_engagement=self.ford,
                status=ClaimEngagementMapping.Status.APPROVED,
            )
        freeze_revision(self.rev, CandidateMemory.Status.ACTIVE)

        # Ford now has 4 eligible candidates (ford_claim plus low/mid/high) against a cap of
        # MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT=3 -- exactly one must be dropped. The original
        # ford_claim has no experience_level at all (the lowest-ranked, per `_anchor_sort_key`),
        # so it is the one excluded; every named experience_level claim, however it happens to be
        # ordered for presentation (claim_id ascending), survives the cut.
        anchors, _ = compute_engagement_anchors(self.rev.pk, [self.ford])
        ford_anchor_ids = {c.claim_id for c in anchors[self.ford.engagement_id]}
        self.assertEqual(ford_anchor_ids, {low.claim_id, mid.claim_id, high.claim_id})
        self.assertNotIn(self.ford_claim.claim_id, ford_anchor_ids)

    def test_no_fabrication_fallback_when_engagement_has_zero_eligible_claims(self):
        anchors, no_evidence = compute_engagement_anchors(self.rev.pk, [self.no_evidence_engagement])
        self.assertEqual(anchors[self.no_evidence_engagement.engagement_id], [])
        self.assertEqual(no_evidence, [self.no_evidence_engagement.engagement_id])

    def test_build_baseline_chronology_never_touches_a_different_revision(self):
        other_rev = make_revision(status=CandidateMemory.Status.BUILDING)
        other_claim = make_narrative_claim(other_rev, canonical_text_en="Other revision claim.")
        ClaimEngagementMapping.objects.create(
            memory_claim=other_claim, career_engagement=self.ford,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        freeze_revision(other_rev, CandidateMemory.Status.SUPERSEDED)
        freeze_revision(self.rev, CandidateMemory.Status.ACTIVE)

        baseline = build_baseline_chronology(self.rev.pk, [self.ford])
        anchor_ids = {c.claim_id for c in baseline.anchor_claims_by_engagement[self.ford.engagement_id]}
        self.assertNotIn(other_claim.claim_id, anchor_ids)
        self.assertIn(self.ford_claim.claim_id, anchor_ids)


class EndToEndHybridResumeBuildTests(HybridChronologyFixtureMixin, TestCase):
    """Drives the real `build_resume_draft` orchestration end to end (FakeAdapter only) to prove
    the corrected markdown output actually contains Continental/Maruti/German content that D-035
    found missing from the real ResumeDraft 4, and that the no-evidence engagement gets an explicit
    diagnostic rather than a fabricated bullet."""

    def setUp(self):
        self._build_fixture()
        self.application = JobApplication.objects.create(pipeline_phase=JobApplication.PipelinePhase.NEW)
        self.application.advance_to_analysis(jra=self.jra)
        fit_assessment = FitAssessment.objects.create(
            job_application=self.application, version=1, based_on_jra=self.jra,
            based_on_candidate_memory=self.rev,
            retrieved_claim_ids=[self.ford_claim.claim_id],
            retrieved_engagement_ids=[self.ford.engagement_id],
            baseline_chronology_manifest=self.fit_assessment.baseline_chronology_manifest,
        )
        self.application.record_fit_assessment(fit_assessment)
        self.application.approve_gate1()

    def _scripted_response(self) -> dict:
        return {
            "target_positioning": {"title_options": [], "recommended_title": "Senior Cloud Engineer"},
            "summary_elements": [
                {
                    "text": "Cloud engineer across automotive OEM/supplier engagements.",
                    "supporting_memory_claim_ids": [self.ford_claim.claim_id],
                    "matched_job_requirement_ids": [],
                }
            ],
            "experience_sections": [
                {
                    "engagement_id": self.ford.engagement_id,
                    "bullets": [
                        {
                            "text": "Owned the Ford telematics integration end to end.",
                            "supporting_memory_claim_ids": [self.ford_claim.claim_id],
                            "matched_job_requirement_ids": [],
                        }
                    ],
                },
                {
                    "engagement_id": self.continental.engagement_id,
                    "bullets": [
                        {
                            "text": "Delivered Continental's test-automation architecture.",
                            "supporting_memory_claim_ids": [self.continental_claim.claim_id],
                            "matched_job_requirement_ids": [],
                        }
                    ],
                },
                {
                    "engagement_id": self.maruti.engagement_id,
                    "bullets": [
                        {
                            "text": "Built Maruti's diagnostics tooling.",
                            "supporting_memory_claim_ids": [self.maruti_claim.claim_id],
                            "matched_job_requirement_ids": [],
                        }
                    ],
                },
            ],
            "positioning_themes": [],
            "achievements": [],
            "skill_categories": [],
            "selected_skills": [],
            "certifications": [],
            "languages": [
                {
                    "text": "German (B1)",
                    "supporting_memory_claim_ids": [self.german_claim.claim_id],
                    "matched_job_requirement_ids": [],
                }
            ],
            "positioning_guidance": {
                "preferred_role_positioning": "", "do_not_overstate": [],
                "terminology_preferences": [], "context_only_technologies": [],
                "naming_privacy_preferences": "", "resume_language": "en",
            },
        }

    def test_final_markdown_includes_previously_omitted_continental_maruti_german_content(self):
        from ..services.build import build_resume_draft

        with scripted_generation(self._scripted_response()):
            draft = build_resume_draft(self.application)

        self.assertIn("Delivered Continental's test-automation architecture.", draft.rendered_markdown)
        self.assertIn("Built Maruti's diagnostics tooling.", draft.rendered_markdown)
        self.assertIn("German (B1)", draft.rendered_markdown)
        # The engagement with zero eligible evidence still gets its header, plus an explicit
        # diagnostic rather than silence or a fabricated bullet.
        self.assertIn(self.no_evidence_engagement.approved_role_title, draft.rendered_markdown)
        self.assertIn(
            "No résumé-eligible narrative evidence is currently available for this engagement.",
            draft.rendered_markdown,
        )
