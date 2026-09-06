"""D-035 investigation: the canonical, explicit-authorization READY-revision workflow
(`services.begin_new_version_from_ready`). Entirely synthetic data -- never JobApplication 9 --
proving the design requirements hold: `ResumeDraft` 1 (this fixture's own analogue of the real
`ResumeDraft` 4) stays immutable, later versions are created rather than modifying it, freshness/
current-pointer rules are enforced throughout, and no direct `pipeline_phase` mutation is used.

Every LLM call is routed through the M2 `FakeAdapter` (`resume_builder.tests.factories.
scripted_generation`) -- zero network, zero live credentials.
"""

from __future__ import annotations

from django.test import TestCase

from candidate_matching.models import FitAssessment
from candidate_matching.tests.factories import scripted_agent_candidate
from resume_builder.models import ResumeDraft
from resume_builder.tests.factories import (
    make_ready_for_gate2_application,
    scripted_generation,
    valid_generation_response,
)
from reviews.services import approve_gate2, run_agent_builder, run_agent_candidate

from ..models import JobApplication
from ..services import RevisionNotAuthorizedError, begin_new_version_from_ready


def _assessment_response(claim_id: str) -> dict:
    return {
        "requirement_assessments": [
            {
                "requirement_id": "JR-001",
                "disposition": "MATCH",
                "explanation": "Directly owned an equivalent service end to end.",
                "gap_or_limitation": "",
                "supporting_memory_claim_ids": [claim_id],
                "supporting_engagement_ids": [],
            }
        ]
    }


def _make_ready_application():
    application, claim_id, engagement_id = make_ready_for_gate2_application()
    with scripted_generation(valid_generation_response(engagement_id, claim_id)):
        run_agent_builder(application)
    approve_gate2(application)
    application.refresh_from_db()
    return application, claim_id, engagement_id


class BeginNewVersionFromReadyTests(TestCase):
    def test_refuses_when_not_ready(self):
        application, _claim_id, _engagement_id = make_ready_for_gate2_application()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.PREPARATION)
        with self.assertRaises(RevisionNotAuthorizedError):
            begin_new_version_from_ready(application)

    def test_performs_no_mutation_itself(self):
        application, _claim_id, _engagement_id = _make_ready_application()
        before = JobApplication.objects.get(pk=application.pk)
        begin_new_version_from_ready(application)
        after = JobApplication.objects.get(pk=application.pk)
        self.assertEqual(before.pipeline_phase, after.pipeline_phase)
        self.assertEqual(before.current_jra_id, after.current_jra_id)
        self.assertEqual(before.current_fit_assessment_id, after.current_fit_assessment_id)
        self.assertEqual(before.current_resume_draft_id, after.current_resume_draft_id)
        self.assertEqual(before.updated_at, after.updated_at)

    def test_full_revision_sequence_preserves_the_original_resume_draft_immutable(self):
        application, claim_id, engagement_id = _make_ready_application()
        original_draft = ResumeDraft.objects.get(pk=application.current_resume_draft_id)
        self.assertEqual(original_draft.version, 1)
        self.assertTrue(original_draft.is_confirmed)
        original_markdown = original_draft.rendered_markdown
        original_confirmed_at = original_draft.confirmed_at

        # Explicit operator authorization -- the one required call before re-entering Gate 1 for a
        # READY application.
        begin_new_version_from_ready(application)

        # The de facto (now formalized) workflow: rerun Agent Candidate -> new FitAssessment
        # version -> re-approve Gate 1. `approve_gate1` only advances ANALYSIS -> PREPARATION; an
        # application already READY that gets a fresh, non-stale FitAssessment stays READY (its
        # own documented idempotent-no-op case) -- the phase itself never regresses. What actually
        # still gates a premature "done" state is Gate 2's own D-006 freshness check below, not a
        # phase rollback (see `test_freshness_rules_still_block_a_premature_gate2_approval_mid_revision`).
        with scripted_agent_candidate(_assessment_response(claim_id)):
            new_fit_assessment = run_agent_candidate(application)
        self.assertEqual(new_fit_assessment.version, 2)
        application.approve_gate1()
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.READY)
        self.assertEqual(application.current_fit_assessment_id, new_fit_assessment.pk)

        with scripted_generation(valid_generation_response(engagement_id, claim_id)):
            new_draft = run_agent_builder(application)
        self.assertEqual(new_draft.version, 2)
        approve_gate2(application)
        application.refresh_from_db()

        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.READY)
        self.assertEqual(application.current_resume_draft_id, new_draft.pk)
        self.assertEqual(application.current_fit_assessment_id, new_fit_assessment.pk)
        self.assertEqual(FitAssessment.objects.filter(job_application=application).count(), 2)
        self.assertEqual(ResumeDraft.objects.filter(job_application=application).count(), 2)

        # The original version 1 draft is untouched -- append-only, never edited in place.
        original_draft.refresh_from_db()
        self.assertEqual(original_draft.rendered_markdown, original_markdown)
        self.assertEqual(original_draft.confirmed_at, original_confirmed_at)
        self.assertNotEqual(new_draft.pk, original_draft.pk)

    def test_freshness_rules_still_block_a_premature_gate2_approval_mid_revision(self):
        application, claim_id, engagement_id = _make_ready_application()
        begin_new_version_from_ready(application)
        with scripted_agent_candidate(_assessment_response(claim_id)):
            run_agent_candidate(application)
        application.approve_gate1()
        application.refresh_from_db()

        # A new FitAssessment/PREPARATION exists, but Agent Builder has not been re-run yet -- the
        # current ResumeDraft (version 1) is now stale relative to the new FitAssessment, and
        # approve_gate2 must still refuse it (D-006), exactly as it would for any other stale draft.
        from ..models import StaleAssessmentError

        with self.assertRaises(StaleAssessmentError):
            application.approve_gate2()
