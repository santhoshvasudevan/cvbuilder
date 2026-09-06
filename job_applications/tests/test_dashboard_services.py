from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from candidate_matching.models import FitAssessment
from job_intake.models import JobRequirementAnalysis
from resume_builder.models import ResumeDraft

from ..models import JobApplication
from ..services import (
    InvalidOutcomeTransitionError,
    build_dashboard_row,
    compute_dashboard_summary,
    compute_freshness,
    derive_dashboard_status,
    list_dashboard_rows,
    resolve_next_action,
    set_application_outcome,
)


def _make_jra(
    application: JobApplication, version: int = 1, employer: str = "Acme", role_title: str = "Engineer"
):
    return JobRequirementAnalysis.objects.create(
        job_application=application, version=version,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="posting text", extracted_text="posting text",
        extracted_text_sha256="0" * 64, posting_language="en",
        employer=employer, role_title=role_title,
    )


def _make_fit_assessment(application: JobApplication, jra, version: int = 1):
    fa = FitAssessment.objects.create(job_application=application, version=version, based_on_jra=jra)
    application.record_fit_assessment(fa)
    return fa


def _make_resume_draft(application: JobApplication, fit_assessment, version: int = 1):
    draft = ResumeDraft.objects.create(
        job_application=application, version=version, based_on_fit_assessment=fit_assessment,
        recommended_title="Engineer", rendered_markdown="# Engineer\n\n## Professional Summary\n",
    )
    application.record_resume_draft(draft)
    return draft


class DashboardRowByPhaseTests(TestCase):
    """Dashboard row/status for every pipeline phase (Phase G #1)."""

    def test_new_phase_no_jra(self):
        application = JobApplication.objects.create()
        row = build_dashboard_row(application)
        self.assertEqual(row.status, "New")
        self.assertFalse(row.jra_exists)
        self.assertEqual(row.next_action.code, "NO_ANALYSIS")
        self.assertFalse(row.review_required)
        self.assertFalse(row.is_stale)

    def test_analysis_phase_awaiting_ac(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        row = build_dashboard_row(application)
        self.assertEqual(row.status, "Analysis")
        self.assertFalse(row.fit_assessment_exists)
        self.assertEqual(row.next_action.code, "GATE1")
        self.assertEqual(row.next_action.label, "Run Agent Candidate")
        self.assertFalse(row.review_required)

    def test_analysis_phase_awaiting_gate1_review(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        _make_fit_assessment(application, jra)
        row = build_dashboard_row(application)
        self.assertEqual(row.status, "Analysis")
        self.assertTrue(row.fit_assessment_exists)
        self.assertTrue(row.fit_assessment_current)
        self.assertTrue(row.review_required)
        self.assertEqual(row.next_action.label, "Review Gate 1")

    def test_preparation_phase_awaiting_ab(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        _make_fit_assessment(application, jra)
        application.approve_gate1()
        row = build_dashboard_row(application)
        self.assertEqual(row.status, "Preparation")
        self.assertTrue(row.gate1_approved)
        self.assertFalse(row.resume_draft_exists)
        self.assertEqual(row.next_action.label, "Run Agent Builder")

    def test_preparation_phase_awaiting_gate2_review(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        fa = _make_fit_assessment(application, jra)
        application.approve_gate1()
        _make_resume_draft(application, fa)
        row = build_dashboard_row(application)
        self.assertEqual(row.status, "Preparation")
        self.assertTrue(row.review_required)
        self.assertEqual(row.next_action.label, "Review Gate 2")

    def test_ready_phase_not_applied(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        fa = _make_fit_assessment(application, jra)
        application.approve_gate1()
        _make_resume_draft(application, fa)
        application.approve_gate2()
        application.refresh_from_db()
        row = build_dashboard_row(application)
        self.assertEqual(row.status, "Ready")
        self.assertTrue(row.gate2_approved)
        self.assertTrue(row.resume_draft_confirmed)
        self.assertFalse(row.review_required)
        self.assertEqual(row.next_action.code, "FINAL")
        self.assertEqual(
            row.next_action.url,
            reverse("resume_builder:preview", kwargs={"application_id": application.pk}),
        )

    def test_ready_phase_applied_status_overrides_phase_label(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        fa = _make_fit_assessment(application, jra)
        application.approve_gate1()
        _make_resume_draft(application, fa)
        application.approve_gate2()
        set_application_outcome(application, JobApplication.ApplicationOutcome.APPLIED)
        application.refresh_from_db()
        row = build_dashboard_row(application)
        self.assertEqual(row.status, "Applied")


class NextActionResolutionTests(TestCase):
    """Correct next-action resolution (Phase G #2), including the stale-chain redirect cases."""

    def test_stale_fit_assessment_redirects_to_gate1_even_when_ready(self):
        application = JobApplication.objects.create()
        jra_v1 = _make_jra(application, version=1)
        application.advance_to_analysis(jra=jra_v1)
        fa = _make_fit_assessment(application, jra_v1)
        application.approve_gate1()
        _make_resume_draft(application, fa)
        application.approve_gate2()

        # Upstream JRA changes after Gate 2 approval (e.g. a late Gate-1 AJ re-run).
        jra_v2 = _make_jra(application, version=2)
        application.record_jra(jra_v2)

        action = resolve_next_action(application)
        self.assertEqual(action.code, "GATE1")
        self.assertIn("Gate 1", action.label)

    def test_stale_resume_draft_redirects_to_gate2(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        fa1 = _make_fit_assessment(application, jra)
        application.approve_gate1()
        _make_resume_draft(application, fa1)

        # A new FitAssessment version lands without a corresponding new ResumeDraft.
        fa2 = FitAssessment.objects.create(job_application=application, version=2, based_on_jra=jra)
        application.record_fit_assessment(fa2)

        action = resolve_next_action(application)
        self.assertEqual(action.code, "GATE2")
        self.assertIn("Gate 2", action.label)


class ChainWideFreshnessTests(TestCase):
    """Full HITL-007 freshness enforcement across the chain (Phase G #20)."""

    def test_transitive_staleness_detected_even_when_direct_link_is_fresh(self):
        application = JobApplication.objects.create()
        jra_v1 = _make_jra(application, version=1)
        application.advance_to_analysis(jra=jra_v1)
        fa = _make_fit_assessment(application, jra_v1)
        application.approve_gate1()
        _make_resume_draft(application, fa)
        application.approve_gate2()

        jra_v2 = _make_jra(application, version=2)
        application.record_jra(jra_v2)

        freshness = compute_freshness(application)
        self.assertTrue(freshness.fit_assessment_stale)
        self.assertFalse(freshness.draft_stale_direct)  # draft is still in sync with its own FitAssessment
        self.assertTrue(freshness.draft_stale)  # but the chain as a whole is stale

        row = build_dashboard_row(application)
        self.assertTrue(row.is_stale)


class MissingPointerTests(TestCase):
    """Broken/missing pointer handling (Phase G #5)."""

    def test_missing_current_fit_assessment_after_advance_to_analysis(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        row = build_dashboard_row(application)
        self.assertFalse(row.fit_assessment_exists)
        self.assertFalse(row.resume_draft_exists)
        self.assertFalse(row.gate1_approved)
        self.assertFalse(row.gate2_approved)


class ListDashboardRowsIsolationTests(TestCase):
    """Cross-application ownership isolation (Phase G #16): one application's rows/derived state
    never leak into another's."""

    def test_two_applications_are_independent(self):
        app_a = JobApplication.objects.create()
        jra_a = _make_jra(app_a, employer="Acme")
        app_a.advance_to_analysis(jra=jra_a)

        app_b = JobApplication.objects.create()
        jra_b = _make_jra(app_b, employer="Globex")
        app_b.advance_to_analysis(jra=jra_b)
        _make_fit_assessment(app_b, jra_b)
        app_b.approve_gate1()

        rows = {row.application.pk: row for row in list_dashboard_rows()}
        self.assertEqual(rows[app_a.pk].employer, "Acme")
        self.assertFalse(rows[app_a.pk].fit_assessment_exists)
        self.assertEqual(rows[app_b.pk].employer, "Globex")
        self.assertTrue(rows[app_b.pk].fit_assessment_exists)


class ApplicationOutcomeServiceTests(TestCase):
    """Application-outcome updates (#12), preservation across regeneration (#13), invalid
    rejection (#14)."""

    def _ready_application(self) -> JobApplication:
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        fa = _make_fit_assessment(application, jra)
        application.approve_gate1()
        _make_resume_draft(application, fa)
        application.approve_gate2()
        return application

    def test_set_outcome_to_applied(self):
        application = self._ready_application()
        set_application_outcome(application, JobApplication.ApplicationOutcome.APPLIED)
        application.refresh_from_db()
        self.assertEqual(application.application_outcome, JobApplication.ApplicationOutcome.APPLIED)

    def test_rejects_unknown_outcome_value(self):
        application = self._ready_application()
        with self.assertRaises(InvalidOutcomeTransitionError):
            set_application_outcome(application, "NOT_A_REAL_OUTCOME")

    def test_rejects_outcome_before_ready(self):
        application = JobApplication.objects.create()
        jra = _make_jra(application)
        application.advance_to_analysis(jra=jra)
        with self.assertRaises(InvalidOutcomeTransitionError):
            set_application_outcome(application, JobApplication.ApplicationOutcome.APPLIED)
        application.refresh_from_db()
        self.assertEqual(application.application_outcome, JobApplication.ApplicationOutcome.NOT_APPLIED)

    def test_rejects_outcome_when_chain_is_stale(self):
        application = self._ready_application()
        new_jra = _make_jra(application, version=2)
        application.record_jra(new_jra)
        with self.assertRaises(InvalidOutcomeTransitionError):
            set_application_outcome(application, JobApplication.ApplicationOutcome.APPLIED)

    def test_outcome_survives_resume_draft_regeneration(self):
        application = self._ready_application()
        set_application_outcome(application, JobApplication.ApplicationOutcome.APPLIED)
        application.refresh_from_db()

        # Regenerate: a new ResumeDraft version lands (e.g. a later Gate-2 feedback re-run in a
        # fresh review cycle) and Gate 2 is approved again.
        new_draft = ResumeDraft.objects.create(
            job_application=application, version=2,
            based_on_fit_assessment=application.current_fit_assessment,
            recommended_title="Senior Engineer", rendered_markdown="# Senior Engineer\n",
        )
        application.record_resume_draft(new_draft)
        application.approve_gate2()

        application.refresh_from_db()
        self.assertEqual(application.application_outcome, JobApplication.ApplicationOutcome.APPLIED)


class DeriveDashboardStatusTests(TestCase):
    def test_not_applied_uses_phase_label(self):
        application = JobApplication.objects.create()
        self.assertEqual(derive_dashboard_status(application), "New")

    def test_applied_overrides_phase_label(self):
        application = JobApplication.objects.create(
            application_outcome=JobApplication.ApplicationOutcome.INTERVIEWING
        )
        self.assertEqual(derive_dashboard_status(application), "Interviewing")


class DashboardSummaryTests(TestCase):
    """compute_dashboard_summary is derived from the same rows the table renders, so its counts
    can never disagree with what an operator sees in the table (M7 UX follow-up, Phase G)."""

    def test_empty_rows_all_zero(self):
        summary = compute_dashboard_summary([])
        self.assertEqual(summary.total, 0)
        self.assertEqual(summary.not_started, 0)
        self.assertEqual(summary.needs_review, 0)
        self.assertEqual(summary.stale, 0)
        self.assertEqual(summary.ready_deliverable, 0)
        self.assertEqual(summary.outcome_recorded, 0)

    def test_counts_reflect_mixed_pipeline_states(self):
        JobApplication.objects.create()

        needs_review = JobApplication.objects.create()
        jra = _make_jra(needs_review, employer="NeedsReview")
        needs_review.advance_to_analysis(jra=jra)
        _make_fit_assessment(needs_review, jra)

        ready = JobApplication.objects.create()
        ready_jra = _make_jra(ready, employer="ReadyCo")
        ready.advance_to_analysis(jra=ready_jra)
        ready_fa = _make_fit_assessment(ready, ready_jra)
        ready.approve_gate1()
        _make_resume_draft(ready, ready_fa)
        ready.approve_gate2()
        ready.refresh_from_db()
        set_application_outcome(ready, JobApplication.ApplicationOutcome.APPLIED)
        ready.refresh_from_db()

        rows = list_dashboard_rows()
        summary = compute_dashboard_summary(rows)

        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.not_started, 1)
        self.assertEqual(summary.needs_review, 1)
        self.assertEqual(summary.ready_deliverable, 1)
        self.assertEqual(summary.outcome_recorded, 1)
