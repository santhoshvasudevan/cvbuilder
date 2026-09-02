"""Opt-in manual smoke test for the OpenAI adapter. Run via:

    python manage.py smoke_test_openai

Never invoked automatically. Requires OPENAI_API_KEY in the environment.
"""

from ..models import LLMProvider
from .common import run_smoke_test


def main() -> None:
    run_smoke_test(LLMProvider.ProviderType.OPENAI)
