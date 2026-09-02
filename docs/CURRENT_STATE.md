# Current State

Last updated: 2026-09-02 (M2 — LLM provider abstraction, registry, and audit foundation
implemented and verified).

## Summary

This repository has completed **Milestones M1 and M2**. M1 established the Django/PostgreSQL
application foundation (see git history for detail). M2 fully implements the `llm_provider` app:
the provider/model registry, the normalized adapter interface, retry policy, typed error
taxonomy, per-provider schema translation (OpenAI/NVIDIA NIM/Gemini), a deterministic fake
adapter, and opt-in manual smoke-test commands. No pipeline stage calls into this app yet --
that starts at Milestone M3. Milestones M3 through M8 remain **not implemented**.

## What exists (M2 — new)

- **Registry models** (`llm_provider/models.py`): `LLMProvider` (name, `provider_type`, `base_url`,
  `credential_env_var` -- never a credential value), `LLMModel` (capability flags:
  `supports_structured_output`/`supports_streaming`/`supports_reasoning`/`max_output_tokens`),
  `StageModelAssignment` (one row per pipeline stage, unique constraint enforces exactly one
  assignment per stage), `LLMCallLog` (token-first audit ledger: input/cached-input/output/total
  tokens, latency, retry count, sanitized error category/message -- no raw body fields exist on
  this model at all, so there is nothing to accidentally log unsanitized). All four registered in
  Django admin (`llm_provider/admin.py`); `LLMCallLog` is admin-read-only by design (an audit
  ledger is never hand-edited).
- **Normalized types** (`llm_provider/types.py`): `NormalizedLLMRequest`, `NormalizedLLMResult`,
  `TokenUsage`. **Error taxonomy** (`llm_provider/errors.py`): `LLMErrorCategory` (six categories
  per requirements.md Sec 9.5) and `sanitize_error_message()`, which strips body-shaped content
  and truncates before anything reaches `LLMCallLog` or a log line.
- **Retry policy** (`llm_provider/retry.py`): `execute_with_retry()` retries only transient
  categories (`RATE_LIMIT`/`TIMEOUT`/`PROVIDER_INTERNAL`), never retries once
  `partial_output_received` is set (regardless of category), and caps attempts with linear
  backoff. Sleep is injectable so tests never actually wait.
- **Schema translation** (`llm_provider/schema_translation.py`): `to_openai_strict_schema()`
  (keeps `$ref`/`$defs`/`enum`, adds `additionalProperties: false` recursively -- OpenAI and NIM
  both use this) and `to_gemini_schema()` (inlines `$ref`/`$defs`, strips `enum`, converts type
  names to Gemini's uppercase OpenAPI-subset dialect).
- **Adapters** (`llm_provider/adapters/`): `BaseLLMAdapter` (abstract -- owns the shared call
  path: retry, re-validation of the provider's raw dict against the canonical Pydantic
  `output_schema` via `model_validate()`, latency measurement, and the single `LLMCallLog` write);
  `OpenAIAdapter`, `NvidiaNimAdapter` (checks `supports_structured_output` before assuming NIM
  strict-schema support), `GeminiAdapter` (REST-only, never gRPC; defensive `thinkingConfig`
  handling), all three via plain REST (`requests`), no provider SDK dependency anywhere; and
  `FakeAdapter` for deterministic tests. `adapters/__init__.py` provides
  `get_adapter_for_stage(stage)`, the one routing function pipeline code will call from M3 onward.
- **Opt-in manual smoke tests** (`llm_provider/smoke/` + three management commands
  `smoke_test_openai`/`smoke_test_nvidia`/`smoke_test_gemini`): each checks for its provider's
  credential env var, prints `NOT LIVE-VERIFIED` and exits cleanly if absent (verified manually
  with all three credentials unset), otherwise makes one real structured-output call. Never
  invoked by `manage.py test` or any automated path.
- **Automated test suite**: 41 deterministic tests across `llm_provider/tests/` (registry CRUD +
  admin-changelist reachability, stage-routing incl. zero-code-change reassignment, retry
  classification matrix incl. the partial-stream rule, error sanitizer, fake-adapter
  `LLMCallLog` correctness, schema translation for all three real adapters, and
  credential/capability configuration guards) -- all pass with zero live credentials.

## M2 verification performed (2026-09-02)

- `manage.py makemigrations`/`migrate` applied `llm_provider.0001_initial` cleanly against the
  real local Postgres instance.
- `manage.py test` -> 41/41 passing (up from 0 at M1), zero live credentials used or required.
- `make check`/`make lint`/`make test` all pass repeatably.
- Manually ran all three smoke-test management commands with no credentials configured; each
  correctly reported `NOT LIVE-VERIFIED` and exited cleanly rather than failing or attempting a
  network call.
- `grep` across the repository confirms no pipeline app imports a provider SDK directly (there is
  no OpenAI/Google-GenerativeAI/Anthropic SDK import anywhere) and no secret-shaped literal exists
  in any tracked file.
- **Not yet done**: none of the three real adapters have been exercised against a live provider
  (no credentials were available in this environment). Per the M2 risk note in
  `docs/IMPLEMENTATION_PLAN.md`, this means LLM-007's per-provider quirk handling is verified only
  at the deterministic schema-translation level, not against real API behavior -- flagged
  honestly rather than claimed as fully proven.

## What does not exist

- Any model fields, migrations, views, or templates for `candidate_memory`, `job_intake`,
  `candidate_matching`, `resume_builder`, `reviews`, or `job_applications` (Milestones M3-M7).
- Any wiring of a pipeline stage to actually call `get_adapter_for_stage()` (starts at M3).
- Any live-provider verification of the OpenAI/NVIDIA NIM/Gemini adapters (opt-in, operator-run,
  not performed in this environment -- no credentials configured).
- Any Candidate Memory bootstrap command, source ingestion, or claims (Milestone M3).
- Any remote/CI configuration (not required; local quality commands remain the standard).

## Decisions (see `docs/DECISIONS.md` for full detail)

D-001 through D-015 are all APPROVED (several "with modification"); D-013 is superseded by D-010.
No decision remains blocking for any milestone through M7.

## Next action

Milestone M3 (Candidate Memory build and confirmation workflow) or M4 (Agent Jobber) may proceed
next -- both depend only on M2, which is now complete, and not on each other.

## Maintenance rule for this file

Update this file after any milestone completes or any meaningful implementation step lands.
Describe the repository as it truly is -- never mark something present, tested, or working that
has not actually been built and verified.
