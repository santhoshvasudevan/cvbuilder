from django.core.management.base import BaseCommand

from llm_provider.smoke.run_nvidia import main


class Command(BaseCommand):
    help = (
        "Opt-in manual smoke test for the NVIDIA NIM adapter. Never run automatically. "
        "Requires NVIDIA_NIM_API_KEY in the environment."
    )

    def handle(self, *args, **options):
        main()
