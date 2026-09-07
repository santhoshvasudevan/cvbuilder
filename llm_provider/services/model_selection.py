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

from ..models import LLMModel, ReasoningEffort, StageModelAssignment
from .eligibility import eligible_models_for_stage, model_display_label


class NoStageDefaultConfiguredError(Exception):
    """Raised when no explicit per-run selection was given and the stage has no
    `StageModelAssignment` row at all -- a genuine configuration gap, never silently routed
    anywhere. Fix via `manage.py configure_gpt54_defaults` or the registry admin."""


class ModelNotEligibleForStageError(Exception):
    """Raised when an explicit per-run selection does not resolve to a model
    `llm_provider.services.eligibility.eligible_models_for_stage(stage)` currently allows -- e.g.
    the model id does not exist, belongs to an inactive provider/model, lacks the stage's required
    capability, or has since been deactivated between when the form was rendered and submitted.
    Always raised before any provider call."""


class ReasoningNotEligibleForStageError(Exception):
    """Raised when a resolved reasoning-effort request (an explicit per-run override, or the
    stage's own configured `default_reasoning_effort`) is not one this run's resolved model can
    honor -- i.e. the model is not registered `supports_reasoning=True` (2026-09-07, paid GPT-5.4
    model defaults). Always raised before any provider call, mirroring
    `ModelNotEligibleForStageError`'s own timing guarantee."""


@dataclasses.dataclass(frozen=True)
class StageModelSelection:
    model: LLMModel
    source: str  # LLMCallLog.SelectionSource.DEFAULT or .OVERRIDE
    assignment: StageModelAssignment | None
    reasoning_effort: str | None  # a `ReasoningEffort` value, or None ("say nothing")


def resolve_stage_model(
    stage: str,
    *,
    requested_model_id: int | None = None,
    requested_reasoning_effort: str | None = None,
) -> StageModelSelection:
    """Implements the three-step precedence documented in this module's docstring, for both the
    model and the reasoning effort independently: an explicit per-run value wins for whichever of
    the two was actually submitted; whichever was left unsubmitted falls back to the stage's own
    configured default (`StageModelAssignment.model`/`.default_reasoning_effort`) -- an operator
    may override just the model, just the reasoning effort, or both, in one run. The resolved
    reasoning effort is always re-validated against whichever model was actually resolved (never
    the model the stage's default assumed), raising `ReasoningNotEligibleForStageError` if that
    model cannot honor it -- this can happen even when neither value was itself invalid on its own
    (e.g. an operator overrides the model to a non-reasoning one while a reasoning stage default is
    still configured).

    `requested_model_id` is an `LLMModel` primary key (not a provider `model_id` string) -- the
    AJ/AC/AB forms submit the eligible model's numeric id, matching how the selector's `<option
    value>` is rendered, so a value that was valid when the page was rendered but has since become
    ineligible (e.g. deactivated) is still validated fresh here, not trusted from the form.
    `requested_reasoning_effort`, when given, must be one of the five `ReasoningEffort` values (use
    `parse_requested_reasoning_effort` to turn a raw form value into this, exactly like
    `parse_requested_model_id` does for the model).
    """
    from ..models import LLMCallLog  # local import: avoids a module-level circular import with models

    assignment = (
        StageModelAssignment.objects.select_related("model__provider").filter(stage=stage).first()
    )

    if requested_model_id is not None:
        model = eligible_models_for_stage(stage).filter(pk=requested_model_id).first()
        if model is None:
            raise ModelNotEligibleForStageError(
                f"The selected model (id={requested_model_id}) is not eligible for stage "
                f"{stage!r} -- it may not exist, may belong to an inactive provider/model, or may "
                "lack a capability this stage requires. Choose a currently-eligible model, or "
                "leave the selector on the system default."
            )
        source = LLMCallLog.SelectionSource.OVERRIDE
    else:
        if assignment is None:
            raise NoStageDefaultConfiguredError(
                f"Stage {stage!r} has no StageModelAssignment configured and no explicit model was "
                "selected for this run -- there is no implicit fallback. Run `manage.py "
                "configure_gpt54_defaults` (sets every stage's default model/reasoning) or assign "
                "a model to this stage via the registry admin."
            )
        model = assignment.model
        source = LLMCallLog.SelectionSource.DEFAULT

    if requested_reasoning_effort is not None:
        reasoning_effort = requested_reasoning_effort or None
    else:
        reasoning_effort = (assignment.default_reasoning_effort or None) if assignment else None

    if reasoning_effort is not None and not model.supports_reasoning:
        raise ReasoningNotEligibleForStageError(
            f"Reasoning effort {reasoning_effort!r} was requested for stage {stage!r}, but the "
            f"resolved model ({model_display_label(model)}) is not registered as supporting "
            "reasoning. Choose a reasoning-capable model, or leave reasoning on the system default."
        )

    return StageModelSelection(
        model=model, source=source, assignment=assignment, reasoning_effort=reasoning_effort
    )


_VALID_REASONING_EFFORTS = frozenset(ReasoningEffort.values)


def parse_requested_reasoning_effort(raw: str | None) -> str | None:
    """Parses one AJ/AC/AB reasoning-effort selector's raw POSTed value into the value
    `resolve_stage_model`/`get_adapter_for_stage` expect: `None` for "system default" (an empty/
    missing selection), or one of the five `ReasoningEffort` values. Every view that accepts a
    per-run reasoning override uses this one function -- mirroring `parse_requested_model_id` --
    so a garbled or tampered form value always produces the same actionable
    `ReasoningNotEligibleForStageError`-adjacent failure rather than an unhandled value reaching an
    adapter."""
    if raw is None or raw.strip() == "":
        return None
    if raw not in _VALID_REASONING_EFFORTS:
        raise ReasoningNotEligibleForStageError(
            f"{raw!r} is not a valid reasoning-effort selection -- choose an option from the "
            f"list ({sorted(_VALID_REASONING_EFFORTS)}), or leave it on the system default."
        )
    return raw


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
