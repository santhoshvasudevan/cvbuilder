"""Idempotent registry configuration: register GPT-5.4 Mini/GPT-5.4 as **direct OpenAI** models
and make them the global default for every currently implemented LLM pipeline stage (2026-09-07,
D-039 paid GPT-5.4 model defaults, corrected same day -- the operator's actual requirement was the
direct OpenAI API, not OpenRouter; the OpenRouter-hosted `openai/gpt-5.4-mini`/`openai/gpt-5.4`
records from the original pass were an implementation misunderstanding, not the intended decision,
and are kept only as explicit, optional, non-default alternatives).

This is the one, deliberate, operator-invoked mechanism for this migration -- never ad hoc shell
SQL, never a data migration silently mutating operational rows. Mirrors
`llm_provider.services.openrouter_free_router.configure_openrouter_free_router`'s shape (same
report/dry-run/idempotency pattern) but is otherwise fully independent of it: this function never
touches `openrouter/free`, the retired Z.ai/GLM row, or any NVIDIA/Gemini model -- those stay
exactly as they are, remaining selectable per-run alternatives
(`llm_provider.services.eligibility.eligible_models_for_stage`).

Historical referential integrity is preserved by construction: nothing is ever deleted, only
created or updated in place -- `LLMCallLog`/`StageModelAssignment` both use `on_delete=models.
PROTECT` on their `model` FK, so a superseded model row could not be deleted while referenced even
if this command tried. Rollback for any one stage is a plain registry edit: reassign it back to
whichever model/reasoning it previously used via the existing admin -- no re-creation, no data
loss.
"""

from __future__ import annotations

import dataclasses

from django.db import transaction

from ..models import LLMModel, LLMProvider, ReasoningEffort, StageModelAssignment

# Exact direct-OpenAI model identifiers (requirements Sec 1/Sec 2 of the correction) -- opaque,
# sent to OpenAI's own API exactly as written, and **never** carrying the `openai/` OpenRouter
# routing prefix (that prefix belongs only to the OpenRouter-hosted alternative records below --
# sending it to the direct OpenAI endpoint would be an invalid/unknown model id there).
GPT54_MINI_MODEL_ID = "gpt-5.4-mini"
GPT54_MODEL_ID = "gpt-5.4"

# The OpenRouter-hosted equivalents from the original (corrected) implementation -- kept active and
# selectable as explicit, optional per-run alternatives (never deleted, never a stage default) per
# the operator's explicit instruction to preserve them as separate optional records.
OPENROUTER_GPT54_MINI_MODEL_ID = "openai/gpt-5.4-mini"
OPENROUTER_GPT54_MODEL_ID = "openai/gpt-5.4"

GPT54_MINI_DISPLAY_NAME = "GPT-5.4 Mini"
GPT54_DISPLAY_NAME = "GPT-5.4"

OPENAI_PROVIDER_NAME = "OpenAI"
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENAI_CREDENTIAL_ENV_VAR = "OPENAI_API_KEY"

OPENROUTER_PROVIDER_NAME = "OpenRouter"
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Conservative *application-registered* output-token capability for both GPT-5.4 models, direct
# and OpenRouter-hosted alike (2026-09-07, Product Owner budget correction -- CLAUDE.md's "Token
# consumption is the v1 observability priority" and requirements Sec 4: "without unnecessarily
# increasing the application's current per-call output budget" -- 16384 is that conservative
# number, not GPT-5.4's own theoretical maximum). This is a *ceiling*, never a floor or a
# consumption target: a stage's own `StageModelAssignment.max_output_tokens` (see
# `STAGE_DEFAULT_MATRIX` below) is what actually bounds a given request, and a model virtually
# never needs, generates, or is billed for the full registered ceiling -- OpenAI bills exactly the
# tokens actually produced, regardless of this configured maximum.
#
# History: the original D-039 pass registered a smaller, now-proven-insufficient 8192 (matching
# `openrouter_free_router.FREE_ROUTER_MAX_OUTPUT_TOKENS`, a free-router precedent this decision no
# longer inherits). Real historical production measurements against this exact pipeline
# (`LLMCallLog` token-usage data) subsequently showed: AC_NORMALIZE ~7650, AC_RANK ~12230, AC_MATCH
# ~7326, AB_BUILD ~8204 output tokens -- proving 8192 was already insufficient for AC_RANK/AB_BUILD
# and left inadequate headroom for AC_NORMALIZE/AC_MATCH. 16384 is the Product Owner's approved
# correction: comfortably above every observed figure, and well within this model family's own
# documented/audited ceiling (the pre-existing direct-OpenAI `gpt-5` row in this same registry is
# separately audited at 128,000 max output tokens, D-029/Phase F) -- this decision deliberately
# registers only 16384, never that larger number, per the Product Owner's explicit "do not inflate
# the registry to a larger undocumented number" instruction.
#
# A model that cannot sustain even a stage's own configured budget still fails safely:
# `finish_reason=length` is classified CONFIGURATION and never silently accepted
# (`llm_provider/adapters/openai.py`'s shared parser, D-026) -- this change never introduces or
# implies an automatic retry/fallback for that failure mode. Stage budgets should be revisited
# later using observed `LLMCallLog` token-usage metrics from real qualification/production runs,
# not raised speculatively ahead of one.
GPT54_MAX_OUTPUT_TOKENS = 16384

# Per-stage output-token budgets (2026-09-07, Product Owner budget correction): MEMORY_BUILD/
# AJ_ANALYZE are deliberately left at their existing, already-sufficient values -- this correction
# targets only the four stages historical measurements proved too tight. Both are comfortably under
# the 16384 model-capability ceiling above, so neither stage is affected by that ceiling's increase.
MEMORY_BUILD_OUTPUT_BUDGET = 4096
AJ_ANALYZE_OUTPUT_BUDGET = 8192
# AC_NORMALIZE/AC_RANK/AC_MATCH/AB_BUILD: raised to the new registered ceiling itself -- the
# Product Owner's approved correction for the four stages proven too tight by real measurements
# (see the ceiling constant's own docstring above for the exact historical figures).
EXPANDED_STAGE_OUTPUT_BUDGET = 16384

# The operator-approved default stage matrix (requirements Sec 1, corrected to the direct-OpenAI
# ids and, in this update, to the Product-Owner-approved per-stage output budgets): extraction/
# analysis stages use the cheaper Mini model at medium reasoning; matching/ranking/writing stages
# -- where getting the judgment right matters more than raw throughput -- use the full model, with
# AC_MATCH/AC_RANK at high reasoning (judgment-heavy) and AB_BUILD at medium (structured writing,
# not open-ended judgment). Provider/model mapping and reasoning levels are unchanged by this
# update -- only the third (budget) element of each tuple changed, and only for four stages.
STAGE_DEFAULT_MATRIX: dict[str, tuple[str, str, int]] = {
    StageModelAssignment.Stage.MEMORY_BUILD: (
        GPT54_MINI_MODEL_ID, ReasoningEffort.MEDIUM, MEMORY_BUILD_OUTPUT_BUDGET,
    ),
    StageModelAssignment.Stage.AJ_ANALYZE: (
        GPT54_MINI_MODEL_ID, ReasoningEffort.MEDIUM, AJ_ANALYZE_OUTPUT_BUDGET,
    ),
    StageModelAssignment.Stage.AC_NORMALIZE: (
        GPT54_MINI_MODEL_ID, ReasoningEffort.MEDIUM, EXPANDED_STAGE_OUTPUT_BUDGET,
    ),
    StageModelAssignment.Stage.AC_MATCH: (
        GPT54_MODEL_ID, ReasoningEffort.HIGH, EXPANDED_STAGE_OUTPUT_BUDGET,
    ),
    StageModelAssignment.Stage.AC_RANK: (
        GPT54_MODEL_ID, ReasoningEffort.HIGH, EXPANDED_STAGE_OUTPUT_BUDGET,
    ),
    StageModelAssignment.Stage.AB_BUILD: (
        GPT54_MODEL_ID, ReasoningEffort.MEDIUM, EXPANDED_STAGE_OUTPUT_BUDGET,
    ),
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
    previous_max_output_tokens: int | str
    new_max_output_tokens: int


@dataclasses.dataclass
class ConfigurationReport:
    openai_provider_id: int | None
    openai_provider_created: bool
    openrouter_provider_id: int | None
    openrouter_provider_created: bool
    mini_model_id: int | None
    mini_model_created: bool
    full_model_id: int | None
    full_model_created: bool
    openrouter_mini_model_id: int | None
    openrouter_mini_model_created: bool
    openrouter_full_model_id: int | None
    openrouter_full_model_created: bool
    reassigned_stages: list[StageReassignment]
    dry_run: bool

    def describe(self) -> str:
        lines = [
            f"{'[dry-run] ' if self.dry_run else ''}OpenAI (direct) provider: "
            f"{'created' if self.openai_provider_created else 'existing'} "
            f"(id={self.openai_provider_id})",
            f"LLMModel {GPT54_MINI_MODEL_ID!r} (direct OpenAI): "
            f"{'created' if self.mini_model_created else 'existing/updated'} "
            f"(id={self.mini_model_id}, max_output_tokens={GPT54_MAX_OUTPUT_TOKENS})",
            f"LLMModel {GPT54_MODEL_ID!r} (direct OpenAI): "
            f"{'created' if self.full_model_created else 'existing/updated'} "
            f"(id={self.full_model_id}, max_output_tokens={GPT54_MAX_OUTPUT_TOKENS})",
            f"OpenRouter provider (optional alternative): "
            f"{'created' if self.openrouter_provider_created else 'existing'} "
            f"(id={self.openrouter_provider_id})",
            f"LLMModel {OPENROUTER_GPT54_MINI_MODEL_ID!r} (OpenRouter, optional alternative): "
            f"{'created' if self.openrouter_mini_model_created else 'existing/updated'} "
            f"(id={self.openrouter_mini_model_id})",
            f"LLMModel {OPENROUTER_GPT54_MODEL_ID!r} (OpenRouter, optional alternative): "
            f"{'created' if self.openrouter_full_model_created else 'existing/updated'} "
            f"(id={self.openrouter_full_model_id})",
        ]
        if self.reassigned_stages:
            for r in self.reassigned_stages:
                lines.append(
                    f"Stage {r.stage!r}: model {r.previous_model_id!r} -> {r.new_model_id!r}, "
                    f"reasoning {r.previous_reasoning_effort!r} -> {r.new_reasoning_effort!r}, "
                    f"output budget {r.previous_max_output_tokens!r} -> {r.new_max_output_tokens!r}"
                )
        else:
            lines.append(
                "Every implemented stage's StageModelAssignment already matches the direct-OpenAI "
                "GPT-5.4 default matrix (model, reasoning, and output budget) -- no reassignment "
                "needed."
            )
        return "\n".join(lines)


def _ensure_model(
    provider: LLMProvider, model_id: str, display_name: str
) -> tuple[LLMModel, bool]:
    model = LLMModel.objects.select_for_update().filter(provider=provider, model_id=model_id).first()
    created = model is None
    if model is None:
        model = LLMModel(provider=provider, model_id=model_id)
    # Truthful, conservative capability flags (requirements Sec 4): both GPT-5.4 models are
    # structured-output- and reasoning-capable paid models; `supports_streaming` stays False
    # because this codebase never streams a response regardless of a model's own streaming
    # capability (every adapter sends a non-streaming request unconditionally) -- `False` is the
    # truthful statement of what this application actually exercises, not a claim about the
    # upstream model's own capability.
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


def _ensure_openai_provider() -> tuple[LLMProvider, bool]:
    """Finds-or-creates the **direct** OpenAI `LLMProvider` row -- reused, never duplicated, if one
    already exists (this registry already has a real direct-OpenAI provider row from earlier work,
    id 11, serving the pre-existing `gpt-5` model). Only fills in `base_url`/`credential_env_var`
    when genuinely blank -- never overwrites an operator's own configuration, mirroring every other
    idempotent configuration function in this module/`openrouter_free_router.py`."""
    provider = (
        LLMProvider.objects.select_for_update()
        .filter(provider_type=LLMProvider.ProviderType.OPENAI)
        .order_by("id")
        .first()
    )
    created = provider is None
    if provider is None:
        provider = LLMProvider(
            name=OPENAI_PROVIDER_NAME,
            provider_type=LLMProvider.ProviderType.OPENAI,
            base_url=OPENAI_DEFAULT_BASE_URL,
            credential_env_var=OPENAI_CREDENTIAL_ENV_VAR,
            data_collection_policy=LLMProvider.DataCollectionPolicy.DENY,
        )
        provider.full_clean()
        provider.save()
        return provider, created
    changed_fields = []
    if not provider.base_url:
        provider.base_url = OPENAI_DEFAULT_BASE_URL
        changed_fields.append("base_url")
    if not provider.credential_env_var:
        provider.credential_env_var = OPENAI_CREDENTIAL_ENV_VAR
        changed_fields.append("credential_env_var")
    if changed_fields:
        provider.full_clean()
        provider.save(update_fields=changed_fields)
    return provider, created


def _ensure_openrouter_provider() -> tuple[LLMProvider, bool]:
    provider = (
        LLMProvider.objects.select_for_update()
        .filter(provider_type=LLMProvider.ProviderType.OPENROUTER)
        .order_by("id")
        .first()
    )
    created = provider is None
    if provider is None:
        provider = LLMProvider(
            name=OPENROUTER_PROVIDER_NAME,
            provider_type=LLMProvider.ProviderType.OPENROUTER,
            base_url=OPENROUTER_DEFAULT_BASE_URL,
            credential_env_var="OPENROUTER_API_KEY",
            data_collection_policy=LLMProvider.DataCollectionPolicy.DENY,
        )
        provider.full_clean()
        provider.save()
    elif not provider.base_url:
        provider.base_url = OPENROUTER_DEFAULT_BASE_URL
        provider.full_clean()
        provider.save(update_fields=["base_url"])
    return provider, created


def configure_gpt54_defaults(*, dry_run: bool = False) -> ConfigurationReport:
    """Converge the registry to: a direct OpenAI `LLMProvider` row (reused if one already exists),
    active `gpt-5.4-mini`/`gpt-5.4` `LLMModel` rows under it with truthful capability flags, every
    currently implemented `StageModelAssignment` pointed at `STAGE_DEFAULT_MATRIX`'s direct-OpenAI
    model+reasoning pair for that stage -- and, kept as explicit optional alternatives (never a
    stage default), the OpenRouter-hosted `openai/gpt-5.4-mini`/`openai/gpt-5.4` records under the
    OpenRouter provider. Safe to call repeatedly -- a second call with no intervening registry
    change reports the same converged state (`reassigned_stages == []`) and makes no further
    writes beyond re-affirming it.

    Only the *default* (`StageModelAssignment`) changes for the 6 stages in the matrix. No other
    provider/model row is ever modified: `openrouter/free`, every NVIDIA/Gemini model, the
    pre-existing direct-OpenAI `gpt-5` row, the OpenRouter-hosted GPT-5.4 records, and the retired
    Z.ai/GLM row are left exactly as they are -- remaining selectable per-run alternatives
    (`llm_provider.services.eligibility.eligible_models_for_stage`) even though none of them is any
    of these 6 stages' default.

    `dry_run=True` performs every write inside this function's own transaction (so
    `full_clean()`-validated, realistic before/after state can be computed and reported) and then
    unconditionally rolls the whole transaction back -- the database is left byte-for-byte
    unchanged, and the caller only ever sees the report.
    """
    with transaction.atomic():
        openai_provider, openai_provider_created = _ensure_openai_provider()
        openrouter_provider, openrouter_provider_created = _ensure_openrouter_provider()

        mini_model, mini_created = _ensure_model(
            openai_provider, GPT54_MINI_MODEL_ID, GPT54_MINI_DISPLAY_NAME
        )
        full_model, full_created = _ensure_model(openai_provider, GPT54_MODEL_ID, GPT54_DISPLAY_NAME)
        models_by_id = {GPT54_MINI_MODEL_ID: mini_model, GPT54_MODEL_ID: full_model}

        # Optional, explicitly-preserved OpenRouter-hosted alternatives -- never the default,
        # never removed if already present from the original (corrected) implementation.
        openrouter_mini_model, openrouter_mini_created = _ensure_model(
            openrouter_provider, OPENROUTER_GPT54_MINI_MODEL_ID, GPT54_MINI_DISPLAY_NAME
        )
        openrouter_full_model, openrouter_full_created = _ensure_model(
            openrouter_provider, OPENROUTER_GPT54_MODEL_ID, GPT54_DISPLAY_NAME
        )

        reassigned: list[StageReassignment] = []
        existing_assignments = {
            assignment.stage: assignment
            for assignment in StageModelAssignment.objects.select_related("model")
            .select_for_update()
            .filter(stage__in=STAGE_DEFAULT_MATRIX)
        }
        for stage, (target_model_id, target_reasoning, target_budget) in STAGE_DEFAULT_MATRIX.items():
            target_model = models_by_id[target_model_id]
            assignment = existing_assignments.get(stage)
            if assignment is None:
                new_assignment = StageModelAssignment(
                    stage=stage,
                    model=target_model,
                    default_reasoning_effort=target_reasoning,
                    max_output_tokens=target_budget,
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
                        previous_max_output_tokens=NO_PRIOR_ASSIGNMENT,
                        new_max_output_tokens=target_budget,
                    )
                )
                continue

            already_converged = (
                assignment.model_id == target_model.id
                and assignment.default_reasoning_effort == target_reasoning
                and assignment.max_output_tokens == target_budget
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
                    previous_max_output_tokens=(
                        assignment.max_output_tokens
                        if assignment.max_output_tokens is not None
                        else NO_PRIOR_ASSIGNMENT
                    ),
                    new_max_output_tokens=target_budget,
                )
            )
            assignment.model = target_model
            assignment.default_reasoning_effort = target_reasoning
            # Explicitly converge to the canonical per-stage budget (requirements: "reproducible
            # for a new database and idempotent for the existing database" -- never merely clamped
            # down from whatever happened to be there before, and never left unset, which would
            # silently fall back to the model's own full capability ceiling instead of this stage's
            # deliberately smaller/larger approved budget).
            assignment.max_output_tokens = target_budget
            assignment.full_clean()
            assignment.save()

        report = ConfigurationReport(
            openai_provider_id=openai_provider.id,
            openai_provider_created=openai_provider_created,
            openrouter_provider_id=openrouter_provider.id,
            openrouter_provider_created=openrouter_provider_created,
            mini_model_id=mini_model.id,
            mini_model_created=mini_created,
            full_model_id=full_model.id,
            full_model_created=full_created,
            openrouter_mini_model_id=openrouter_mini_model.id,
            openrouter_mini_model_created=openrouter_mini_created,
            openrouter_full_model_id=openrouter_full_model.id,
            openrouter_full_model_created=openrouter_full_created,
            reassigned_stages=reassigned,
            dry_run=dry_run,
        )

        if dry_run:
            transaction.set_rollback(True)

    return report
