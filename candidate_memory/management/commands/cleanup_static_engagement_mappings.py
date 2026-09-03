"""Operator entry point for retroactively cleaning up `ClaimEngagementMapping` rows that predate
the static/narrative boundary refinement (D-019, 2026-09-03): a mapping whose claim's `claim_type`
is in `services.engagement_mapping.STATIC_ENGAGEMENT_CLAIM_TYPES` is now obsolete -- that claim's
facts are owned by its `career_engagement` directly -- and should be rejected, regardless of its
current status (including an already-`APPROVED` one from before this refinement).

Defaults to a read-only report; nothing is written unless `--apply` is given.

    python manage.py cleanup_static_engagement_mappings --candidate-memory-id 7
    python manage.py cleanup_static_engagement_mappings --candidate-memory-id 7 --apply
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from candidate_memory.models import CandidateMemory
from candidate_memory.services.engagement_mapping import find_mappings_needing_review, reject_mapping


class Command(BaseCommand):
    help = (
        "Report (or, with --apply, reject) ClaimEngagementMapping rows whose claim is a static "
        "engagement claim type (owned by CareerEngagement, not narrative evidence). Read-only by "
        "default -- no mapping status is changed unless --apply is given. No LLM call, no raw SQL."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--candidate-memory-id", type=int, required=True,
            help="Primary key of the CandidateMemory revision whose mappings to review.",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually reject the static mappings listed below. Without this flag, only a "
            "report is printed and nothing is written.",
        )

    def handle(self, *args, **options):
        try:
            candidate_memory = CandidateMemory.objects.get(pk=options["candidate_memory_id"])
        except CandidateMemory.DoesNotExist as exc:
            raise CommandError(
                f"No CandidateMemory with id={options['candidate_memory_id']} exists."
            ) from exc

        report = find_mappings_needing_review(candidate_memory)

        self.stdout.write(f"CandidateMemory: id={candidate_memory.pk} version={candidate_memory.version}")
        self.stdout.write(f"\nStatic mappings to reject ({len(report.to_reject)}):")
        for mapping in report.to_reject:
            claim = mapping.memory_claim
            self.stdout.write(
                f"  {claim.claim_id} ({claim.claim_type}) -> {mapping.career_engagement.engagement_id} "
                f"[currently {mapping.status}]"
            )
        if not report.to_reject:
            self.stdout.write("  (none)")

        self.stdout.write(f"\nNarrative mappings to retain ({len(report.to_retain)}):")
        for mapping in report.to_retain:
            claim = mapping.memory_claim
            self.stdout.write(
                f"  {claim.claim_id} ({claim.claim_type}) -> {mapping.career_engagement.engagement_id} "
                f"[{mapping.status}]"
            )
        if not report.to_retain:
            self.stdout.write("  (none)")

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    "\n--apply not given: no mapping statuses were changed. Re-run with --apply to "
                    "reject the static mappings listed above."
                )
            )
            return

        with transaction.atomic():
            for mapping in report.to_reject:
                reject_mapping(mapping)
        self.stdout.write(self.style.SUCCESS(f"\nRejected {len(report.to_reject)} static mapping(s)."))
