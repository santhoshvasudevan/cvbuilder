"""Operator entry point for the deterministic claim<->engagement mapping proposal (D-019). Never
runs automatically; makes no LLM call; only ever creates `ClaimEngagementMapping` rows with
`status=PROPOSED` via the existing tested `services.engagement_mapping.
propose_claim_engagement_mappings` -- never auto-approves anything, and never touches a
`MemoryClaim` row.

    python manage.py propose_claim_engagement_mappings --candidate-memory-id 7
    python manage.py propose_claim_engagement_mappings --candidate-memory-id 7 --dry-run
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from candidate_memory.models import CandidateMemory
from candidate_memory.services.engagement_mapping import propose_claim_engagement_mappings


class Command(BaseCommand):
    help = (
        "Propose deterministic ClaimEngagementMapping rows (status=PROPOSED only, never "
        "auto-approved) for one CandidateMemory revision. Uses the existing tested "
        "services.engagement_mapping.propose_claim_engagement_mappings -- no LLM call, no source "
        "re-extraction, no raw SQL. Idempotent: rerunning never duplicates an existing mapping."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--candidate-memory-id", type=int, required=True,
            help="Primary key of the CandidateMemory revision to propose mappings for.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be proposed without writing anything. Runs the real "
            "proposal logic inside a database transaction that is always rolled back, rather "
            "than a separate/duplicated preview code path.",
        )

    def handle(self, *args, **options):
        try:
            candidate_memory = CandidateMemory.objects.get(pk=options["candidate_memory_id"])
        except CandidateMemory.DoesNotExist as exc:
            raise CommandError(
                f"No CandidateMemory with id={options['candidate_memory_id']} exists."
            ) from exc

        with transaction.atomic():
            summary = propose_claim_engagement_mappings(candidate_memory)
            if options["dry_run"]:
                transaction.set_rollback(True)

        self.stdout.write(f"CandidateMemory: id={candidate_memory.pk} version={candidate_memory.version}")
        self.stdout.write(f"Claims considered: {summary.claims_considered}")
        self.stdout.write(f"Proposed: {summary.proposed}")
        self.stdout.write(f"Already existing (idempotent skip): {summary.already_mapped}")
        self.stdout.write(f"Ambiguous (2+ candidate engagements): {summary.ambiguous}")
        self.stdout.write(f"Unresolved/unmapped (no matching APPROVED engagement): {summary.unresolved}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("--dry-run: no mappings were actually written."))
        else:
            self.stdout.write(self.style.SUCCESS("Mappings written (status=PROPOSED only)."))
