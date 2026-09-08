# CVBuilder V2 Repository Instructions

This repository is being redesigned on branch `cvbuild2`.

## Before changing anything

1. Confirm current branch and HEAD.
2. Read `requirements.md` completely.
3. Read:
   - `docs/CURRENT_STATE.md`
   - `docs/ARCHITECTURE.md`
   - `docs/IMPLEMENTATION_PLAN.md`
   - `docs/DECISIONS.md`
   - `docs/TEST_STRATEGY.md`
   - `docs/REQUIREMENT_TRACEABILITY.md`
   - `docs/RESUME_OUTPUT_STRUCTURE.md`
4. Inspect actual repository state.
5. Never assume `main` implementation exists on this branch.

## V2 Product Priority

The primary goal is expert-quality candidate positioning and persuasive relevance.

Do not optimize the system into an overly conservative fact extractor.

The operator owns final factual approval.

Preserve provenance/warnings where useful, but do not weaken positioning quality solely to satisfy exact-claim matching.

## Load-Bearing V2 Design Rules

- AJ produces a Recruiter Decision Model.
- Candidate retrieval uses minimum sufficient context.
- CandidateContext has five buckets: direct match, differentiators, career narrative, foundations, gaps/constraints.
- AC is one strong assessment call by default; do not recreate V1's `AC_NORMALIZE`/`AC_RANK`/`AC_MATCH` chain (three calls inside `candidate_matching`) or fold `AB_BUILD` (a separate single call inside `resume_builder`) back into AC without fresh evidence — see `docs/V2_REUSE_AUDIT.md` for the confirmed historical mapping.
- APS is a first-class artifact.
- AB is multi-pass: plan → draft → warnings → recruiter critique → one refinement.
- Default self-evaluation is bounded; no unbounded loops.
- ResumeDraft generates three title options + recommended title, summary, exactly three experience bullet sections, key achievements, and skills.
- Company/date/title/location metadata is static and not model-generated.
- `ExperienceSlot` is a sequenced related collection, not fixed database columns; exactly three active primary slots are required by a HARD_INTEGRITY check, not a schema constraint (V2-D024).
- Certifications/languages are not LLM-generated initially.
- selected projects are deferred.
- Every major LLM call is operator-inspectable and configurable.
- `StageRun` and `JobApplicationStageState` belong to `job_applications` (workflow layer), not `llm_provider`. `llm_provider` owns only `LLMProvider`/`LLMModel`/`StageModelAssignment`/`LLMCallLog`/adapters; `LLMCallLog` references the initiating `StageRun` (V2-D022).
- One canonical stage vocabulary (owned by `job_applications`) is shared by `StageRun`, `JobApplicationStageState`, and `StageModelAssignment`; `StageModelAssignment` only applies to LLM-capable stages, never to deterministic/workflow stages.
- Deterministic checks split into `HARD_INTEGRITY` (fails closed: malformed output, dangling object references, static-metadata corruption, wrong experience-slot cardinality) and `SOFT_REVIEW_WARNING` (never blocks: weak evidence, overstatement, inferred positioning). Only soft warnings are advisory (V2-D026).
- AC's `FitExperienceLevel` is distinct from `MemoryClaim`'s experience-level classification in Candidate Memory; no forced enum migration between them (V2-D023).
- `candidate_context` is a distinct Django app, not a submodule of `candidate_memory`. `candidate_memory` is the source of candidate truth; `candidate_context` builds a job-specific projection from it and must never become a source of truth itself (V2-D030).
- `ExperienceSlot` selection is explicitly operator-controlled — never automatic. The operator selects source engagement records and creates/activates exactly three primary slots with explicit order (V2-D031).
- `ReviewFeedback` targets a `StageRun` (nullable, plus a `gate` field), not a separately-maintained `Target` enum — `StageRun.stage` already identifies what the feedback concerns (V2-D032).
- AJ's `JobRequirement` captures three orthogonal dimensions (`requirement_priority`, `requirement_domain`, `origin`), never one flat category enum (V2-D033).
- `LLMModel.supported_reasoning_levels` is the canonical source of a model's reasoning capability; `supports_reasoning` is derived only, never independently stored (V2-D034).
- Initial providers: OpenAI, NVIDIA NIM, Gemini, OpenRouter.
- Pipeline apps never call provider SDKs directly.
- Provider/model/reasoning/token defaults are stage-specific and runtime-overridable.
- Token usage matters more than dollar-cost calculation.
- Durable workflow state remains in PostgreSQL.
- No LangGraph/LangChain/Agents SDK workflow dependency by default.

## Reuse From `main`

Do not merge `main` wholesale.

The read-only reuse audit is complete — see `docs/V2_REUSE_AUDIT.md` for the full `REUSE_AS_IS` / `REUSE_WITH_ADAPTATION` / `DO_NOT_REUSE` classification, exact files/commits, and recommended reuse order. That document is an audit/history record, not a canonical architecture source — `docs/ARCHITECTURE.md` and `docs/DECISIONS.md` govern where they differ.

Prefer selective cherry-pick/file reuse with tests.

Confirmed safe candidates are infrastructure/UI/provider primitives and the Candidate Memory ingestion pipeline. V1's `AC_NORMALIZE`/`AC_RANK`/`AC_MATCH` orchestration (`candidate_matching`), the separate `AB_BUILD` call (`resume_builder`), strict no-fabrication gates, and old renderer assumptions require redesign review.

## Working Process

Work one milestone at a time.

Before implementation of a milestone:
- restate scope;
- identify requirements;
- identify reuse candidates;
- identify tests;
- check git status.

After implementation:
- run real verification;
- update CURRENT_STATE;
- update traceability;
- report files changed;
- report tests;
- report unresolved issues.

Never weaken a test merely to make it pass.

Do not automatically commit unless explicitly asked.
