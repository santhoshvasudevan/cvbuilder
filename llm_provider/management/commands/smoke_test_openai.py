from django.core.management.base import BaseCommand

from llm_provider.smoke.run_openai import main


class Command(BaseCommand):
    help = (
        "Opt-in manual smoke test for the OpenAI adapter. Never run automatically. "
        "Requires OPENAI_API_KEY in the environment."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            default=None,
            help=(
                "Explicit OpenAI model id to test. If omitted, uses the model assigned to "
                "MEMORY_BUILD for this provider, or the sole registered structured-output-"
                "capable model if unambiguous; fails clearly (no provider call) otherwise."
            ),
        )

    def handle(self, *args, **options):
        main(options["model"])
