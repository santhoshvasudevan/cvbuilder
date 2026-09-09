# CVBuilder V2 Test Strategy

**Status:** Proposed  
**Date:** 2026-09-08

V2 testing has three distinct layers:

1. deterministic correctness tests,
2. live/manual provider qualification,
3. human content-quality evaluation.

Do not confuse these layers.

## 1. Deterministic Tests

### Foundation
- Django loads.
- PostgreSQL is the configured application database.
- `.env` is not committed.
- no Redis/Celery/SPA dependency appears without approval.

**M1 status: VERIFIED.** `job_applications/tests/` (51 tests as of the M1 re-audit correction — see `docs/CURRENT_STATE.md`) covers Django startup, settings/environment validation, PostgreSQL configuration (including a live query against a real connection), the home view and URL routing, admin registration/reachability, `StageRun`/`JobApplicationStageState` deletion behavior, and M1's architectural invariants (no legacy V1 stage identifiers or app names, `.env` not tracked by git, `.env.example` placeholder-only). Run via `make test`; `make check`, `make migrations-check`, and `make lint` are also part of `make verify`. Canonical local commands live in the root `Makefile` — see `AGENTS.md`.

### LLM provider
- adapter routing;
- provider/model registry;
- stage defaults;
- runtime overrides;
- structured-output translation;
- retry classification;
- error sanitization;
- token logging;
- identical input snapshot used for model A/B runs;
- `reasoning_level` rejected (at `StageModelAssignment` save time and on a per-call override) when it is not a member of the selected model's `supported_reasoning_levels` (V2-D034).

**M2 status: VERIFIED (post-correction).** `llm_provider/tests/` (179 tests as of the second M2 independent re-audit correction — see `docs/DECISIONS.md` V2-D042/V2-D043/V2-D044 and `docs/CURRENT_STATE.md`) covers registry model validation/constraints/deletion-protection, pre-flight "fails before HTTP" configuration validation (inactive provider/model, missing/unset credential, unsupported structured output/reasoning, output-budget ceiling, temperature capability/range/reasoning-combination — V2-D042/V2-D044, including that the two temperature capability flags both default `False` and that `supports_temperature_with_reasoning=True` requires `supports_temperature=True`), retry classification (including proof that retry never substitutes a different provider/model), Gemini/OpenAI schema translation, all four real adapters' request-building and response-parsing with `requests.post` mocked (zero live calls), the `FakeAdapter` end-to-end call path (validation → retry → schema re-validation → audit log), routing (`get_adapter_for_stage`/`get_adapter_for_model`, including proof no fallback occurs across stages/models), the manual-run/model-comparison console, Django admin (including that `LLMCallLog` is add/change-locked), and the opt-in smoke-test management command's deterministic (no-credential) branches. Automated tests never require live credentials and never reach the network — every `requests.post` call site in a real-provider adapter test is mocked.

**Error sanitization (corrected scope, V2-D043):** the pre-correction claim above that "error sanitization" was verified covered only JSON/dict-body redaction — it did not cover URL/query-string credential leakage, which is exactly how the M2 independent re-audit's BLOCKER finding (a Gemini API key embedded in the request URL, unredacted by the then-current `sanitize_error_message`, and reachable through any transient network failure) got past it. The correction adds `URLAndQueryStringSanitizationTests` and `NetworkExceptionClassificationTests` (`llm_provider/tests/test_errors.py`), and Gemini-specific regression coverage in `llm_provider/tests/test_real_adapter_config_guards.py::GeminiAdapterTests` (a request-shape test proving no credential/`key=` ever appears in the outgoing URL, and five simulated-failure tests — connection-refused, DNS, TLS, generic connection error, timeout — each proving a synthetic, credential-bearing exception message cannot surface in the raised error, `LLMCallLog.error_message`, or any other field of the persisted audit-log row). Every assertion in this coverage checks behavior (the synthetic credential's absence from real output) rather than merely grepping source for a redaction call.

**Reasoning-vocabulary drift guard (V2-D041/V2-D043):** `job_applications/tests/test_models.py::ReasoningLevelVocabularyDriftTests` asserts `set(job_applications.models._REASONING_LEVEL_VALUES) == set(llm_provider.models.ReasoningLevel.values)` directly — the regression guard V2-D041's documented duplication was missing until the M2 correction. Confirmed still green after V2-D044.

**Temperature capability fail-closed defaults (V2-D044):** a second independent re-audit of the first correction found `LLMModel.supports_temperature`/`supports_temperature_with_reasoning` defaulted `True` (fail-open) — inconsistent with `supports_structured_output`/`supported_reasoning_levels`'s fail-closed pattern on the same model row, and empirically shown to silently grant capability to pre-existing rows on migration. `LLMModelTests.test_temperature_capability_fields_default_to_false` asserts the ORM-level field default directly; `test_clean_rejects_temperature_with_reasoning_when_temperature_itself_unsupported` proves `LLMModel.clean()` now rejects `supports_temperature_with_reasoning=True` combined with `supports_temperature=False` (closing a second MINOR finding); `SaveTimeCallTimeParityTests` (`test_validation.py`) proves `StageModelAssignment.clean()` and `validate_temperature_supported` agree on every tested configuration, so a runtime override cannot be accepted where a stored assignment would have been rejected or vice versa; `OpenAIAdapterTests.test_invalid_temperature_never_reaches_http` (closing a third MINOR finding) proves an invalid temperature configuration never reaches `requests.post`, triggers no retry, and is classified `CONFIGURATION`.

### Candidate context
Fixtures verify that context includes:
- direct match;
- differentiators;
- career narrative;
- foundations;
- gaps/constraints.

A regression test should prevent over-aggressive retrieval from dropping all earlier-career context.

These tests gate M3B's acceptance criteria (V2-D025) — AC (M5) must not depend on `CandidateContextSnapshot` until they pass on real candidate data.

### Static resume profile
- `ExperienceSlot` is a related, sequenced collection, not fixed columns — a HARD_INTEGRITY check enforces exactly three `ExperienceSlot` rows with `is_primary=True, is_active=True` for the configured candidate (V2-D024);
- generated draft references slots by ID;
- model output cannot overwrite static company/date/title/location metadata (HARD_INTEGRITY, V2-D026);
- renderer uses static metadata;
- `ExperienceSlot` creation/activation only occurs through the operator-controlled selection UI (select engagement → create/activate → order → validate), never automatically (V2-D031).

### AJ
Schema tests for stable requirement IDs, the three orthogonal requirement dimensions (`requirement_priority`/`requirement_domain`/`origin` — V2-D033), role identity, ranked hiring signals, screen-out risks, recruiter questions, and RecruiterDecisionModel fields.

### AC
Schema tests for disposition values, evidence-strength values, experience-level values, gaps retained, and differentiators supported by supplied context.

### APS
Schema tests for required positioning fields.

### AB
- exactly three title options;
- exactly three experience-section references;
- no generated static metadata;
- duplicate detection;
- suspicious metric warning;
- section completeness;
- critic schema;
- refinement preserves unchanged content where instructed.

Of the above, "no generated static metadata" and correct experience-slot cardinality are `HARD_INTEGRITY` checks (fail closed); duplicate detection and suspicious-metric warnings are `SOFT_REVIEW_WARNING` checks (surfaced, never block) — see V2-D026.

### UI/workflow
- StageRun status persists after restart;
- operator edit does not delete original model output;
- approval/rerun works;
- current pipeline stage derives from DB state;
- application outcome is independent from generation phase;
- `ReviewFeedback` correctly references its `StageRun` (or is nullable for aggregate gate-level feedback), with no separate `Target` enum (V2-D032).

## 2. Live Provider Qualification

Manual opt-in only.

For each configured provider/model:
- execute minimal structured output;
- verify response parsing;
- verify usage metadata;
- record safe call metadata;
- do not persist secrets/raw sensitive provider errors.

Provider status:
- `NOT_TESTED`
- `VERIFIED`
- `FAILED`
- `NOT_CONFIGURED`

## 3. Content-Quality Evaluation

This is essential in V2.

Use representative real jobs and score outputs on:
- Job Alignment
- Candidate Differentiation
- Recruiter Impact
- Specificity
- Career Coherence
- Seniority Positioning
- Technical Credibility
- Domain Positioning
- ATS Coverage
- Conciseness
- Completeness
- Correction Burden

Use a 1–10 scale.

Compare:
- expert-assisted reference;
- one-shot baseline;
- V2 multi-pass output;
- alternative models.

See `docs/QUALITY_BENCHMARK.md` for the full benchmark methodology, including the initial Amazon GenAI Solutions Architect representative benchmark (V2-D028).

## 4. Stage-Specific Quality Checks

### AJ
- Did AJ understand what the job really is?
- Are hiring signals ranked correctly?
- Are screen-out risks realistic?
- Are recruiter questions useful?

### AC
- Did AC use enough candidate breadth?
- Did it miss transferable strengths?
- Did it retain career progression?
- Are experience levels realistic?
- Are gaps preserved?

### APS
- Is there a clear candidate thesis?
- Is lead/support/de-emphasize strategy convincing?
- Does narrative explain why this candidate fits?
- Does it answer recruiter questions?

These APS quality checks must pass on at least one representative real job application before AB (M6) implementation begins (V2-D027).

### AB
- Is the opening strong?
- Is the draft generic?
- Does it use strongest evidence prominently?
- Is candidate differentiated?
- Is career progression coherent?
- Does critique identify real weaknesses?
- Does refinement materially improve the draft?

## 5. Token Evaluation

For every real benchmark capture:
- input tokens;
- cached input tokens;
- output tokens;
- total tokens;
- latency;
- retries;
- stage;
- provider;
- model;
- reasoning level;
- configured output budget.

Calculate quality-per-token comparisons in M8.

## 6. Acceptance Target

Representative final resume content should reach approximately **90% or better of expert-assisted reference quality**, measured per the methodology in `docs/QUALITY_BENCHMARK.md`.

A green deterministic test suite alone does not satisfy this criterion.
