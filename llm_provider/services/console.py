"""Manual operator call console (docs/IMPLEMENTATION_PLAN.md M2 acceptance: "operator can run a
stage manually"; requirements.md LLM-010/LLM-011 model comparison). This is the service layer a
Django admin action or a future UI calls -- it never performs live provider calls on its own; it
routes through `llm_provider.adapters`, which is where any real HTTP would happen.

No function here ever selects a different provider/model than the one explicitly requested --
there is no automatic fallback anywhere in this module (docs/IMPLEMENTATION_PLAN.md M2 scope
boundary). A failed call returns a `NormalizedLLMResult` with `.error` set; it is never silently
retried against another model.
"""

from __future__ import annotations

from ..adapters import get_adapter_for_model, get_adapter_for_stage
from ..models import LLMModel
from ..types import NormalizedLLMRequest, NormalizedLLMResult


def run_stage_manually(stage: str, request: NormalizedLLMRequest, *, stage_run=None) -> NormalizedLLMResult:
    """Execute the currently-assigned model for `stage` against `request` (UI-005: "the operator
    can explicitly click to run each major LLM call"). Raises `StageModelAssignment.DoesNotExist`
    or a `ConfigurationError` subclass if the stage has no valid, active assignment -- both
    before any HTTP call.
    """
    adapter = get_adapter_for_stage(stage, stage_run=stage_run)
    return adapter.generate(request)


def run_with_model_override(
    model: LLMModel, request: NormalizedLLMRequest, *, stage_run=None
) -> NormalizedLLMResult:
    """Execute an explicit model override for one call without changing the stage's stored
    default (LLM-006 runtime override). Configuration is still validated before any HTTP call --
    `get_adapter_for_model` does not skip pre-flight validation, `generate()` runs it.
    """
    adapter = get_adapter_for_model(model, stage_run=stage_run)
    return adapter.generate(request)


def compare_models(
    calls: list[tuple[LLMModel, NormalizedLLMRequest]], *, stage_run=None
) -> dict[int, NormalizedLLMResult]:
    """Model comparison on identical stored input (LLM-010/011): run each `(model, request)` pair
    independently and return one `NormalizedLLMResult` per `model.id`. Each call is fully
    independent -- one model's failure never affects, retries, or substitutes another model's
    call, and every call writes its own `LLMCallLog` row.
    """
    results: dict[int, NormalizedLLMResult] = {}
    for model, request in calls:
        adapter = get_adapter_for_model(model, stage_run=stage_run)
        results[model.id] = adapter.generate(request)
    return results
