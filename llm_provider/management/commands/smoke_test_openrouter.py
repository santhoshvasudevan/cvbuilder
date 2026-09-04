from django.core.management.base import BaseCommand

from llm_provider.smoke.run_openrouter import main


class Command(BaseCommand):
    help = (
        "Opt-in manual smoke test for the OpenRouter adapter. Never run automatically. "
        "Requires OPENROUTER_API_KEY in the environment."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            default=None,
            help=(
                "Explicit OpenRouter model slug to test (e.g. z-ai/glm-5.2:free). If omitted, "
                "uses the model assigned to MEMORY_BUILD for this provider, or the sole "
                "registered structured-output-capable model if unambiguous; fails clearly (no "
                "provider call) otherwise."
            ),
        )
        parser.add_argument(
            "--reasoning",
            action="store_true",
            default=False,
            help=(
                "Send an explicitly reasoning-enabled request. Only use against a model "
                "registered with supports_reasoning=True -- the adapter rejects an "
                "enabled-reasoning request against a model that isn't."
            ),
        )

    def handle(self, *args, **options):
        main(options["model"], reasoning_enabled=options["reasoning"])
