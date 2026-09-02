"""Opt-in manual smoke test for the OpenAI adapter. Run via:

    python manage.py smoke_test_openai
    python manage.py smoke_test_openai --model gpt-4o-mini

Never invoked automatically. Requires OPENAI_API_KEY in the environment.
"""

from ..models import LLMProvider
from .common import run_smoke_test


def main(model_id: str | None = None) -> None:
    run_smoke_test(LLMProvider.ProviderType.OPENAI, model_id)
