from __future__ import annotations

from django.test import TestCase

from candidate_memory.models import CandidateMemory, CareerEngagement, ClaimEngagementMapping

from ..models import RequirementAssessment
from ..services.fit_assessment import AgentCandidateError, build_fit_assessment
from ..services.retrieve import NoActiveCandidateMemoryError
from .factories import (
    freeze_revision,
    make_engagement,
    make_job_application_with_jra,
    make_narrative_claim,
    make_revision,
    scripted_agent_candidate,
    valid_assessment_response,
)


class BuildFitAssessmentTests(TestCase):
    def test_raises_without_an_active_candidate_memory(self):
        application = make_job_application_with_jra()
        with self.assertRaises(NoActiveCandidateMemoryError):
            build_fit_assessment(application)

    def test_raises_without_a_current_jra(self):
        from job_applications.models import JobApplication

        application = JobApplication.objects.create()
        with self.assertRaises(AgentCandidateError):
            build_fit_assessment(application)

    def test_raises_when_the_current_jra_has_zero_job_requirements(self):
        """M5 precondition (2026-09-04 AJ hardening, D-022): refuses to run against a current JRA
        with zero JobRequirements -- this guards every such JRA, including one that already
        existed before this check was added (e.g. the real JobApplication id=9, created before
        Agent Jobber's own sanity gate existed), since it is a fresh runtime check against
        whatever the current JRA actually contains, never something baked in at creation time."""
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(requirements=[])

        self.assertEqual(application.current_jra.requirements.count(), 0)
        with self.assertRaises(AgentCandidateError):
            build_fit_assessment(application)

    def test_every_relevant_requirement_gets_exactly_one_assessment(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service end to end.")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        application = make_job_application_with_jra(
            requirements=[
                {"category": "MANDATORY", "text": "Own the payments service end to end."},
                {"category": "MANDATORY", "text": "5+ years of total experience in software"},
            ]
        )

        with scripted_agent_candidate(valid_assessment_response()):
            fit_assessment = build_fit_assessment(application)

        assessments = fit_assessment.requirement_assessments.all()
        self.assertEqual(assessments.count(), 2)
        self.assertEqual({a.requirement_id for a in assessments}, {"JR-001", "JR-002"})

    def test_static_requirement_is_never_sent_to_the_llm(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        engagement = make_engagement(
            start_year=2010, start_month=1,
            end_status=CareerEngagement.EndStatus.KNOWN, end_year=2020, end_month=1,
        )
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "5+ years of total experience"}]
        )

        # An LLM response that has nothing for JR-001 would leave it uncovered if it were routed
        # to the LLM -- since it's static, the LLM is never called for it at all.
        with scripted_agent_candidate({"requirement_assessments": []}):
            fit_assessment = build_fit_assessment(application)

        assessment = fit_assessment.requirement_assessments.get(requirement_id="JR-001")
        self.assertEqual(assessment.disposition, RequirementAssessment.Disposition.MATCH)
        self.assertEqual(assessment.supporting_engagement_ids, [engagement.engagement_id])

    def test_a_known_gap_is_never_softened_by_sanitization(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Experience with Kubernetes at scale"}]
        )
        response = valid_assessment_response(
            requirement_assessments=[
                {
                    "requirement_id": "JR-001",
                    "disposition": "GAP",
                    "explanation": "No evidence of Kubernetes experience.",
                    "gap_or_limitation": "No Kubernetes claims found.",
                    "supporting_memory_claim_ids": [],
                    "supporting_engagement_ids": [],
                }
            ]
        )
        with scripted_agent_candidate(response):
            fit_assessment = build_fit_assessment(application)

        assessment = fit_assessment.requirement_assessments.get(requirement_id="JR-001")
        self.assertEqual(assessment.disposition, RequirementAssessment.Disposition.GAP)

    def test_match_with_a_fabricated_claim_id_is_downgraded_to_unknown(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Experience with Kubernetes at scale"}]
        )
        response = valid_assessment_response(
            requirement_assessments=[
                {
                    "requirement_id": "JR-001",
                    "disposition": "MATCH",
                    "explanation": "Strong Kubernetes background.",
                    "gap_or_limitation": "",
                    "supporting_memory_claim_ids": ["MC-9999-9999"],
                    "supporting_engagement_ids": [],
                }
            ]
        )
        with scripted_agent_candidate(response):
            fit_assessment = build_fit_assessment(application)

        assessment = fit_assessment.requirement_assessments.get(requirement_id="JR-001")
        self.assertEqual(assessment.disposition, RequirementAssessment.Disposition.UNKNOWN)

    def test_records_exactly_which_claims_were_retrieved(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        claim = make_narrative_claim(rev)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Some narrative requirement"}]
        )
        with scripted_agent_candidate(valid_assessment_response()):
            fit_assessment = build_fit_assessment(application)

        self.assertEqual(fit_assessment.retrieved_claim_ids, [claim.claim_id])

    def test_creates_a_new_version_and_updates_the_current_pointer_on_rerun(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Some narrative requirement"}]
        )
        with scripted_agent_candidate(valid_assessment_response()):
            first = build_fit_assessment(application)
            second = build_fit_assessment(application)

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        application.refresh_from_db()
        self.assertEqual(application.current_fit_assessment_id, second.pk)


class PinnedEvidenceIdentityTests(TestCase):
    """D-037: `build_fit_assessment` (the real M5 boundary) must always pin
    `based_on_candidate_memory` and persist a valid `baseline_chronology_manifest` atomically with
    the new `FitAssessment` -- these tests exercise the real orchestration function end to end
    (FakeAdapter only), not a hand-built test fixture."""

    def test_new_fit_assessment_pins_the_active_candidate_memory(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Some narrative requirement"}]
        )
        with scripted_agent_candidate(valid_assessment_response()):
            fit_assessment = build_fit_assessment(application)

        self.assertEqual(fit_assessment.based_on_candidate_memory_id, rev.pk)

    def test_manifest_is_persisted_atomically_and_is_valid(self):
        from ..services.baseline_chronology import validate_manifest

        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service end to end.")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Own the payments service end to end."}]
        )
        with scripted_agent_candidate(
            valid_assessment_response(
                requirement_assessments=[
                    {
                        "requirement_id": "JR-001",
                        "disposition": "MATCH",
                        "explanation": "Directly owned an equivalent service end to end.",
                        "gap_or_limitation": "",
                        "supporting_memory_claim_ids": [claim.claim_id],
                        "supporting_engagement_ids": [],
                    }
                ]
            )
        ):
            fit_assessment = build_fit_assessment(application)

        # Re-fetched fresh from the database: proves the manifest was actually committed in the
        # same transaction as the FitAssessment row, not merely held on the in-memory instance.
        persisted = type(fit_assessment).objects.get(pk=fit_assessment.pk)
        manifest = persisted.baseline_chronology_manifest
        self.assertTrue(manifest)
        validate_manifest(manifest, candidate_memory_id=rev.pk)
        self.assertEqual(manifest["approved_engagement_ids"], [engagement.engagement_id])
        self.assertIn(claim.claim_id, manifest["claim_inclusion_reasons"])

    def test_candidate_memory_becoming_active_later_does_not_alter_an_existing_fit_assessments_manifest(self):
        """The exact D-037 drift scenario: FitAssessment 10 created from CandidateMemory 7; a
        later CandidateMemory 8 becomes ACTIVE. Agent Builder's context for the older
        FitAssessment must be byte-for-byte identical before and after."""
        from resume_builder.services.context import build_builder_context

        rev7 = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev7, canonical_text_en="Owned the payments service end to end.")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev7, CandidateMemory.Status.ACTIVE)
        application = make_job_application_with_jra(
            requirements=[{"category": "MANDATORY", "text": "Own the payments service end to end."}]
        )
        with scripted_agent_candidate(
            valid_assessment_response(
                requirement_assessments=[
                    {
                        "requirement_id": "JR-001",
                        "disposition": "MATCH",
                        "explanation": "Directly owned an equivalent service end to end.",
                        "gap_or_limitation": "",
                        "supporting_memory_claim_ids": [claim.claim_id],
                        "supporting_engagement_ids": [],
                    }
                ]
            )
        ):
            fit_assessment_10 = build_fit_assessment(application)

        manifest_before = dict(fit_assessment_10.baseline_chronology_manifest)
        context_before = build_builder_context(fit_assessment_10)

        # CandidateMemory 8 activates -- only one CandidateMemory may be ACTIVE at a time, so rev7
        # is superseded first (the one legal ACTIVE -> SUPERSEDED transition), then the original
        # engagement is even rejected in the meantime.
        rev8 = make_revision(status=CandidateMemory.Status.BUILDING)
        make_narrative_claim(rev8, canonical_text_en="An entirely unrelated later claim.")
        freeze_revision(rev7, CandidateMemory.Status.SUPERSEDED)
        freeze_revision(rev8, CandidateMemory.Status.ACTIVE)
        engagement.approval_status = engagement.ApprovalStatus.REJECTED
        engagement.save(update_fields=["approval_status"])

        fit_assessment_10.refresh_from_db()
        manifest_after = fit_assessment_10.baseline_chronology_manifest
        context_after = build_builder_context(fit_assessment_10)

        self.assertEqual(manifest_before, manifest_after)
        self.assertEqual(context_before.claim_ids, context_after.claim_ids)
        self.assertEqual(context_before.engagement_ids, context_after.engagement_ids)
        self.assertEqual(
            [c.approved_engagement_ids for c in context_before.claims],
            [c.approved_engagement_ids for c in context_after.claims],
        )
