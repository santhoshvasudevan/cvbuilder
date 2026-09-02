"""Explicit snapshot export/regeneration (D-015): deterministic generation from the active
PostgreSQL Candidate Memory revision. Never runs automatically; never mutates Candidate Memory.

    python manage.py export_candidate_memory_snapshot
    python manage.py export_candidate_memory_snapshot --output /tmp/snapshot.md
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from candidate_memory.services.snapshot_export import NoActiveRevisionError, export_snapshot


class Command(BaseCommand):
    help = "Regenerate the Candidate Memory snapshot from the active revision. Never run automatically."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output", default=None,
            help="Override the output path (default: docs/CANDIDATE_MEMORY_SNAPSHOT.md).",
        )

    def handle(self, *args, **options):
        try:
            path = export_snapshot(options["output"])
        except NoActiveRevisionError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Snapshot written to {path}"))
