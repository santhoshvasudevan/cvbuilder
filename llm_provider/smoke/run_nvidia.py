"""Opt-in manual smoke test for the NVIDIA NIM adapter. Run via:

    python manage.py smoke_test_nvidia
    python manage.py smoke_test_nvidia --model nvidia/nemotron-3-super-120b-a12b

Never invoked automatically. Requires NVIDIA_NIM_API_KEY in the environment.
"""

from ..models import LLMProvider
from .common import run_smoke_test

# NVIDIA's own guidance for its Nemotron reasoning models recommends temperature=1.0 (vs. this
# harness's 0.0 default for the other providers) -- applied only to this one-off smoke call, not
# to the M3 pipeline's own (separately, deliberately deterministic) extraction requests.
_RECOMMENDED_TEMPERATURE = 1.0


def main(model_id: str | None = None) -> None:
    run_smoke_test(LLMProvider.ProviderType.NVIDIA_NIM, model_id, temperature=_RECOMMENDED_TEMPERATURE)
