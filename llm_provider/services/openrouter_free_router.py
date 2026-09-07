"""Idempotent registry configuration: make OpenRouter's Free Models Router (`openrouter/free`)
the global default for *every* currently implemented LLM pipeline stage (2026-09-07, D-038;
broadened from its original AC_NORMALIZE-only scope by explicit operator decision the same day --
see docs/DECISIONS.md's D-038 entry).

This is the one, deliberate, operator-invoked mechanism for this migration -- never ad hoc shell
SQL, never a data migration silently mutating operational rows. It is safe to run repeatedly (each
run converges the registry to the same target state) and never touches any provider/model row
outside what this migration targets: NVIDIA/OpenAI/Gemini model rows are left exactly as they are
(active, selectable per-run alternatives -- see `llm_provider.services.eligibility`), only their
role as a stage's *global default* changes. FAKE rows are never touched at all.

Historical referential integrity is preserved by construction: the Z.ai `LLMModel` row is never
deleted, only deactivated (`is_active=False`) -- `LLMCallLog`/`StageModelAssignment` both use
`on_delete=models.PROTECT` on their `model` FK, so it could not be deleted while referenced even
if this command tried. Rollback for any one stage is a plain registry edit: reassign it back to
whichever model it previously used via the existing admin (reactivating the Z.ai row first,
`is_active=True`, if that is the target) -- no re-creation, no data loss.
"""

from __future__ import annotations

import dataclasses

from django.db import transaction

from ..models import LLMModel, LLMProvider, StageModelAssignment

# The exact retired model id this migration moves stages *off of* -- opaque, never parsed/split.
ZAI_MODEL_ID = "z-ai/glm-5.2:free"

# The exact model id this migration moves stages *onto* -- opaque, sent to OpenRouter exactly as
# written, never combined with any prefix/vendor segment (requirements.md Sec 9, D-038).
FREE_ROUTER_MODEL_ID = "openrouter/free"

FREE_ROUTER_PROVIDER_NAME = "OpenRouter"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Conservative output-token ceiling for the free router (D-038): `openrouter/free` is a *virtual*
# router whose underlying model can differ call to call, so this codebase cannot claim one true
# capability ceiling for it the way it could for a single pinned model (unlike the retired Z.ai
# row's verified 230,400). This value is deliberately set to match the smallest known-good stage
# budget already exercised live in this codebase (AC_NORMALIZE/AC_MATCH, D-027) rather than
# assumed from the previous model's much larger capability. A routed model that cannot sustain
# even this budget still fails safely: `finish_reason=length` is classified CONFIGURATION and
# never silently accepted (see llm_provider/adapters/openai.py's shared parser, D-026). Intended
# to be verified/adjusted via `manage.py smoke_test_openrouter --model openrouter/free`, never
# raised speculatively.
FREE_ROUTER_MAX_OUTPUT_TOKENS = 8192


# Sentinel used in a StageReassignment's `previous_model_id` when a stage had no
# StageModelAssignment row at all before this command ran (e.g. AC_RANK before D-015's own
# migration ever created a default for it) -- never a real model_id, never confused with one.
NO_PRIOR_ASSIGNMENT = "(none)"


@dataclasses.dataclass
class StageReassignment:
    stage: str
    previous_model_id: str
    new_model_id: str


@dataclasses.dataclass
class ConfigurationReport:
    provider_id: int | None
    provider_created: bool
    free_router_model_id: int | None
    free_router_model_created: bool
    zai_model_found: bool
    zai_model_deactivated: bool
    reassigned_stages: list[StageReassignment]
    dry_run: bool

    def describe(self) -> str:
        lines = [
            f"{'[dry-run] ' if self.dry_run else ''}OpenRouter provider: "
            f"{'created' if self.provider_created else 'existing'} (id={self.provider_id})",
            f"LLMModel {FREE_ROUTER_MODEL_ID!r}: "
            f"{'created' if self.free_router_model_created else 'existing/updated'} "
            f"(id={self.free_router_model_id}, max_output_tokens={FREE_ROUTER_MAX_OUTPUT_TOKENS})",
        ]
        if self.zai_model_found:
            lines.append(
                f"LLMModel {ZAI_MODEL_ID!r}: "
                f"{'deactivated' if self.zai_model_deactivated else 'already inactive'} "
                "(preserved for historical/audit referential integrity, never deleted)"
            )
        else:
            lines.append(f"LLMModel {ZAI_MODEL_ID!r}: not found -- nothing to deactivate.")
        if self.reassigned_stages:
            for r in self.reassigned_stages:
                lines.append(f"Stage {r.stage!r}: {r.previous_model_id!r} -> {r.new_model_id!r}")
        else:
            lines.append(
                "Every implemented stage's StageModelAssignment already points at "
                f"{FREE_ROUTER_MODEL_ID!r} -- no reassignment needed."
            )
        return "\n".join(lines)


def configure_openrouter_free_router(*, dry_run: bool = False) -> ConfigurationReport:
    """Converge the registry to: an OpenRouter provider row with the documented base URL, an
    active `openrouter/free` LLMModel with truthful (conservative, never-inferred) capability
    flags, the Z.ai model deactivated (never deleted), and **every** currently implemented
    `StageModelAssignment.Stage` -- MEMORY_BUILD/AJ_ANALYZE/AC_NORMALIZE/AC_RANK/AC_MATCH/
    AB_BUILD, whatever it was previously assigned to (Z.ai, NVIDIA, OpenAI, or nothing at all) --
    pointed at `openrouter/free` instead. Safe to call repeatedly -- a second call with no
    intervening registry change reports the same converged state (`reassigned_stages == []`) and
    makes no further writes beyond re-affirming it.

    Only the *default* (`StageModelAssignment`) changes. No other provider/model row is ever
    modified by this function: NVIDIA/OpenAI model rows keep whatever `is_active` value they
    already had, so they remain selectable per-run alternatives via
    `llm_provider.services.eligibility.eligible_models_for_stage` even though they are no longer
    any stage's default.

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
                name=FREE_ROUTER_PROVIDER_NAME,
                provider_type=LLMProvider.ProviderType.OPENROUTER,
                base_url=DEFAULT_BASE_URL,
                credential_env_var="OPENROUTER_API_KEY",
                data_collection_policy=LLMProvider.DataCollectionPolicy.DENY,
            )
            provider.full_clean()
            provider.save()
        elif not provider.base_url:
            # Only fill in a genuinely blank base_url (which already resolves to the same
            # DEFAULT_BASE_URL via the adapter's own fallback) -- never overwrite an operator's
            # deliberate custom endpoint.
            provider.base_url = DEFAULT_BASE_URL
            provider.full_clean()
            provider.save(update_fields=["base_url"])

        free_router_model = LLMModel.objects.filter(
            provider=provider, model_id=FREE_ROUTER_MODEL_ID
        ).first()
        free_router_model_created = free_router_model is None
        if free_router_model is None:
            free_router_model = LLMModel(provider=provider, model_id=FREE_ROUTER_MODEL_ID)
        # Truthful, conservative capability flags -- never inferred from the retired Z.ai row
        # (D-038's explicit instruction: a virtual router is not "the same model, new name").
        free_router_model.supports_structured_output = True
        free_router_model.supports_reasoning = False
        free_router_model.max_output_tokens = FREE_ROUTER_MAX_OUTPUT_TOKENS
        free_router_model.is_active = True
        if not free_router_model.display_name:
            # Only fill in a genuinely blank display_name -- never overwrite an operator's own
            # curated label, mirroring the provider base_url fill-in-only-if-blank rule above.
            free_router_model.display_name = "Free Models Router"
        free_router_model.full_clean()
        free_router_model.save()

        # Deactivate *every* LLMModel row matching the retired model id, across every provider
        # row that happens to have one -- never only the row under `provider` (the canonical
        # OpenRouter provider selected above). A real-database check during the 2026-09-07
        # broadened-default work found a second, orphaned OpenRouter provider row (a stray debug/
        # smoke-test artifact) with its own still-active copy of the same retired model id; only
        # deactivating "the" canonical row would have left that duplicate silently eligible.
        zai_models = list(LLMModel.objects.select_for_update().filter(model_id=ZAI_MODEL_ID))
        zai_model_found = bool(zai_models)
        zai_model_deactivated = False
        for zai_model in zai_models:
            if zai_model.is_active:
                zai_model_deactivated = True
                zai_model.is_active = False
                zai_model.full_clean()
                zai_model.save(update_fields=["is_active"])

        reassigned: list[StageReassignment] = []
        existing_assignments = {
            assignment.stage: assignment
            for assignment in StageModelAssignment.objects.select_related("model")
            .select_for_update()
            .filter(stage__in=StageModelAssignment.Stage.values)
        }
        for stage in StageModelAssignment.Stage.values:
            assignment = existing_assignments.get(stage)
            if assignment is None:
                # No default configured for this stage at all yet -- create one, converging it to
                # the free router rather than leaving it unconfigured (D-038 broadened scope:
                # "openrouter/free is the default for every current LLM pipeline stage").
                new_assignment = StageModelAssignment(stage=stage, model=free_router_model)
                new_assignment.full_clean()
                new_assignment.save()
                reassigned.append(
                    StageReassignment(
                        stage=stage,
                        previous_model_id=NO_PRIOR_ASSIGNMENT,
                        new_model_id=FREE_ROUTER_MODEL_ID,
                    )
                )
                continue
            if assignment.model_id == free_router_model.id:
                # Already converged -- idempotency requires making no further write here.
                continue
            reassigned.append(
                StageReassignment(
                    stage=stage,
                    previous_model_id=assignment.model.model_id,
                    new_model_id=FREE_ROUTER_MODEL_ID,
                )
            )
            assignment.model = free_router_model
            if (
                assignment.max_output_tokens is not None
                and assignment.max_output_tokens > FREE_ROUTER_MAX_OUTPUT_TOKENS
            ):
                # Never silently exceed the new model's declared capability -- clamp down and
                # let full_clean() re-verify rather than saving an inconsistent row.
                assignment.max_output_tokens = FREE_ROUTER_MAX_OUTPUT_TOKENS
            assignment.full_clean()
            assignment.save()

        report = ConfigurationReport(
            provider_id=provider.id,
            provider_created=provider_created,
            free_router_model_id=free_router_model.id,
            free_router_model_created=free_router_model_created,
            zai_model_found=zai_model_found,
            zai_model_deactivated=zai_model_deactivated,
            reassigned_stages=reassigned,
            dry_run=dry_run,
        )

        if dry_run:
            # Roll back every write made above -- a dry run must leave the database byte-for-byte
            # unchanged even though the objects above were saved to compute a realistic,
            # full_clean()-validated report.
            transaction.set_rollback(True)

    return report
