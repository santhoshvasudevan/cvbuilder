"""End-to-end, DB-backed adversarial tests for engagement-placement correctness (audit hardening,
2026-09-03), through the real `build_builder_context` -> `validate_and_flatten` pipeline -- not
just hand-built `RetrievalContext` fixtures. Named after the real corpus's own Ford/Continental/
Maruti engagements per the audit's explicit request, though these use synthetic operator-approved
engagement records, never real candidate data.
"""

from __future__ import annotations

from django.test import TestCase

from candidate_matching.models import FitAssessment
from candidate_memory.models import CandidateMemory, ClaimEngagementMapping
from job_applications.models import JobApplication
from job_intake.models import JobRequirementAnalysis

from ..services.context import build_builder_context
from ..validators.no_fabrication import NoFabricationError, validate_and_flatten
from .factories import freeze_revision, make_engagement, make_narrative_claim, make_revision


def _make_jra() -> JobRequirementAnalysis:
    application = JobApplication.objects.create()
    return JobRequirementAnalysis.objects.create(
        job_application=application,
        version=1,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="x" * 25,
        extracted_text="x" * 25,
        extracted_text_sha256="0" * 64,
        posting_language="en",
    )


def _output_with_bullet(engagement_id: str, claim_ids: list[str]) -> dict:
    return {
        "target_positioning": {"title_options": [], "recommended_title": "Engineer"},
        "summary_elements": [],
        "experience_sections": [
            {
                "engagement_id": engagement_id,
                "bullets": [
                    {
                        "text": "Did the work.",
                        "supporting_memory_claim_ids": claim_ids,
                        "matched_job_requirement_ids": [],
                    }
                ],
            }
        ],
        "positioning_themes": [],
        "achievements": [],
        "skill_categories": [],
        "selected_skills": [],
        "certifications": [],
        "languages": [],
        "positioning_guidance": {},
    }


class EngagementPlacementAdversarialTests(TestCase):
    def setUp(self):
        self.rev = make_revision(status=CandidateMemory.Status.BUILDING)
        self.ford = make_engagement(legal_employer="Ambigai", client_organization="Ford Motor Company")
        self.continental = make_engagement(legal_employer="Ambigai", client_organization="Continental AG")
        self.maruti = make_engagement(legal_employer="Maruti Suzuki India")

        self.ford_claim = make_narrative_claim(
            self.rev, canonical_text_en="Delivered a Ford connectivity feature."
        )
        self.continental_claim = make_narrative_claim(
            self.rev,
            canonical_text_en="Delivered a Continental test-architecture improvement.",
            stable_key="k2",
        )
        self.global_claim = make_narrative_claim(
            self.rev,
            canonical_text_en="Proficient in Python and C++.",
            stable_key="k3",
        )
        ClaimEngagementMapping.objects.create(
            memory_claim=self.ford_claim,
            career_engagement=self.ford,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        ClaimEngagementMapping.objects.create(
            memory_claim=self.continental_claim,
            career_engagement=self.continental,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        freeze_revision(self.rev, CandidateMemory.Status.ACTIVE)

        self.fit_assessment = FitAssessment(
            based_on_jra=_make_jra(),
            retrieved_claim_ids=[
                self.ford_claim.claim_id,
                self.continental_claim.claim_id,
                self.global_claim.claim_id,
            ],
            retrieved_engagement_ids=[
                self.ford.engagement_id,
                self.continental.engagement_id,
                self.maruti.engagement_id,
            ],
        )
        self.retrieval = build_builder_context(self.fit_assessment)

    def test_ford_evidence_placed_under_continental_is_rejected(self):
        from ..schemas import AgentBuilderOutput

        output = AgentBuilderOutput.model_validate(
            _output_with_bullet(self.continental.engagement_id, [self.ford_claim.claim_id])
        )
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=self.retrieval)
        self.assertIn("approved only for a different engagement", str(ctx.exception))

    def test_continental_evidence_placed_under_maruti_is_rejected(self):
        from ..schemas import AgentBuilderOutput

        output = AgentBuilderOutput.model_validate(
            _output_with_bullet(self.maruti.engagement_id, [self.continental_claim.claim_id])
        )
        with self.assertRaises(NoFabricationError):
            validate_and_flatten(output, retrieval=self.retrieval)

    def test_global_only_evidence_for_an_experience_bullet_is_rejected(self):
        from ..schemas import AgentBuilderOutput

        output = AgentBuilderOutput.model_validate(
            _output_with_bullet(self.ford.engagement_id, [self.global_claim.claim_id])
        )
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=self.retrieval)
        self.assertIn("only global (unmapped) evidence", str(ctx.exception))

    def test_matching_engagement_plus_supplementary_global_evidence_passes(self):
        from ..schemas import AgentBuilderOutput

        output = AgentBuilderOutput.model_validate(
            _output_with_bullet(
                self.ford.engagement_id, [self.ford_claim.claim_id, self.global_claim.claim_id]
            )
        )
        elements = validate_and_flatten(output, retrieval=self.retrieval)
        self.assertEqual(elements[0].engagement_id, self.ford.engagement_id)

    def test_claim_validly_mapped_to_multiple_engagements_is_accepted_under_either(self):
        from ..schemas import AgentBuilderOutput

        ClaimEngagementMapping.objects.create(
            memory_claim=self.ford_claim,
            career_engagement=self.continental,
            status=ClaimEngagementMapping.Status.APPROVED,
        )
        fit_assessment = FitAssessment(
            based_on_jra=self.fit_assessment.based_on_jra,
            retrieved_claim_ids=[self.ford_claim.claim_id],
            retrieved_engagement_ids=[self.ford.engagement_id, self.continental.engagement_id],
        )
        retrieval = build_builder_context(fit_assessment)

        for target in (self.ford, self.continental):
            output = AgentBuilderOutput.model_validate(
                _output_with_bullet(target.engagement_id, [self.ford_claim.claim_id])
            )
            elements = validate_and_flatten(output, retrieval=retrieval)
            self.assertEqual(elements[0].engagement_id, target.engagement_id)

    def _mapping_status_scenario(self, status):
        # A fresh revision is required: self.rev is already ACTIVE (frozen in setUp), and claims
        # can only be created while a revision is BUILDING/NEEDS_REVIEW. Only one CandidateMemory
        # may be ACTIVE at a time, so self.rev is superseded first (the one legal transition out
        # of ACTIVE) -- this scenario doesn't use self.rev/self.retrieval at all.
        freeze_revision(self.rev, CandidateMemory.Status.SUPERSEDED)
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        claim = make_narrative_claim(rev, canonical_text_en="Mapping-status scenario claim.")
        ClaimEngagementMapping.objects.create(memory_claim=claim, career_engagement=self.ford, status=status)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        return claim

    def test_rejected_mapping_never_reaches_retrieval_citing_it_is_treated_as_fabricated(self):
        from ..schemas import AgentBuilderOutput

        rejected_claim = self._mapping_status_scenario(ClaimEngagementMapping.Status.REJECTED)
        fit_assessment = FitAssessment(
            based_on_jra=self.fit_assessment.based_on_jra,
            retrieved_claim_ids=[rejected_claim.claim_id],  # only ever possible via a stale/tampered pointer
            retrieved_engagement_ids=[self.ford.engagement_id],
        )
        retrieval = build_builder_context(fit_assessment)
        # build_builder_context re-verifies each claim's OWN eligibility -- it does not exclude a
        # claim merely for having a REJECTED mapping (a REJECTED mapping is orthogonal to a claim's
        # own confirmation/eligibility), so the claim is legitimately retrievable but carries no
        # approved_engagement_ids -- i.e. it is "global" from Agent Builder's point of view.
        self.assertEqual(retrieval.claims[0].approved_engagement_ids, ())

        output = AgentBuilderOutput.model_validate(
            _output_with_bullet(self.ford.engagement_id, [rejected_claim.claim_id])
        )
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=retrieval)
        self.assertIn("only global (unmapped) evidence", str(ctx.exception))

    def test_proposed_only_mapping_is_also_treated_as_global_never_as_approved_for_that_engagement(self):
        from ..schemas import AgentBuilderOutput

        proposed_claim = self._mapping_status_scenario(ClaimEngagementMapping.Status.PROPOSED)
        fit_assessment = FitAssessment(
            based_on_jra=self.fit_assessment.based_on_jra,
            retrieved_claim_ids=[proposed_claim.claim_id],
            retrieved_engagement_ids=[self.ford.engagement_id],
        )
        retrieval = build_builder_context(fit_assessment)
        self.assertEqual(retrieval.claims[0].approved_engagement_ids, ())

        output = AgentBuilderOutput.model_validate(
            _output_with_bullet(self.ford.engagement_id, [proposed_claim.claim_id])
        )
        with self.assertRaises(NoFabricationError):
            validate_and_flatten(output, retrieval=retrieval)

    def test_claim_from_a_different_candidate_memory_revision_is_never_retrievable(self):
        from ..schemas import AgentBuilderOutput

        other_rev = make_revision(status=CandidateMemory.Status.BUILDING)
        other_claim = make_narrative_claim(other_rev, canonical_text_en="From a different revision.")
        freeze_revision(other_rev, CandidateMemory.Status.SUPERSEDED)

        fit_assessment = FitAssessment(
            based_on_jra=self.fit_assessment.based_on_jra,
            retrieved_claim_ids=[other_claim.claim_id],  # only reachable via a stale/tampered pointer
            retrieved_engagement_ids=[self.ford.engagement_id],
        )
        retrieval = build_builder_context(fit_assessment)
        self.assertEqual(retrieval.claims, [])  # cross-revision claim never resolves at all

        output = AgentBuilderOutput.model_validate(
            _output_with_bullet(self.ford.engagement_id, [other_claim.claim_id])
        )
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=retrieval)
        self.assertIn("not in the retrieved context", str(ctx.exception))

    def test_unknown_engagement_id_is_rejected(self):
        from ..schemas import AgentBuilderOutput

        output = AgentBuilderOutput.model_validate(_output_with_bullet("CE-9999", [self.ford_claim.claim_id]))
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=self.retrieval)
        self.assertIn("No CareerEngagement", str(ctx.exception))

    def test_unapproved_draft_engagement_is_rejected(self):
        from ..schemas import AgentBuilderOutput

        draft_engagement = make_engagement(approval_status="DRAFT")
        output = AgentBuilderOutput.model_validate(
            _output_with_bullet(draft_engagement.engagement_id, [self.ford_claim.claim_id])
        )
        with self.assertRaises(NoFabricationError):
            validate_and_flatten(output, retrieval=self.retrieval)
