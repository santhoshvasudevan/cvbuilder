"""Idempotent registry configuration: register OpenRouter's paid GPT-5.4 Mini/GPT-5.4 models and
make them the global default for every currently implemented LLM pipeline stage (2026-09-07,
D-039 paid GPT-5.4 model defaults -- operator-approved, superseding D-038's `openrouter/free`
default-for-every-stage matrix with a real, paid, per-stage-tuned matrix).

This is the one, deliberate, operator-invoked mechanism for this migration -- never ad hoc shell
SQL, never a data migration silently mutating operational rows. Mirrors
`llm_provider.services.openrouter_free_router.configure_openrouter_free_router`'s shape (same
report/dry-run/idempotency pattern) but is otherwise fully independent of it: this function never
touches `openrouter/free`, the retired Z.ai/GLM row, or any NVIDIA/Gemini/OpenAI model -- those
stay exactly as they are, remaining selectable per-run alternatives
(`llm_provider.services.eligibility.eligible_models_for_stage`). Running
`configure_openrouter_free_router` first (recommended, documented in the operator guide) is not a
prerequisite this function enforces -- it creates its own OpenRouter `LLMProvider` row if none
exists yet, exactly like that command does.

Historical referential integrity is preserved by construction: nothing is ever deleted, only
created or updated in place (`LLMModel.objects.get_or_create`-style upsert,
`StageModelAssignment.objects.update_or_create`-style upsert) -- `LLMCallLog`/`StageModelAssignment`
both use `on_delete=models.PROTECT` on their `model` FK, so a superseded model row could not be
deleted while referenced even if this command tried. Rollback for any one stage is a plain registry
edit: reassign it back to whichever model/reasoning it previously used via the existing admin -- no
re-creation, no data loss.
"""

from __future__ import annotations

import dataclasses

from django.db import transaction

from ..models import LLMModel, LLMProvider, ReasoningEffort, StageModelAssignment

# Exact OpenRouter-routed model identifiers (requirements Sec 1) -- opaque, sent to OpenRouter
# exactly as written, never combined with any prefix/vendor segment, never the direct-OpenAI
# identifiers ("gpt-5.4-mini"/"gpt-5.4") which this codebase must never send through the
# OpenRouter adapter.
GPT54_MINI_MODEL_ID = "openai/gpt-5.4-mini"
GPT54_MODEL_ID = "openai/gpt-5.4"

GPT54_MINI_DISPLAY_NAME = "GPT-5.4 Mini"
GPT54_DISPLAY_NAME = "GPT-5.4"

OPENROUTER_PROVIDER_NAME = "OpenRouter"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Conservative output-token ceiling for both GPT-5.4 models (requirements Sec 4: "without
# unnecessarily increasing the application's current per-call output budget"). Matches the exact
# value `openrouter_free_router.FREE_ROUTER_MAX_OUTPUT_TOKENS` already uses -- the smallest
# known-good stage budget already exercised live in this codebase (AC_NORMALIZE/AC_MATCH, D-027) --
# rather than a larger number assumed from GPT-5.4's own theoretical maximum. A model that cannot
# sustain even this budget still fails safely: `finish_reason=length` is classified CONFIGURATION
# and never silently accepted (`llm_provider/adapters/openai.py`'s shared parser, D-026). Intended
# to be verified/adjusted via `manage.py smoke_test_openrouter --model openai/gpt-5.4`, never raised
# speculatively ahead of an actual live call.
GPT54_MAX_OUTPUT_TOKENS = 8192

# The operator-approved default stage matrix (requirements Sec 1): extraction/analysis stages use
# the cheaper Mini model at medium reasoning; matching/ranking/writing stages -- where getting the
# judgment right matters more than raw throughput -- use the full model, with AC_MATCH/AC_RANK at
# high reasoning (judgment-heavy) and AB_BUILD at medium (structured writing, not open-ended
# judgment).
STAGE_DEFAULT_MATRIX: dict[str, tuple[str, str]] = {
    StageModelAssignment.Stage.MEMORY_BUILD: (GPT54_MINI_MODEL_ID, ReasoningEffort.MEDIUM),
    StageModelAssignment.Stage.AJ_ANALYZE: (GPT54_MINI_MODEL_ID, ReasoningEffort.MEDIUM),
    StageModelAssignment.Stage.AC_NORMALIZE: (GPT54_MINI_MODEL_ID, ReasoningEffort.MEDIUM),
    StageModelAssignment.Stage.AC_MATCH: (GPT54_MODEL_ID, ReasoningEffort.HIGH),
    StageModelAssignment.Stage.AC_RANK: (GPT54_MODEL_ID, ReasoningEffort.HIGH),
    StageModelAssignment.Stage.AB_BUILD: (GPT54_MODEL_ID, ReasoningEffort.MEDIUM),
}

NO_PRIOR_ASSIGNMENT = "(none)"
NO_PRIOR_REASONING = "(none)"


@dataclasses.dataclass
class StageReassignment:
    stage: str
    previous_model_id: str
    previous_reasoning_effort: str
    new_model_id: str
    new_reasoning_effort: str


@dataclasses.dataclass
class ConfigurationReport:
    provider_id: int | None
    provider_created: bool
    mini_model_id: int | None
    mini_model_created: bool
    full_model_id: int | None
    full_model_created: bool
    reassigned_stages: list[StageReassignment]
    dry_run: bool

    def describe(self) -> str:
        lines = [
            f"{'[dry-run] ' if self.dry_run else ''}OpenRouter provider: "
            f"{'created' if self.provider_created else 'existing'} (id={self.provider_id})",
            f"LLMModel {GPT54_MINI_MODEL_ID!r}: "
            f"{'created' if self.mini_model_created else 'existing/updated'} "
            f"(id={self.mini_model_id}, max_output_tokens={GPT54_MAX_OUTPUT_TOKENS})",
            f"LLMModel {GPT54_MODEL_ID!r}: "
            f"{'created' if self.full_model_created else 'existing/updated'} "
            f"(id={self.full_model_id}, max_output_tokens={GPT54_MAX_OUTPUT_TOKENS})",
        ]
        if self.reassigned_stages:
            for r in self.reassigned_stages:
                lines.append(
                    f"Stage {r.stage!r}: model {r.previous_model_id!r} -> {r.new_model_id!r}, "
                    f"reasoning {r.previous_reasoning_effort!r} -> {r.new_reasoning_effort!r}"
                )
        else:
            lines.append(
                "Every implemented stage's StageModelAssignment already matches the GPT-5.4 "
                "default matrix -- no reassignment needed."
            )
        return "\n".join(lines)


def _ensure_model(provider: LLMProvider, model_id: str, display_name: str) -> tuple[LLMModel, bool]:
    model = LLMModel.objects.select_for_update().filter(provider=provider, model_id=model_id).first()
    created = model is None
    if model is None:
        model = LLMModel(provider=provider, model_id=model_id)
    # Truthful, conservative capability flags (requirements Sec 4): both GPT-5.4 models are
    # structured-output- and reasoning-capable paid models; `supports_streaming` stays False
    # because this codebase never streams a response regardless of a model's own streaming
    # capability (every adapter, including OpenRouterAdapter, sends `stream: false`
    # unconditionally) -- `False` is the truthful statement of what this application actually
    # exercises, not a claim about the upstream model's own capability.
    model.supports_structured_output = True
    model.supports_reasoning = True
    model.supports_streaming = False
    model.max_output_tokens = GPT54_MAX_OUTPUT_TOKENS
    model.is_active = True
    if not model.display_name:
        # Only fill in a genuinely blank display_name -- never overwrite an operator's own curated
        # label, mirroring `openrouter_free_router.configure_openrouter_free_router`'s identical
        # fill-in-only-if-blank rule.
        model.display_name = display_name
    model.full_clean()
    model.save()
    return model, created


def configure_gpt54_defaults(*, dry_run: bool = False) -> ConfigurationReport:
    """Converge the registry to: an OpenRouter provider row with the documented base URL, active
    `openai/gpt-5.4-mini`/`openai/gpt-5.4` `LLMModel` rows with truthful capability flags, and
    every currently implemented `StageModelAssignment` pointed at `STAGE_DEFAULT_MATRIX`'s
    model+reasoning pair for that stage -- whatever it was previously assigned to (`openrouter/free`,
    NVIDIA, a stale GPT-5 row, or nothing at all). Safe to call repeatedly -- a second call with no
    intervening registry change reports the same converged state (`reassigned_stages == []`) and
    makes no further writes beyond re-affirming it.

    Only the *default* (`StageModelAssignment`) changes for the 6 stages in the matrix. No other
    provider/model row is ever modified: `openrouter/free`, every NVIDIA/Gemini/OpenAI model, and
    the retired Z.ai/GLM row are left exactly as they are -- remaining selectable per-run
    alternatives (`llm_provider.services.eligibility.eligible_models_for_stage`) even though they
    are no longer any of these 6 stages' default.

    `dry_run=True` performs every write inside this function's own transaction (so
    `full_clean()`-validated, realistic before/after state can be computed and reported) and then
    unconditionally rolls the whole transaction back -- the database is left byte-for-byte
    unchanged, and the caller only ever sees the report.
    """
    with transaction.atomic():
        provider = (
            LLMProvider.objects.select_for_update()
            .filter(provider_type=LLMProvider.ProviderType.OPENROUTER)
            .order_by("id")
            .first()
        )
        provider_created = provider is None
        if provider is None:
            provider = LLMProvider(
                name=OPENROUTER_PROVIDER_NAME,
                provider_type=LLMProvider.ProviderType.OPENROUTER,
                base_url=DEFAULT_BASE_URL,
                credential_env_var="OPENROUTER_API_KEY",
                data_collection_policy=LLMProvider.DataCollectionPolicy.DENY,
            )
            provider.full_clean()
            provider.save()
        elif not provider.base_url:
            provider.base_url = DEFAULT_BASE_URL
            provider.full_clean()
            provider.save(update_fields=["base_url"])

        mini_model, mini_created = _ensure_model(provider, GPT54_MINI_MODEL_ID, GPT54_MINI_DISPLAY_NAME)
        full_model, full_created = _ensure_model(provider, GPT54_MODEL_ID, GPT54_DISPLAY_NAME)
        models_by_id = {GPT54_MINI_MODEL_ID: mini_model, GPT54_MODEL_ID: full_model}

        reassigned: list[StageReassignment] = []
        existing_assignments = {
            assignment.stage: assignment
            for assignment in StageModelAssignment.objects.select_related("model")
            .select_for_update()
            .filter(stage__in=STAGE_DEFAULT_MATRIX)
        }
        for stage, (target_model_id, target_reasoning) in STAGE_DEFAULT_MATRIX.items():
            target_model = models_by_id[target_model_id]
            assignment = existing_assignments.get(stage)
            if assignment is None:
                new_assignment = StageModelAssignment(
                    stage=stage, model=target_model, default_reasoning_effort=target_reasoning
                )
                new_assignment.full_clean()
                new_assignment.save()
                reassigned.append(
                    StageReassignment(
                        stage=stage,
                        previous_model_id=NO_PRIOR_ASSIGNMENT,
                        previous_reasoning_effort=NO_PRIOR_REASONING,
                        new_model_id=target_model_id,
                        new_reasoning_effort=target_reasoning,
                    )
                )
                continue

            already_converged = (
                assignment.model_id == target_model.id
                and assignment.default_reasoning_effort == target_reasoning
            )
            if already_converged:
                # Idempotency requires making no further write here.
                continue

            reassigned.append(
                StageReassignment(
                    stage=stage,
                    previous_model_id=assignment.model.model_id,
                    previous_reasoning_effort=assignment.default_reasoning_effort or NO_PRIOR_REASONING,
                    new_model_id=target_model_id,
                    new_reasoning_effort=target_reasoning,
                )
            )
            assignment.model = target_model
            assignment.default_reasoning_effort = target_reasoning
            if (
                assignment.max_output_tokens is not None
                and assignment.max_output_tokens > GPT54_MAX_OUTPUT_TOKENS
            ):
                # Never silently exceed the new model's declared capability -- clamp down and let
                # full_clean() re-verify rather than saving an inconsistent row.
                assignment.max_output_tokens = GPT54_MAX_OUTPUT_TOKENS
            assignment.full_clean()
            assignment.save()

        report = ConfigurationReport(
            provider_id=provider.id,
            provider_created=provider_created,
            mini_model_id=mini_model.id,
            mini_model_created=mini_created,
            full_model_id=full_model.id,
            full_model_created=full_created,
            reassigned_stages=reassigned,
            dry_run=dry_run,
        )

        if dry_run:
            transaction.set_rollback(True)

    return report
