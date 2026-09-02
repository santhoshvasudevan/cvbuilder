from django.core.management.base import BaseCommand

from llm_provider.smoke.run_gemini import main


class Command(BaseCommand):
    help = (
        "Opt-in manual smoke test for the Gemini adapter. Never run automatically. "
        "Requires GEMINI_API_KEY in the environment."
    )

    def handle(self, *args, **options):
        main()
