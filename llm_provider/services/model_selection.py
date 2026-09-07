"""Per-run model-selection resolution (2026-09-07): the one place that decides which `LLMModel`
a single stage invocation actually uses, with a fixed precedence and no hidden fallback:

1. a valid, eligible, explicitly-selected model for *this run* (an operator override submitted
   through the AJ/AC/AB UI);
2. otherwise the stage's configured `StageModelAssignment` (the global default);
3. otherwise fail closed with a typed, actionable error -- never a hard-coded provider/model.

`llm_provider.adapters.get_adapter_for_stage` is the only caller in production code; it is a thin
wrapper that also applies `StageModelAssignment.max_output_tokens`/`read_timeout_seconds` and the
existing inactive/FAKE-provider guards on top of whatever this module resolves. Kept as a separate
module (rather than folding this logic into `adapters/__init__.py` directly) so the AJ/AC/AB views
can call `resolve_stage_model` themselves to validate a submitted selection *before* redirecting
into a service call, producing a form-level error instead of a raised exception surfacing as a
500.
"""

from __future__ import annotations

import dataclasses

from ..models import LLMModel, StageModelAssignment
from .eligibility import eligible_models_for_stage, model_display_label


class NoStageDefaultConfiguredError(Exception):
    """Raised when no explicit per-run selection was given and the stage has no
    `StageModelAssignment` row at all -- a genuine configuration gap, never silently routed
    anywhere. Fix via `manage.py configure_openrouter_free_router` or the registry admin."""


class ModelNotEligibleForStageError(Exception):
    """Raised when an explicit per-run selection does not resolve to a model
    `llm_provider.services.eligibility.eligible_models_for_stage(stage)` currently allows -- e.g.
    the model id does not exist, belongs to an inactive provider/model, lacks the stage's required
    capability, or has since been deactivated between when the form was rendered and submitted.
    Always raised before any provider call."""


@dataclasses.dataclass(frozen=True)
class StageModelSelection:
    model: LLMModel
    source: str  # LLMCallLog.SelectionSource.DEFAULT or .OVERRIDE
    assignment: StageModelAssignment | None


def resolve_stage_model(
    stage: str, *, requested_model_id: int | None = None
) -> StageModelSelection:
    """Implements the three-step precedence documented in this module's docstring.

    `requested_model_id` is an `LLMModel` primary key (not a provider `model_id` string) -- the
    AJ/AC/AB forms submit the eligible model's numeric id, matching how the selector's `<option
    value>` is rendered, so a value that was valid when the page was rendered but has since become
    ineligible (e.g. deactivated) is still validated fresh here, not trusted from the form.
    """
    from ..models import LLMCallLog  # local import: avoids a module-level circular import with models

    if requested_model_id is not None:
        model = eligible_models_for_stage(stage).filter(pk=requested_model_id).first()
        if model is None:
            raise ModelNotEligibleForStageError(
                f"The selected model (id={requested_model_id}) is not eligible for stage "
                f"{stage!r} -- it may not exist, may belong to an inactive provider/model, or may "
                "lack a capability this stage requires. Choose a currently-eligible model, or "
                "leave the selector on the system default."
            )
        assignment = (
            StageModelAssignment.objects.select_related("model__provider").filter(stage=stage).first()
        )
        return StageModelSelection(
            model=model, source=LLMCallLog.SelectionSource.OVERRIDE, assignment=assignment
        )

    try:
        assignment = StageModelAssignment.objects.select_related("model__provider").get(stage=stage)
    except StageModelAssignment.DoesNotExist as exc:
        raise NoStageDefaultConfiguredError(
            f"Stage {stage!r} has no StageModelAssignment configured and no explicit model was "
            "selected for this run -- there is no implicit fallback. Run `manage.py "
            "configure_openrouter_free_router` (sets every stage's default to OpenRouter's Free "
            "Models Router) or assign a model to this stage via the registry admin."
        ) from exc
    return StageModelSelection(
        model=assignment.model, source=LLMCallLog.SelectionSource.DEFAULT, assignment=assignment
    )


def parse_requested_model_id(raw: str | None) -> int | None:
    """Parses one AJ/AC/AB model-selector's raw POSTed value into the `LLMModel` primary key
    `resolve_stage_model`/`get_adapter_for_stage` expect, or `None` for "system default" (an
    empty/missing selection -- the selector's own blank/default option). Every view that accepts
    a per-run override uses this one function so a garbled or tampered form value always produces
    the same actionable `ModelNotEligibleForStageError` rather than an unhandled `ValueError`
    surfacing as a 500."""
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ModelNotEligibleForStageError(
            f"{raw!r} is not a valid model selection -- choose an option from the list, or leave "
            "it on the system default."
        ) from exc


def describe_selection_error_choices(stage: str) -> str:
    """Human-readable, comma-joined list of currently-eligible models for `stage` -- used to make
    a `ModelNotEligibleForStageError`/`NoStageDefaultConfiguredError` message actionable in a UI
    error rather than just naming the problem."""
    labels = [model_display_label(model) for model in eligible_models_for_stage(stage)]
    return ", ".join(labels) if labels else "(none currently eligible)"
