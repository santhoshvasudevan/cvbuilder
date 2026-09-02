"""Candidate Memory UI: authorization by revision state (mutable vs read-only), POST-only/CSRF-
safe state-changing actions, filters, and the add/update-profile + snapshot-export entry points.
No browser automation -- Django's test Client only, per M3's explicit test-suite constraints."""

from __future__ import annotations

from pathlib import Path

from django.test import Client, TestCase
from django.urls import reverse

from ..models import CandidateMemory, MemoryClaim, MemoryClaimSupport, MemoryConflict
from .factories import freeze_revision, make_claim, make_revision, make_source, scripted_extraction

_ONE_ITEM_RESPONSE = {
    "items": [
        {
            "plane": "EVIDENCE",
            "canonical_text_en": "Delivered a new engagement.",
            "support": {
                "quote": "Delivered a new engagement.", "start_line": 1, "end_line": 1, "language": "en",
            },
            "claim_type": "employment",
            "subject_scope": "Some Employer",
            "resume_eligible": True,
        }
    ]
}


class OverviewAndRevisionDetailTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def test_overview_renders_with_no_revisions(self):
        resp = self.client.get(reverse("candidate_memory:overview"))
        self.assertEqual(resp.status_code, 200)

    def test_overview_shows_active_and_working_revisions(self):
        active = make_revision(status=CandidateMemory.Status.ACTIVE, version=1)
        working = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW, version=2)
        resp = self.client.get(reverse("candidate_memory:overview"))
        self.assertContains(resp, f"v{active.version}")
        self.assertContains(resp, f"v{working.version}")

    def test_revision_detail_renders(self):
        rev = make_revision()
        resp = self.client.get(reverse("candidate_memory:revision_detail", kwargs={"version": rev.version}))
        self.assertEqual(resp.status_code, 200)

    def test_unknown_revision_is_404(self):
        resp = self.client.get(reverse("candidate_memory:revision_detail", kwargs={"version": 999999}))
        self.assertEqual(resp.status_code, 404)


class ClaimsListFilterTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.rev = make_revision()
        self.claim_a = make_claim(
            self.rev, subject_scope="Ford Motor Company", claim_type="employment",
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED, resume_eligible=True,
        )
        self.claim_b = make_claim(
            self.rev, subject_scope="Continental", claim_type="skill",
            confirmation_status=MemoryClaim.ConfirmationStatus.UNCONFIRMED, resume_eligible=False,
        )

    def _list_url(self, **params):
        url = reverse("candidate_memory:claims_list", kwargs={"version": self.rev.version})
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def test_no_filter_shows_all(self):
        resp = self.client.get(self._list_url())
        self.assertContains(resp, self.claim_a.claim_id)
        self.assertContains(resp, self.claim_b.claim_id)

    def test_filter_by_subject_scope(self):
        resp = self.client.get(self._list_url(q="Ford"))
        self.assertContains(resp, self.claim_a.claim_id)
        self.assertNotContains(resp, self.claim_b.claim_id)

    def test_filter_by_confirmation_status(self):
        resp = self.client.get(self._list_url(confirmation_status="CONFIRMED"))
        self.assertContains(resp, self.claim_a.claim_id)
        self.assertNotContains(resp, self.claim_b.claim_id)

    def test_filter_by_eligibility(self):
        resp = self.client.get(self._list_url(eligible="yes"))
        self.assertContains(resp, self.claim_a.claim_id)
        self.assertNotContains(resp, self.claim_b.claim_id)

    def test_filter_by_claim_type(self):
        resp = self.client.get(self._list_url(claim_type="skill"))
        self.assertContains(resp, self.claim_b.claim_id)
        self.assertNotContains(resp, self.claim_a.claim_id)


class ClaimActionAuthorizationTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def _confirm_url(self, rev, claim):
        return reverse(
            "candidate_memory:claim_confirm", kwargs={"version": rev.version, "claim_id": claim.claim_id}
        )

    def test_get_on_state_changing_action_is_405(self):
        rev = make_revision()
        claim = make_claim(rev)
        resp = self.client.get(self._confirm_url(rev, claim))
        self.assertEqual(resp.status_code, 405)

    def test_post_without_csrf_token_is_rejected(self):
        rev = make_revision()
        claim = make_claim(rev)
        resp = self.client.post(self._confirm_url(rev, claim))
        self.assertEqual(resp.status_code, 403)

    def test_post_with_csrf_token_confirms_claim_on_mutable_revision(self):
        client = Client()  # CSRF checks disabled by default -- exercises the happy path
        rev = make_revision()
        claim = make_claim(rev)
        resp = client.post(self._confirm_url(rev, claim))
        self.assertEqual(resp.status_code, 302)
        claim.refresh_from_db()
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.CONFIRMED)

    def test_post_on_active_revision_is_refused_and_does_not_change_claim(self):
        client = Client()
        rev = make_revision()
        claim = make_claim(rev)
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        resp = client.post(self._confirm_url(rev, claim))
        self.assertEqual(resp.status_code, 302)  # redirects back with an error message, not a 500
        claim.refresh_from_db()
        self.assertEqual(claim.confirmation_status, MemoryClaim.ConfirmationStatus.UNCONFIRMED)


class ConflictInboxActionTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_resolve_conflict_via_post(self):
        rev = make_revision()
        winner = make_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT)
        loser = make_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.BLOCKED_CONFLICT)
        conflict = MemoryConflict.objects.create(candidate_memory=rev, conflict_key="k", description="d")
        conflict.involved_claims.set([winner, loser])

        url = reverse(
            "candidate_memory:conflict_resolve", kwargs={"version": rev.version, "conflict_id": conflict.pk}
        )
        resp = self.client.post(
            url, data={"resolved_claim_id": winner.claim_id, "resolution_note": "chose winner"}
        )
        self.assertEqual(resp.status_code, 302)
        conflict.refresh_from_db()
        loser.refresh_from_db()
        self.assertEqual(conflict.status, MemoryConflict.Status.RESOLVED)
        self.assertEqual(loser.confirmation_status, MemoryClaim.ConfirmationStatus.RETIRED)


class ActivationFlowTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_activate_confirm_shows_blockers(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        make_claim(rev, confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED, resume_eligible=True)
        resp = self.client.get(reverse("candidate_memory:activate_confirm", kwargs={"version": rev.version}))
        self.assertContains(resp, "no MemoryClaimSupport")

    def test_activate_post_succeeds_when_no_blockers(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        source = make_source(rev, raw_content="Built the Ford integration.\n")
        claim = make_claim(
            rev, canonical_text_en="Built the Ford integration.", resume_eligible=True,
            confirmation_status=MemoryClaim.ConfirmationStatus.CONFIRMED,
        )
        MemoryClaimSupport.objects.create(
            memory_claim=claim, memory_source_document=source,
            quotation="Built the Ford integration.", start_line=1, end_line=1,
            source_language="en", support_role=MemoryClaimSupport.SupportRole.PRIMARY,
        )
        resp = self.client.post(reverse("candidate_memory:activate", kwargs={"version": rev.version}))
        self.assertEqual(resp.status_code, 302)
        rev.refresh_from_db()
        self.assertEqual(rev.status, CandidateMemory.Status.ACTIVE)

    def test_activate_get_is_405(self):
        rev = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        resp = self.client.get(reverse("candidate_memory:activate", kwargs={"version": rev.version}))
        self.assertEqual(resp.status_code, 405)


class UpdateProfileFormTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_get_renders_form(self):
        resp = self.client.get(reverse("candidate_memory:update_profile_form"))
        self.assertEqual(resp.status_code, 200)

    def test_post_creates_new_revision(self):
        with scripted_extraction(_ONE_ITEM_RESPONSE):
            resp = self.client.post(
                reverse("candidate_memory:update_profile_form"),
                data={"text": "Delivered a new engagement.", "context": "New role"},
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            CandidateMemory.objects.filter(status=CandidateMemory.Status.NEEDS_REVIEW).count(), 1
        )


class SnapshotExportViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_export_with_no_active_revision_shows_error_not_500(self):
        resp = self.client.post(reverse("candidate_memory:snapshot_export"))
        self.assertEqual(resp.status_code, 302)

    def test_export_with_active_revision_writes_file(self):
        """Never let this test touch the real docs/CANDIDATE_MEMORY_SNAPSHOT.md -- patch the
        view's export function to write to a throwaway path instead."""
        from unittest import mock

        make_revision(status=CandidateMemory.Status.ACTIVE)
        target = Path("view_export_test_snapshot.md")
        self.addCleanup(lambda: target.unlink(missing_ok=True))

        with mock.patch("candidate_memory.views.export_snapshot", return_value=target) as mocked:
            resp = self.client.post(reverse("candidate_memory:snapshot_export"))
        mocked.assert_called_once_with()
        self.assertEqual(resp.status_code, 302)

    def test_export_get_is_405(self):
        resp = self.client.get(reverse("candidate_memory:snapshot_export"))
        self.assertEqual(resp.status_code, 405)
