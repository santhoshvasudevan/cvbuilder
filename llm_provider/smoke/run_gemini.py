"""Opt-in manual smoke test for the Gemini adapter. Run via:

    python manage.py smoke_test_gemini

Never invoked automatically. Requires GEMINI_API_KEY in the environment.
"""

from ..models import LLMProvider
from .common import run_smoke_test


def main() -> None:
    run_smoke_test(LLMProvider.ProviderType.GEMINI)
