from django.core.management.base import BaseCommand

from llm_provider.smoke.common import (
    DEFAULT_REASONING_SMOKE_MAX_OUTPUT_TOKENS,
    DEFAULT_SMOKE_MAX_OUTPUT_TOKENS,
    SMOKE_MAX_OUTPUT_TOKENS_CEILING,
)
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
        parser.add_argument(
            "--max-output-tokens",
            type=int,
            default=None,
            help=(
                "Explicit smoke-test output-token budget, overriding the default. Defaults: "
                f"{DEFAULT_SMOKE_MAX_OUTPUT_TOKENS} for a plain request, "
                f"{DEFAULT_REASONING_SMOKE_MAX_OUTPUT_TOKENS} when --reasoning is set (reasoning "
                "tokens consume the same completion-token allowance as the final answer, so the "
                "plain default is not a valid budget for a reasoning-enabled request). Must be a "
                "positive integer, must not exceed the selected model's own max_output_tokens "
                f"capability, and must not exceed the smoke-test safety ceiling of "
                f"{SMOKE_MAX_OUTPUT_TOKENS_CEILING} -- validated before any provider call is made."
            ),
        )

    def handle(self, *args, **options):
        main(
            options["model"],
            reasoning_enabled=options["reasoning"],
            max_output_tokens=options["max_output_tokens"],
        )
