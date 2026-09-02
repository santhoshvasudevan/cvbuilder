from django.core.management.base import BaseCommand

from llm_provider.smoke.run_nvidia import main


class Command(BaseCommand):
    help = (
        "Opt-in manual smoke test for the NVIDIA NIM adapter. Never run automatically. "
        "Requires NVIDIA_NIM_API_KEY in the environment."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            default=None,
            help=(
                "Explicit NVIDIA NIM model id to test. If omitted, uses the model assigned to "
                "MEMORY_BUILD for this provider, or the sole registered structured-output-"
                "capable model if unambiguous; fails clearly (no provider call) otherwise."
            ),
        )

    def handle(self, *args, **options):
        main(options["model"])
