"""Opt-in manual smoke test for the OpenRouter adapter. Run via:

    python manage.py smoke_test_openrouter
    python manage.py smoke_test_openrouter --model z-ai/glm-5.2:free
    python manage.py smoke_test_openrouter --model z-ai/glm-5.2:free --reasoning

Never invoked automatically. Requires OPENROUTER_API_KEY in the environment. `--reasoning` sends
an explicitly reasoning-enabled request -- only pass it against a model registered with
`supports_reasoning=True`, since the adapter rejects an enabled-reasoning request against a model
that isn't (see `llm_provider/adapters/openrouter.py`).
"""

from ..models import LLMProvider
from .common import run_smoke_test


def main(model_id: str | None = None, reasoning_enabled: bool = False) -> None:
    run_smoke_test(
        LLMProvider.ProviderType.OPENROUTER,
        model_id,
        reasoning_enabled=reasoning_enabled or None,
    )
