from __future__ import annotations

from django.test import TestCase

from candidate_matching.services.retrieve import RetrievalContext, RetrievedClaim, RetrievedEngagement
from candidate_memory.models import CandidateMemory

from ..models import ResumeElement
from ..schemas import AgentBuilderOutput
from ..validators.no_fabrication import NoFabricationError, validate_and_flatten
from .factories import freeze_revision, make_engagement, make_revision


def _retrieval(claim_ids, engagement_ids, claim_engagements=None) -> RetrievalContext:
    """`claim_engagements` optionally maps claim_id -> tuple of approved engagement_ids for that
    claim (default: global/unmapped) -- lets tests exercise engagement-placement correctness
    without touching the database."""
    claim_engagements = claim_engagements or {}
    return RetrievalContext(
        candidate_memory_id=1,
        claims=[
            RetrievedClaim(
                claim_id=cid,
                text="t",
                claim_type="responsibility",
                subject_scope="s",
                approved_engagement_ids=claim_engagements.get(cid, ()),
            )
            for cid in claim_ids
        ],
        engagements=[
            RetrievedEngagement(
                engagement_id=eid,
                approved_role_title="Title",
                displayed_organization="Org",
                location="",
                is_current=True,
                duration_months=12,
            )
            for eid in engagement_ids
        ],
        rules=[],
    )


def _output(**overrides) -> AgentBuilderOutput:
    data = {
        "target_positioning": {"title_options": [], "recommended_title": "Engineer"},
        "summary_elements": [],
        "experience_sections": [],
        "positioning_themes": [],
        "achievements": [],
        "skill_categories": [],
        "selected_skills": [],
        "certifications": [],
        "languages": [],
        "positioning_guidance": {},
    }
    data.update(overrides)
    return AgentBuilderOutput.model_validate(data)


class ValidateAndFlattenTests(TestCase):
    def test_valid_summary_element_passes(self):
        output = _output(
            summary_elements=[
                {
                    "text": "Summary.",
                    "supporting_memory_claim_ids": ["MC-1"],
                    "matched_job_requirement_ids": [],
                }
            ]
        )
        elements = validate_and_flatten(output, retrieval=_retrieval(["MC-1"], []))
        self.assertEqual(len(elements), 1)
        self.assertEqual(elements[0].section, ResumeElement.Section.SUMMARY)

    def test_element_with_no_evidence_is_rejected(self):
        output = _output(
            summary_elements=[
                {"text": "Summary.", "supporting_memory_claim_ids": [], "matched_job_requirement_ids": []}
            ]
        )
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=_retrieval([], []))
        self.assertIn("no supporting_memory_claim_ids", str(ctx.exception))

    def test_fabricated_claim_id_is_rejected(self):
        output = _output(
            summary_elements=[
                {
                    "text": "Summary.",
                    "supporting_memory_claim_ids": ["MC-fake"],
                    "matched_job_requirement_ids": [],
                }
            ]
        )
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=_retrieval([], []))
        self.assertIn("not in the retrieved context", str(ctx.exception))

    def test_unknown_engagement_id_is_rejected(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        output = _output(
            experience_sections=[
                {
                    "engagement_id": "CE-9999",
                    "bullets": [
                        {
                            "text": "Did things.",
                            "supporting_memory_claim_ids": ["MC-1"],
                            "matched_job_requirement_ids": [],
                        }
                    ],
                }
            ]
        )
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=_retrieval(["MC-1"], []))
        self.assertIn("No CareerEngagement", str(ctx.exception))

    def test_engagement_not_in_retrieved_context_is_rejected_even_if_approved(self):
        engagement = make_engagement()
        output = _output(
            experience_sections=[
                {
                    "engagement_id": engagement.engagement_id,
                    "bullets": [
                        {
                            "text": "Did things.",
                            "supporting_memory_claim_ids": ["MC-1"],
                            "matched_job_requirement_ids": [],
                        }
                    ],
                }
            ]
        )
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=_retrieval(["MC-1"], []))  # engagement not retrieved
        self.assertIn("not part of the retrieved context", str(ctx.exception))

    def test_valid_experience_bullet_under_a_retrieved_engagement_passes(self):
        engagement = make_engagement()
        output = _output(
            experience_sections=[
                {
                    "engagement_id": engagement.engagement_id,
                    "bullets": [
                        {
                            "text": "Did things.",
                            "supporting_memory_claim_ids": ["MC-1"],
                            "matched_job_requirement_ids": ["JR-001"],
                        }
                    ],
                }
            ]
        )
        retrieval = _retrieval(
            ["MC-1"],
            [engagement.engagement_id],
            claim_engagements={"MC-1": (engagement.engagement_id,)},
        )
        elements = validate_and_flatten(output, retrieval=retrieval)
        self.assertEqual(len(elements), 1)
        self.assertEqual(elements[0].engagement_id, engagement.engagement_id)
        self.assertEqual(elements[0].matched_job_requirement_ids, ["JR-001"])

    def test_experience_bullet_with_only_global_evidence_is_rejected(self):
        engagement = make_engagement()
        output = _output(
            experience_sections=[
                {
                    "engagement_id": engagement.engagement_id,
                    "bullets": [
                        {
                            "text": "Did things.",
                            "supporting_memory_claim_ids": ["MC-1"],
                            "matched_job_requirement_ids": [],
                        }
                    ],
                }
            ]
        )
        # MC-1 is global (no engagement mapping) -- not sufficient on its own.
        retrieval = _retrieval(["MC-1"], [engagement.engagement_id])
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=retrieval)
        self.assertIn("only global (unmapped) evidence", str(ctx.exception))

    def test_experience_bullet_mixing_matched_and_global_evidence_passes(self):
        engagement = make_engagement()
        output = _output(
            experience_sections=[
                {
                    "engagement_id": engagement.engagement_id,
                    "bullets": [
                        {
                            "text": "Did things, using Python broadly.",
                            "supporting_memory_claim_ids": ["MC-1", "MC-global"],
                            "matched_job_requirement_ids": [],
                        }
                    ],
                }
            ]
        )
        retrieval = _retrieval(
            ["MC-1", "MC-global"],
            [engagement.engagement_id],
            claim_engagements={"MC-1": (engagement.engagement_id,)},
        )
        elements = validate_and_flatten(output, retrieval=retrieval)
        self.assertEqual(sorted(elements[0].supporting_memory_claim_ids), ["MC-1", "MC-global"])

    def test_experience_bullet_citing_a_claim_mapped_to_a_different_engagement_is_rejected(self):
        engagement_a = make_engagement()
        engagement_b = make_engagement()
        output = _output(
            experience_sections=[
                {
                    "engagement_id": engagement_b.engagement_id,
                    "bullets": [
                        {
                            "text": "Did things.",
                            "supporting_memory_claim_ids": ["MC-1"],
                            "matched_job_requirement_ids": [],
                        }
                    ],
                }
            ]
        )
        retrieval = _retrieval(
            ["MC-1"],
            [engagement_a.engagement_id, engagement_b.engagement_id],
            claim_engagements={"MC-1": (engagement_a.engagement_id,)},
        )
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=retrieval)
        self.assertIn("approved only for a different engagement", str(ctx.exception))

    def test_claim_approved_for_multiple_engagements_passes_under_either(self):
        engagement_a = make_engagement()
        engagement_b = make_engagement()
        retrieval = _retrieval(
            ["MC-1"],
            [engagement_a.engagement_id, engagement_b.engagement_id],
            claim_engagements={"MC-1": (engagement_a.engagement_id, engagement_b.engagement_id)},
        )
        for target in (engagement_a, engagement_b):
            output = _output(
                experience_sections=[
                    {
                        "engagement_id": target.engagement_id,
                        "bullets": [
                            {
                                "text": "Did things.",
                                "supporting_memory_claim_ids": ["MC-1"],
                                "matched_job_requirement_ids": [],
                            }
                        ],
                    }
                ]
            )
            elements = validate_and_flatten(output, retrieval=retrieval)
            self.assertEqual(elements[0].engagement_id, target.engagement_id)

    def test_summary_may_use_a_global_claim_with_no_engagement_mapping(self):
        output = _output(
            summary_elements=[
                {
                    "text": "Broadly skilled.",
                    "supporting_memory_claim_ids": ["MC-1"],
                    "matched_job_requirement_ids": [],
                }
            ]
        )
        elements = validate_and_flatten(output, retrieval=_retrieval(["MC-1"], []))
        self.assertEqual(elements[0].section, ResumeElement.Section.SUMMARY)

    def test_multiple_failures_are_all_reported_together(self):
        output = _output(
            summary_elements=[
                {"text": "A.", "supporting_memory_claim_ids": [], "matched_job_requirement_ids": []},
                {"text": "B.", "supporting_memory_claim_ids": ["MC-fake"], "matched_job_requirement_ids": []},
            ]
        )
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=_retrieval([], []))
        self.assertEqual(len(ctx.exception.failures), 2)

    def test_forbidden_static_field_fails_schema_validation_before_reaching_this_validator(self):
        with self.assertRaises(Exception):
            AgentBuilderOutput.model_validate(
                {
                    "target_positioning": {"title_options": [], "recommended_title": "Engineer"},
                    "summary_elements": [],
                    "experience_sections": [
                        {
                            "engagement_id": "CE-0001",
                            "employer": "Sneaky Corp",  # forbidden -- not a real field
                            "bullets": [],
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
            )
