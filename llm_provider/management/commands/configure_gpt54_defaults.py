"""Idempotent registry configuration for the paid GPT-5.4 model defaults (D-039).

    python manage.py configure_gpt54_defaults --dry-run
    python manage.py configure_gpt54_defaults

Ensures an OpenRouter LLMProvider row and active `openai/gpt-5.4-mini`/`openai/gpt-5.4` LLMModel
rows exist with truthful capability flags, and points every currently implemented
StageModelAssignment at the operator-approved default matrix (extraction/analysis stages ->
GPT-5.4 Mini at medium reasoning; matching/ranking/writing stages -> GPT-5.4 at high/medium
reasoning). Never touches `openrouter/free`, NVIDIA/Gemini models, or the retired Z.ai/GLM row --
those remain exactly as configured, selectable per-run alternatives. Safe to run repeatedly. Never
run automatically -- not on migrate, runserver, or manage.py test.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from llm_provider.services.gpt54_defaults import configure_gpt54_defaults


class Command(BaseCommand):
    help = (
        "Idempotently register OpenRouter's paid GPT-5.4 Mini/GPT-5.4 models and set them as the "
        "default model+reasoning-effort for every currently implemented LLM pipeline stage "
        "(D-039). Never run automatically."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        report = configure_gpt54_defaults(dry_run=options["dry_run"])
        self.stdout.write(report.describe())
