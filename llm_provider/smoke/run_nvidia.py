"""Opt-in manual smoke test for the NVIDIA NIM adapter. Run via:

    python manage.py smoke_test_nvidia

Never invoked automatically. Requires NVIDIA_NIM_API_KEY in the environment.
"""

from ..models import LLMProvider
from .common import run_smoke_test


def main() -> None:
    run_smoke_test(LLMProvider.ProviderType.NVIDIA_NIM)
