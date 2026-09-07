"""D-037 corrective fix: `begin_new_version_from_ready` is now a real, guarded READY -> ANALYSIS
transition (`JobApplication.begin_revision_from_ready`), not the no-op D-036 originally shipped.
Entirely synthetic data -- never JobApplication 9 -- proving the design requirements hold:
`ResumeDraft` 1 (this fixture's own analogue of the real `ResumeDraft` 4) stays immutable, later
versions are created rather than modifying it, freshness/current-pointer rules are enforced
throughout, and Gate 1/Gate 2 must both genuinely re-approve the new versions.

Every LLM call is routed through the M2 `FakeAdapter` (`resume_builder.tests.factories.
scripted_generation`) -- zero network, zero live credentials.
"""

from __future__ import annotations

from django.test import Client, TestCase
from django.urls import reverse

from candidate_matching.models import FitAssessment
from candidate_matching.tests.factories import scripted_agent_candidate
from resume_builder.models import ResumeDraft
from resume_builder.tests.factories import (
    make_ready_for_gate2_application,
    scripted_generation,
    valid_generation_response,
)
from reviews.services import approve_gate2, run_agent_builder, run_agent_candidate

from ..models import InvalidPhaseTransitionError, JobApplication, StaleAssessmentError
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

    def test_transitions_ready_to_analysis_and_touches_no_pointer(self):
        application, _claim_id, _engagement_id = _make_ready_application()
        before = JobApplication.objects.get(pk=application.pk)
        self.assertEqual(before.pipeline_phase, JobApplication.PipelinePhase.READY)

        begin_new_version_from_ready(application)

        after = JobApplication.objects.get(pk=application.pk)
        self.assertEqual(after.pipeline_phase, JobApplication.PipelinePhase.ANALYSIS)
        self.assertEqual(before.current_jra_id, after.current_jra_id)
        self.assertEqual(before.current_fit_assessment_id, after.current_fit_assessment_id)
        self.assertEqual(before.current_resume_draft_id, after.current_resume_draft_id)
        self.assertEqual(before.application_outcome, after.application_outcome)
        self.assertGreater(after.updated_at, before.updated_at)

    def test_refuses_a_duplicate_submission(self):
        application, _claim_id, _engagement_id = _make_ready_application()
        begin_new_version_from_ready(application)
        application.refresh_from_db()
        # The application is now ANALYSIS, not READY -- a second submission (e.g. a double-click)
        # is safely rejected rather than double-applied or silently ignored.
        with self.assertRaises(RevisionNotAuthorizedError):
            begin_new_version_from_ready(application)

    def test_refuses_when_already_stale_relative_to_upstream_change(self):
        application, claim_id, _engagement_id = _make_ready_application()
        # Simulate the ChainFreshness scenario noted in D-035's own investigation: a Gate-1
        # feedback re-run targeting Agent Candidate after Gate 2 was already approved leaves the
        # application READY but with a ResumeDraft now stale relative to the (new) current
        # FitAssessment -- a different, already-handled recovery path, not this one.
        with scripted_agent_candidate(_assessment_response(claim_id)):
            run_agent_candidate(application)
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.READY)
        with self.assertRaises(RevisionNotAuthorizedError):
            begin_new_version_from_ready(application)

    def test_model_method_raises_typed_errors_directly(self):
        application, _claim_id, _engagement_id = make_ready_for_gate2_application()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.PREPARATION)
        with self.assertRaises(InvalidPhaseTransitionError):
            application.begin_revision_from_ready()

    def test_model_method_raises_stale_assessment_error_directly(self):
        application, claim_id, _engagement_id = _make_ready_application()
        with scripted_agent_candidate(_assessment_response(claim_id)):
            run_agent_candidate(application)
        application.refresh_from_db()
        with self.assertRaises(StaleAssessmentError):
            application.begin_revision_from_ready()

    def test_full_revision_sequence_requires_gate1_and_gate2_to_re_approve(self):
        application, claim_id, engagement_id = _make_ready_application()
        original_draft = ResumeDraft.objects.get(pk=application.current_resume_draft_id)
        self.assertEqual(original_draft.version, 1)
        self.assertTrue(original_draft.is_confirmed)
        original_markdown = original_draft.rendered_markdown
        original_confirmed_at = original_draft.confirmed_at
        original_fit_assessment_id = application.current_fit_assessment_id

        # Explicit operator authorization -- the one required call before re-entering Gate 1 for a
        # READY application. Unlike D-036's no-op, this genuinely reopens the gate: the phase is
        # now ANALYSIS, so Agent Builder cannot run again until Gate 1 re-approves.
        begin_new_version_from_ready(application)
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.ANALYSIS)
        self.assertEqual(application.current_fit_assessment_id, original_fit_assessment_id)

        from resume_builder.services.build import ResumeBuilderError, build_resume_draft

        with self.assertRaises(ResumeBuilderError):
            build_resume_draft(application)

        with scripted_agent_candidate(_assessment_response(claim_id)):
            new_fit_assessment = run_agent_candidate(application)
        self.assertEqual(new_fit_assessment.version, 2)
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.ANALYSIS)

        application.approve_gate1()
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.PREPARATION)
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
        application, claim_id, _engagement_id = _make_ready_application()
        begin_new_version_from_ready(application)
        application.refresh_from_db()
        with scripted_agent_candidate(_assessment_response(claim_id)):
            run_agent_candidate(application)
        application.approve_gate1()
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.PREPARATION)

        # A new FitAssessment/PREPARATION exists, but Agent Builder has not been re-run yet -- the
        # current ResumeDraft (version 1) is now stale relative to the new FitAssessment, and
        # approve_gate2 must still refuse it (D-006), exactly as it would for any other stale draft.
        with self.assertRaises(StaleAssessmentError):
            application.approve_gate2()

    def test_historical_artifacts_and_outcome_survive_a_full_revision(self):
        from ..services import compute_freshness, set_application_outcome

        application, claim_id, engagement_id = _make_ready_application()
        set_application_outcome(application, JobApplication.ApplicationOutcome.APPLIED)
        application.refresh_from_db()

        begin_new_version_from_ready(application)
        application.refresh_from_db()
        self.assertEqual(application.application_outcome, JobApplication.ApplicationOutcome.APPLIED)

        with scripted_agent_candidate(_assessment_response(claim_id)):
            run_agent_candidate(application)
        application.approve_gate1()
        application.refresh_from_db()
        with scripted_generation(valid_generation_response(engagement_id, claim_id)):
            run_agent_builder(application)
        approve_gate2(application)
        application.refresh_from_db()

        # Outcome preserved throughout -- nothing in the revision workflow ever writes it.
        self.assertEqual(application.application_outcome, JobApplication.ApplicationOutcome.APPLIED)
        self.assertFalse(compute_freshness(application).draft_stale)
        # Historical version 1 rows remain queryable/unchanged.
        self.assertEqual(FitAssessment.objects.filter(job_application=application, version=1).count(), 1)
        self.assertEqual(ResumeDraft.objects.filter(job_application=application, version=1).count(), 1)


class BeginRevisionViewTests(TestCase):
    def test_get_is_rejected(self):
        application, _claim_id, _engagement_id = _make_ready_application()
        client = Client()
        response = client.get(reverse("job_applications:begin_revision", kwargs={"pk": application.pk}))
        self.assertEqual(response.status_code, 405)
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.READY)

    def test_post_without_csrf_token_is_rejected(self):
        application, _claim_id, _engagement_id = _make_ready_application()
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("job_applications:begin_revision", kwargs={"pk": application.pk}))
        self.assertEqual(response.status_code, 403)
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.READY)

    def test_post_from_ready_begins_a_revision(self):
        application, _claim_id, _engagement_id = _make_ready_application()
        client = Client()
        response = client.post(reverse("job_applications:begin_revision", kwargs={"pk": application.pk}))
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.ANALYSIS)

    def test_post_when_not_ready_is_safely_rejected(self):
        application, _claim_id, _engagement_id = make_ready_for_gate2_application()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.PREPARATION)
        client = Client()
        response = client.post(reverse("job_applications:begin_revision", kwargs={"pk": application.pk}))
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.PREPARATION)

    def test_duplicate_post_submission_is_safe(self):
        application, _claim_id, _engagement_id = _make_ready_application()
        client = Client()
        first = client.post(reverse("job_applications:begin_revision", kwargs={"pk": application.pk}))
        second = client.post(reverse("job_applications:begin_revision", kwargs={"pk": application.pk}))
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        application.refresh_from_db()
        # Exactly one transition happened -- the second POST was safely rejected, not re-applied.
        self.assertEqual(application.pipeline_phase, JobApplication.PipelinePhase.ANALYSIS)
