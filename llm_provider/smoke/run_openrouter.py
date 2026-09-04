"""Opt-in manual smoke test for the OpenRouter adapter. Run via:

    python manage.py smoke_test_openrouter
    python manage.py smoke_test_openrouter --model z-ai/glm-5.2:free
    python manage.py smoke_test_openrouter --model z-ai/glm-5.2:free --reasoning
    python manage.py smoke_test_openrouter --reasoning --max-output-tokens 4096

Never invoked automatically. Requires OPENROUTER_API_KEY in the environment. `--reasoning` sends
an explicitly reasoning-enabled request -- only pass it against a model registered with
`supports_reasoning=True`, since the adapter rejects an enabled-reasoning request against a model
that isn't (see `llm_provider/adapters/openrouter.py`). `--max-output-tokens` overrides the
default smoke-test output-token budget (D-026): 64 for a plain request, 4096 when `--reasoning` is
set, since reasoning tokens consume the same completion allowance as the final answer -- see
`llm_provider/smoke/common.py`'s `resolve_smoke_max_output_tokens` for the exact validation rules.
"""

from ..models import LLMProvider
from .common import run_smoke_test


def main(
    model_id: str | None = None,
    reasoning_enabled: bool = False,
    max_output_tokens: int | None = None,
) -> None:
    run_smoke_test(
        LLMProvider.ProviderType.OPENROUTER,
        model_id,
        reasoning_enabled=reasoning_enabled or None,
        max_output_tokens=max_output_tokens,
    )
