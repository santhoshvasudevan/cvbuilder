from django.core.management.base import BaseCommand

from llm_provider.smoke.run_openai import main


class Command(BaseCommand):
    help = (
        "Opt-in manual smoke test for the OpenAI adapter. Never run automatically. "
        "Requires OPENAI_API_KEY in the environment."
    )

    def handle(self, *args, **options):
        main()
