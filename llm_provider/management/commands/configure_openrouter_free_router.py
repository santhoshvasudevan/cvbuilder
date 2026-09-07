"""Idempotent registry configuration for the OpenRouter Free Models Router migration (D-038).

    python manage.py configure_openrouter_free_router --dry-run
    python manage.py configure_openrouter_free_router

Ensures an OpenRouter LLMProvider row and an active `openrouter/free` LLMModel row exist with
truthful, conservative capability flags; deactivates (never deletes) the retired Z.ai/GLM model;
and reassigns whichever StageModelAssignment row(s) currently point at it to the free router
instead. Every other provider/model/stage assignment (NVIDIA, OpenAI, Gemini, FAKE, and any stage
not currently on the Z.ai model) is left untouched. Safe to run repeatedly. Never run
automatically -- not on migrate, runserver, or manage.py test.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from llm_provider.services.openrouter_free_router import configure_openrouter_free_router


class Command(BaseCommand):
    help = (
        "Idempotently configure the OpenRouter registry to use the Free Models Router "
        "('openrouter/free') in place of the retired Z.ai/GLM model for whichever stage(s) "
        "currently use it. Never run automatically."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        report = configure_openrouter_free_router(dry_run=options["dry_run"])
        self.stdout.write(report.describe())
