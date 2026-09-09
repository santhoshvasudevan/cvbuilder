# CVBuilder V2 Requirement Traceability

**Status:** V2 planning baseline — implementation not yet assessed  
**Date:** 2026-09-08

| Requirement area | IDs | Primary component | Milestone | Verification |
|---|---|---|---|---|
| Expert-quality positioning | GOAL | whole pipeline | M5-M8 | benchmark vs expert-assisted reference |
| Human-owned factual approval | FACT-001..005 | reviews / resume_builder | M6 | Gate 2 walkthrough |
| Job tracking | §4 | job_applications | M7 | dashboard/integration tests |
| Pipeline phase / application outcome separation | V2-D021 | job_applications | M1 | model test — VERIFIED (`JobApplicationTests.test_pipeline_phase_and_application_outcome_are_independent_fields`) |
| StageRun / JobApplicationStageState | V2-D022 | job_applications | M1/M2 | model/migration test — VERIFIED (migration `job_applications/0001_initial.py` applied to real PostgreSQL; model tests pass); provider/model/reasoning_level/max_output_tokens/temperature added in M2 (V2-D036, migrations `job_applications/0002_*`/`0003_*`) — VERIFIED (`StageRunTests`, fresh-DB migration) |
| Canonical stage vocabulary | V2-D022 | job_applications/llm_provider | M1/M2 | enum/routing test — VERIFIED for M1 (`StageIdentifierTests`, incl. no-legacy-identifier check); `StageModelAssignment` wiring — M2 VERIFIED (`llm_provider.models.LLM_CAPABLE_STAGE_CHOICES` restricted to the shared vocabulary, `StageModelAssignmentTests`) |
| Job intake | AJ-001 | job_intake | M4 | URL/paste tests |
| Stable job requirements | AJ-002 | job_intake | M4 | schema/unit test |
| Orthogonal requirement taxonomy (priority/domain/origin) | AJ-002, V2-D033 | job_intake | M4 | schema/enum test |
| Recruiter Decision Model | AJ-003..005 | job_intake | M4 | schema + manual quality review |
| Language/source retention | AJ-006..007 | job_intake | M4 | unit/manual tests |
| Candidate profile | §6.1 | candidate_memory | M3A | model/UI tests |
| candidate_context is a distinct app; candidate_memory is source of truth | V2-D030 | candidate_memory/candidate_context | M1/M3A/M3B | app-boundary/import review |
| Candidate preferences | CTX-004 | candidate_memory | M3A | UI/model test |
| Three static experience slots (sequenced collection) | STATIC-001, V2-D024 | static profile | M3A | model + HARD_INTEGRITY test |
| Operator-controlled ExperienceSlot selection UI | V2-D031 | static profile / candidate_memory | M3A | UI walkthrough + HARD_INTEGRITY test |
| Static company/date/title/location | STATIC-002 | static profile / renderer | M3A/M6 | renderer test |
| Static cert/language later | STATIC-003 | static profile | M3A/M6 | absence of LLM generation |
| Projects deferred | STATIC-004 | future | Deferred | n/a |
| Minimum sufficient context | CTX-001 | candidate_context | M3B | retrieval regression test |
| Five context buckets | CTX-002 | candidate_context | M3B | fixture test |
| Career narrative | CTX-003 | candidate_context | M3B | manual/fixture test |
| Token-aware context | CTX-005 | candidate_context | M3B/M8 | token report |
| Context snapshot | CTX-006 | candidate_context | M3B | persistence/A-B test |
| Editable context | CTX-007 | candidate_context/UI | M3B | UI walkthrough |
| CandidateContext quality gate before AC depends on it | V2-D025 | candidate_context | M3B | benchmark/fixture test, gates M5 |
| Holistic AC | AC-001 | candidate_matching | M5 | manual benchmark |
| RequirementFit | AC-002 | candidate_matching | M5 | schema test |
| Dispositions | AC-003 | candidate_matching | M5 | enum test |
| Evidence strength | AC-004 | candidate_matching | M5 | enum test |
| Experience level (FitExperienceLevel, distinct from MemoryClaim's) | AC-005, V2-D023 | candidate_matching | M5 | enum test |
| Differentiators | AC-006 | candidate_matching | M5 | manual benchmark |
| Career narrative | AC-007 | candidate_matching | M5 | manual benchmark |
| Gaps retained | AC-008 | candidate_matching | M5 | fixture/manual test |
| PositioningStrategy | APS-001..009 | positioning_strategy | M5 | schema/manual quality review; must pass before M6 begins (V2-D027) |
| Gate 1 | §10 | reviews | M5 | walkthrough |
| ReviewFeedback targets StageRun, not a Target enum | V2-D032 | reviews | M1/M5/M6 | model test |
| AB plan | AB-001 | resume_builder | M6 | schema test |
| AB structured draft | AB-002 | resume_builder | M6 | schema test |
| Deterministic warnings | AB-003 | resume_builder | M6 | fixture tests |
| Hard integrity vs soft review warnings | FACT-003, V2-D026 | resume_builder | M6 | fixture tests (both classes) |
| Recruiter critique | AB-004 | resume_builder | M6 | schema/manual review |
| Controlled refinement | AB-005 | resume_builder | M6 | diff/regression test |
| Bounded loop | AB-006 | resume_builder | M6 | workflow test |
| Optional evaluator | AB-007 | resume_builder | M6 | optional UI test |
| V2 ResumeDraft structure | §12 | resume_builder | M6 | rendering contract test |
| Gate 2 | §14 | reviews | M6 | walkthrough |
| Pipeline UI | UI-001..010 | owning apps/job_applications | M2-M7 | UI walkthrough/tests |
| Provider independence | LLM-001 | llm_provider | M2 | import/routing tests — M2 VERIFIED (no provider SDK import anywhere in the repo; only `requests` in `llm_provider.adapters`, confirmed by source grep) |
| Provider/model registry | LLM-002..006 | llm_provider | M2 | model/admin tests — M2 VERIFIED (`LLMProviderTests`, `LLMModelTests`, `StageModelAssignmentTests`, `test_admin.py`); temperature-applicable call parameter (requirements.md line 730, "...temperature where applicable") — M2 correction VERIFIED (V2-D042: `LLMModel.supports_temperature`/`supports_temperature_with_reasoning` explicit capability metadata, `validate_temperature_supported` pre-flight, `TemperatureValidationTests` + `StageModelAssignmentTests` temperature cases) |
| Structured output | LLM-007 | llm_provider | M2 | adapter tests — M2 VERIFIED (`test_schema_translation.py`, `test_real_adapter_config_guards.py`) |
| Canonical per-model reasoning levels (supported_reasoning_levels, no independent supports_reasoning) | LLM-004, V2-D034 | llm_provider | M2 | model/validation test — M2 VERIFIED (`LLMModel.supports_reasoning` is a derived property only; `test_clean_rejects_reasoning_levels_missing_none`, `validate_reasoning_level` tests) |
| OpenRouter | LLM-008 | llm_provider | M2 | adapter/smoke test — M2 VERIFIED (`OpenRouterAdapter` is one `ADAPTER_CLASSES` entry among four, no special-cased pipeline logic; `OpenRouterAdapterTests`) |
| Editable URLs | LLM-009 | llm_provider | M2 | registry test — M2 VERIFIED (`LLMProvider.base_url`, blank falls back to the adapter's `DEFAULT_BASE_URL`, no code change required to override) |
| Model experiments | LLM-010..011 | llm_provider | M2/M8 | same-input rerun test — M2 VERIFIED for M2 scope (`compare_models`, `test_console.py`: identical stored input rerun against independent models, each writing its own `LLMCallLog`, no fallback/merge); full A/B against a real `StageRun.input_snapshot` awaits a real LLM-capable stage (M4+) |
| Secrets | LLM-012 | project-wide | M1/M2 | config/VCS review — M1 VERIFIED (`detect-secrets scan` clean; `.env` confirmed untracked by an automated test; `.env.example` contains placeholders only); M2 VERIFIED (`LLMProvider.credential_env_variable` stores only a variable name, never a value — `test_credential_value_never_stored`; `detect-secrets scan` remains clean after M2); M2 independent re-audit found a BLOCKER on the *audit-log* half of this requirement (a credential could reach `LLMCallLog.error_message` via a URL-embedded Gemini key on a network failure) — CLOSED by the M2 correction (V2-D043: `x-goog-api-key` header transport, hardened `sanitize_error_message`, `classify_network_exception`; `GeminiAdapterTests` credential-non-leak regressions) |
| Token optimization | TOKEN-001..006 | llm_provider/reporting | M2/M8 | usage reports — M2 VERIFIED for TOKEN-001 only (`LLMCallLog` captures input/cached-input/output/total tokens per call); TOKEN-002..006 (compaction, reporting, tuning) remain M8 |
| Selective main reuse | §20 | project-wide | M1/M2 | reuse audit — see `docs/V2_REUSE_AUDIT.md`; M1 infra reuse VERIFIED (file-by-file cherry-pick from `main`, no bulk merge); M2 `llm_provider` reuse VERIFIED (file-by-file from `main`'s original M2 commit `8627a93` + OpenRouter from `a541a0a`, adapted per V2-D039/040/041, no bulk merge — `docs/IMPLEMENTATION_PLAN.md` M2 implementation note) |
| Quality benchmark methodology | V2-D028 | project-wide | M8 | `docs/QUALITY_BENCHMARK.md` |
| Repository-native multi-agent continuity | §23, V2-D035 | project-wide | M0.2/ongoing | `AGENTS.md`, `docs/ENGINEERING_RULES.md`, `docs/HANDOVER_PROTOCOL.md`, `docs/MILESTONE_COMPLETION_CHECKLIST.md` present and followed |
| Plain Django orchestration | §21 | project-wide | M1-M7 | architecture/code review |
