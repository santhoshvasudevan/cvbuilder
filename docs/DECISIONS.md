# CVBuilder V2 Architectural Decisions

**Status vocabulary:** PROPOSED / APPROVED / SUPERSEDED  
**Date:** 2026-09-08

These V2 decisions replace conflicting V1 planning assumptions once approved by the product owner.

## V2-D001 — Positioning-first objective
**Status:** PROPOSED

Optimize generation for recruiter positioning, synthesis, differentiation, narrative coherence, and persuasive relevance. Evidence/provenance informs generation but must not over-constrain it.

## V2-D002 — Operator owns final factual approval
**Status:** PROPOSED

The operator is the factual authority at Human Gate 2. Potentially weak or inferred wording is warned/reviewed rather than automatically rejected. Static facts remain deterministic.

## V2-D003 — Keep MemoryClaims, broaden candidate context
**Status:** PROPOSED

Retain MemoryClaims where useful, but introduce `CandidateContextSnapshot` with five buckets: direct match, differentiators, career narrative, foundations, gaps/constraints.

## V2-D004 — Minimum sufficient context
**Status:** PROPOSED

Retrieval targets minimum sufficient context, not the minimum count of matching claims.

## V2-D005 — Simplify AC
**Status:** PROPOSED

Do not preserve V1's AC chain (`AC_NORMALIZE → AC_RANK → AC_MATCH`, three calls inside `candidate_matching`) by default, and do not fold `AB_BUILD` (a separate single call inside `resume_builder`) into AC. Use deterministic selection + one `AC_ASSESS` call. Add an extra context call only if measured need appears. (Historical phrasing corrected by V2-D029 — see below.)

## V2-D006 — RecruiterDecisionModel
**Status:** PROPOSED

AJ produces both structured requirements and a recruiter/hiring-manager decision model.

## V2-D007 — APS first-class artifact
**Status:** PROPOSED

Add Agent Positioning Strategy between AC and AB. APS is a durable, editable, versioned artifact.

## V2-D008 — Multi-pass AB
**Status:** PROPOSED

AB uses plan, structured draft, deterministic warnings, recruiter critique, and one controlled refinement. Optional evaluator remains advisory.

## V2-D009 — Exactly three generated experience sections
**Status:** PROPOSED

Current candidate has exactly three primary experience slots. Company name, role title, location, and dates are static operator-maintained content. LLM generates only tailored bullets.

## V2-D010 — Limited initial ResumeDraft schema
**Status:** PROPOSED

Generated initial V2 sections:
- three title options + recommended title
- professional summary
- three experience-section bullet collections
- key achievements
- skills

`selected_projects`, certifications, and languages are deferred from LLM generation.

## V2-D011 — StaticResumeProfile
**Status:** PROPOSED

Create static content for experience metadata and later certifications/languages. Static values always override accidental model-generated duplicates.

## V2-D012 — Human-controlled LLM calls
**Status:** PROPOSED

Every major LLM call is individually inspectable/runnable with input, provider, model, reasoning level, output-token budget, output, token usage, edit/rerun/approve.

## V2-D013 — Provider registry includes OpenRouter
**Status:** PROPOSED

Initial provider set: OpenAI Direct, NVIDIA NIM, Gemini, OpenRouter. Provider base URLs and credential references are registry-driven.

## V2-D014 — Stage-level model economics
**Status:** PROPOSED

Tune provider/model/reasoning/token defaults by measured stage quality and token consumption. Quality per token is the objective.

## V2-D015 — PostgreSQL workflow truth
**Status:** PROPOSED

Durable pipeline/artifact/approval state remains in PostgreSQL. No paused orchestration graph is authoritative.

## V2-D016 — No LangGraph by default
**Status:** PROPOSED

Plain Django/service orchestration remains initial V2 choice. Future framework adoption requires a concrete measured need.

## V2-D017 — Selective reuse from `main`
**Status:** PROPOSED

Do not merge `main`. Perform a read-only reuse audit and selectively cherry-pick/copy stable architecture-neutral components.

## V2-D018 — Advisory factual warnings
**Status:** PROPOSED

Deterministic checks may flag static metadata conflicts, suspicious metrics, duplicate claims, weak evidence, chronology concerns, and unsupported technologies. They are operator warnings except where deterministic static metadata integrity is at risk.

## V2-D019 — Bounded self-evaluation
**Status:** PROPOSED

Default automatic iteration is one `Draft → Critique → Refinement`. More iterations require explicit operator action.

## V2-D020 — Benchmark against expert-assisted output
**Status:** PROPOSED

The project is not considered content-quality complete until representative CVBuilder outputs reach approximately 90% of expert-assisted reference quality.

---

## M0.2 — Architecture Closure Decisions

The decisions below close specific ambiguities/gaps identified in `docs/V2_REUSE_AUDIT.md`. Each is **APPROVED** (product-owner-directed at M0.2) and takes precedence over any earlier PROPOSED text it corrects.

## V2-D021 — Pipeline phase and application outcome remain distinct enums
**Status:** APPROVED

`JobApplication.pipeline_phase` (`NEW`/`ANALYSIS`/`POSITIONING`/`PREPARATION`/`READY`) and `JobApplication.application_outcome` (`NOT_APPLIED`/`APPLIED`/`INTERVIEWING`/`REJECTED`) are two separate fields/enums, never presented as one combined operator-facing status list. This corrects a self-contradiction in an earlier `requirements.md` draft that listed both inside a single list while also stating they "remain separate."

## V2-D022 — StageRun and JobApplicationStageState belong to job_applications; llm_provider owns only the LLM registry and call log
**Status:** APPROVED

`JobApplication` does not accumulate a growing set of `current_*` foreign keys, one per pipeline artifact. Instead, `job_applications` (workflow layer) owns `StageRun` (one execution attempt/version of a workflow stage, LLM-backed or deterministic) and `JobApplicationStageState` (per `JobApplication` + stage: current and approved `StageRun`). Domain artifacts (`JobAnalysis`, `CandidateContextSnapshot`, `CandidateAssessment`, `PositioningStrategy`, `ResumeContentPlan`, `ResumeDraft`, `RecruiterCritique`) are owned by their own apps and each references the `StageRun` that produced them.

`llm_provider` owns `LLMProvider`, `LLMModel`, `StageModelAssignment`, `LLMCallLog`, and provider adapters only — it does not own `StageRun`. `LLMCallLog` references the initiating `StageRun` by a nullable FK (nullable to support standalone/manual smoke-test calls not tied to any `StageRun`). This is a deliberate, accepted exception to the general rule that `llm_provider` has no dependency on pipeline apps.

One canonical stage vocabulary, owned by `job_applications`, is shared by `StageRun`, `JobApplicationStageState`, and `StageModelAssignment`. `StageModelAssignment` applies only to the LLM-capable subset of that vocabulary: `AJ_ANALYZE`, `AC_ASSESS`, `APS_POSITION`, `AB_PLAN`, `AB_DRAFT`, `AB_CRITIQUE`, `AB_REFINE`, `QUALITY_EVAL`. Deterministic/workflow stages — `CANDIDATE_CONTEXT_BUILD`, `VALIDATE_DRAFT`, `VALIDATE_REFINED`, `GATE_1`, `GATE_2`, `RENDER` — use `StageRun` for input/output/audit but never have a `StageModelAssignment` row. Candidate Memory ingestion (V1's `MEMORY_BUILD`) remains outside this vocabulary since it is candidate-scoped, not `JobApplication`-scoped, and keeps its own `CandidateMemory` revision lifecycle.

## V2-D023 — FitExperienceLevel is distinct from MemoryClaim's experience-level classification
**Status:** APPROVED

AC-005's experience-level enum (`AWARENESS`/`LEARNING`/`PROTOTYPE`/`HANDS_ON`/`PRODUCTION`/`ARCHITECTURE`/`LEADERSHIP`) is formalized as a dedicated `FitExperienceLevel` enum used only on `RequirementFit`. It is a distinct concept from `candidate_memory.MemoryClaim`'s own source/claim-level experience classification, which describes what the source evidence itself shows rather than AC's judgment of demonstrated level against a specific requirement. No database enum migration or value-mapping between the two is required or intended.

## V2-D024 — ExperienceSlot is a sequenced related collection with a validation-enforced cardinality
**Status:** APPROVED

`ExperienceSlot` is modeled as a related, ordered collection (FK to `StaticResumeProfile`, `sequence`, `is_primary`, `is_active`), not as three hardcoded database columns. The "exactly three" requirement (STATIC-001) is enforced as a `HARD_INTEGRITY` deterministic check (V2-D026) requiring exactly three `ExperienceSlot` rows with `is_primary=True, is_active=True` before AB-2 (Draft) may run. This keeps the cardinality a business rule rather than a schema shape, so it can be changed later without a model migration if the candidate's career history grows.

## V2-D025 — M3 splits into M3A (Candidate Knowledge + StaticResumeProfile) and M3B (CandidateContextSnapshot)
**Status:** APPROVED

The reuse audit confirmed the static-profile half of M3 is largely pre-built on `main` (`CareerEngagement`, `static_profile_boundary.py`), while the five-bucket `CandidateContextSnapshot` has zero precedent anywhere on `main`. Treating these as one undifferentiated M3 milestone understated the risk/effort of the context-snapshot half. M3A (Candidate Knowledge, `StaticResumeProfile`, `ExperienceSlot`) and M3B (`CandidateContextSnapshot`, five buckets, token-aware compaction) are tracked as distinct sub-milestones in `docs/IMPLEMENTATION_PLAN.md`. M3B requires its own quality acceptance tests (real evidence that all five buckets are populated and career breadth is retained) before AC is allowed to depend on it.

## V2-D026 — Deterministic factual checks split into HARD_INTEGRITY and SOFT_REVIEW_WARNING
**Status:** APPROVED

FACT-003's "warnings, not automatic rejection" is refined into two explicit classes:

- **HARD_INTEGRITY** (fails closed, blocks the draft): malformed structured output, references to a nonexistent claim/experience-slot/object, invalid schema, incorrect required experience-slot cardinality, or any attempt to replace static company/title/date/location metadata.
- **SOFT_REVIEW_WARNING** (surfaced, never blocks): weak support, transferable wording, inferred positioning, possible overstatement, suspicious metrics, technology-depth uncertainty, or wording requiring operator confirmation.

Only `SOFT_REVIEW_WARNING` items are advisory. This also resolves how V1's `no_fabrication.py`/`completeness.py` checks are reused: the check logic (claim-ID existence, engagement-correctness, bullet-count caps) is retained, but reclassified case-by-case as `HARD_INTEGRITY` or `SOFT_REVIEW_WARNING` rather than uniformly raising and discarding the whole draft. Human Gate 2 remains the final factual authority.

## V2-D027 — APS requires explicit quality acceptance criteria before AB implementation
**Status:** APPROVED

APS has no precedent anywhere on `main` and, along with `CandidateContextSnapshot`, is the highest-judgment new component in V2. `docs/IMPLEMENTATION_PLAN.md` M5 and `docs/TEST_STRATEGY.md` define explicit APS quality acceptance criteria (clear candidate thesis, convincing lead/support/de-emphasize strategy, credible title options, retained gaps), evaluated before AB (which consumes APS) is implemented — not deferred to M8's general benchmarking pass.

## V2-D028 — Quality benchmark methodology defined in docs/QUALITY_BENCHMARK.md
**Status:** APPROVED

A dedicated benchmark methodology document compares expert-assisted reference resumes, V1 agent output, and V2 output across positioning, differentiation, JD alignment, specificity, completeness, career narrative, seniority positioning, technical credibility, recruiter impact, generic wording, and factual correction burden. The initial Amazon GenAI Solutions Architect application is recorded as the first representative benchmark case, using only source content actually available — no fabricated benchmark inputs.

## V2-D029 — Correct historical description of V1's AC/AB call chain
**Status:** APPROVED

Earlier V2 planning text described a single "old four-call AC chain" (`AC_NORMALIZE`/`AC_RANK`/`AC_MATCH`/`AC_BUILD`). The reuse audit confirms this spans two apps: three calls (`AC_NORMALIZE`, `AC_RANK`, `AC_MATCH`) inside `candidate_matching`, plus one separate call (`AB_BUILD`) inside `resume_builder` — there is no `AC_BUILD` stage in `candidate_matching`. All canonical V2 documents are corrected to describe this accurately; see `docs/V2_REUSE_AUDIT.md` for the confirmed mapping.

---

## Architecture Closure Decisions (follow-up pass)

The five decisions below close the remaining open architecture questions listed in `docs/CURRENT_STATE.md` and `docs/V2_REUSE_AUDIT.md` after the initial M0.2 pass. Each is **APPROVED** (product-owner-directed).

## V2-D030 — candidate_context is a distinct Django app; candidate_memory remains the source of candidate truth
**Status:** APPROVED

`candidate_context` is a distinct Django/domain app in V2, not an optional submodule of `candidate_memory` (this replaces the earlier hedge in `docs/ARCHITECTURE.md` §3 that it "may live inside `candidate_memory`").

`candidate_memory` owns durable candidate knowledge: candidate source documents, `MemoryClaim`s, Candidate Profile, operator corrections/preferences, `CareerEngagement`-equivalent data, and `StaticResumeProfile`/`ExperienceSlot` source data.

`candidate_context` owns the job-specific projection: `CandidateContextSnapshot`, the five context buckets, retrieval/selection logic, token-aware compaction, context quality validation, and the context inspection/edit workflow.

`CandidateContextSnapshot` consumes `candidate_memory` and the current job/AJ artifacts as inputs but must never become a source of candidate truth in its own right — corrections, confirmations, and factual edits are made in `candidate_memory`, not in a context snapshot.

## V2-D031 — ExperienceSlot selection is explicitly operator-controlled
**Status:** APPROVED

The system does not automatically choose which three career engagements become the resume's primary experience slots. `StaticResumeProfile` contains an ordered, related `ExperienceSlot` collection (V2-D024). The operator selects `CareerEngagement`-equivalent source records (owned by `candidate_memory`, V2-D030) and creates/activates exactly three primary `ExperienceSlot`s with explicit sequence/order.

Each `ExperienceSlot` contains/references static operator-owned metadata: company, position title, location, start date, end date/present, and sequence. Whether this metadata is copied onto `ExperienceSlot` or resolved by reference to its source `CareerEngagement`-equivalent record is an M3A implementation detail, not fixed here.

Exactly three active primary `ExperienceSlot`s remains a `HARD_INTEGRITY` prerequisite for resume generation (V2-D024/V2-D026) — this decision does not change the cardinality rule, only how slots come to exist.

The M3A UI mechanism supports this flow: select engagement → create/activate slot → order (1/2/3) → edit static metadata if authorized → validate exactly three.

## V2-D032 — ReviewFeedback targets a StageRun, not a Target enum
**Status:** APPROVED

`ReviewFeedback` does not introduce separate enum values for `AB_PLAN`/`AB_DRAFT`/`AB_CRITIQUE`/`AB_REFINE` (or any other per-stage target). Feedback becomes stage-specific through the workflow model instead:

```text
ReviewFeedback
    job_application     (FK)
    stage_run           (FK -> StageRun, nullable)
    gate                (GATE_1 / GATE_2)
    comment
    created_at
```

`StageRun.stage` (the canonical stage vocabulary, V2-D022) already identifies the exact stage/run/version/model execution a piece of feedback concerns, so a separately-evolving `ReviewFeedback.Target` enum is unnecessary and would drift from the real stage vocabulary over time. `stage_run` is nullable to allow aggregate Gate 1/Gate 2 feedback that is not about one specific LLM run; `gate` identifies the review scope in that case.

## V2-D033 — AJ requirement taxonomy uses orthogonal dimensions, not one overloaded category enum
**Status:** APPROVED

Job requirements have independent dimensions that V1's single flat category conflated. V2's `JobRequirement` captures at least three separate dimensions:

```text
requirement_priority:  MANDATORY | PREFERRED | CONTEXTUAL
requirement_domain:    TECHNICAL | DOMAIN | LEADERSHIP | CUSTOMER_FACING |
                        RESPONSIBILITY | EDUCATION_CERTIFICATION | OTHER
origin:                EXPLICIT | IMPLIED
```

Exact stored enum naming may be refined during M4 implementation, but these three separate dimensions are now the architectural contract — no field collapses priority, domain, and origin into one enum. `RecruiterDecisionModel` (AJ-003) may rank requirements across these dimensions. `RequirementFit` (AC) continues to reference requirements only by their stable `JR-NNN` ID, so this change is invisible to AC's schema.

## V2-D034 — supported_reasoning_levels is the canonical source of a model's reasoning capability
**Status:** APPROVED

`LLMModel.supported_reasoning_levels` (a set/list drawn from canonical values `NONE`, `LOW`, `MEDIUM`, `HIGH`, `XHIGH`) is the single source of truth for what a model supports. Provider adapters translate these canonical values into provider-specific request parameters. A model with no reasoning capability stores `{NONE}` (or an equivalent empty/`NONE`-only representation) — there is no independently-stored `supports_reasoning` boolean that could drift from the level list; `supports_reasoning` may exist only as a derived property/helper computed from `supported_reasoning_levels`.

`StageModelAssignment.default_reasoning_level` must be valid for its selected model's `supported_reasoning_levels`. Per-call UI overrides are validated against the same set. This must be settled before M1/M2 schema work, since `StageModelAssignment` and the registry admin depend on it.

---

## V2-D035 — Repository-native multi-agent continuity
**Status:** APPROVED

The repository must support safe continuation by a different coding agent (Claude Code, Codex, or any future tool) without access to the previous agent's conversational context. This is achieved entirely through repository-native mechanisms, never through assumed session memory:

- canonical repository documentation (`requirements.md`, this document, `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/TEST_STRATEGY.md`, `docs/REQUIREMENT_TRACEABILITY.md`);
- Git history (branch, commits, diffs);
- deterministic tests;
- `docs/CURRENT_STATE.md`, kept concise and operational, verified rather than trusted;
- an explicit handover protocol (`docs/HANDOVER_PROTOCOL.md`) covering both clean milestone handover and emergency/mid-task handover;
- a reproducible environment/configuration (Docker/Postgres, `.env.example`, and canonical local commands, once M1 establishes them).


The tool-neutral entry point for this is `AGENTS.md`, backed by `docs/ENGINEERING_RULES.md` (the detailed engineering agreement), `docs/HANDOVER_PROTOCOL.md`, and `docs/MILESTONE_COMPLETION_CHECKLIST.md`. Tool-specific files (`CLAUDE.md`) point to these rather than duplicating them, so the rules stay in one place regardless of which agent reads them.

---

## M1 Implementation Decisions

## V2-D036 — M1 implements only `job_applications`; StageRun's provider/model fields are added in M2
**Status:** APPROVED

M1 implements exactly one Django app, `job_applications`, containing `JobApplication`, `StageRun`, and `JobApplicationStageState`. Every other app boundary documented in `docs/ARCHITECTURE.md` §3 (`job_intake`, `candidate_memory`, `candidate_context`, `candidate_matching`, `positioning_strategy`, `resume_builder`, `reviews`, `llm_provider`) is created when its own milestone begins, not pre-scaffolded now — pre-creating empty app shells ahead of their milestone would be exactly the kind of speculative abstraction the M1 task explicitly disallowed.

This has one direct schema consequence: `docs/ARCHITECTURE.md` §5's full `StageRun` schema includes `provider` (FK → `llm_provider.LLMProvider`), `model` (FK → `llm_provider.LLMModel`), `reasoning_level`, `max_output_tokens`, and `temperature`. Since `llm_provider` does not exist in M1, `StageRun` cannot carry real foreign keys to it yet. M1's `StageRun` therefore implements only the provider-independent fields — `job_application`, `stage`, `status`, `input_snapshot`, `raw_structured_output`, `working_output`, `lock_version`, `created_at`, `approved_at`. M2 adds the five remaining fields via a new migration once `llm_provider.LLMProvider`/`LLMModel` exist.

This is an implementation-sequencing note, not an architecture change: `docs/ARCHITECTURE.md` §5's target schema for `StageRun` is unchanged. A test in `job_applications/tests/test_models.py` (`test_stage_run_has_no_provider_model_fields_yet`) asserts the M1-scoped field set explicitly, so its removal in M2 is a deliberate, visible change rather than a silent one.

## V2-D037 — M1 local Postgres database reset (stale V1 schema found and cleared)
**Status:** APPROVED

The local Docker-managed PostgreSQL volume (`cvbuilder_postgres_data`) already contained a full V1 (`main`-branch) schema and a `django_migrations` row for `("job_applications", "0001_initial")` from V1's different `JobApplication` model, left over from unrelated prior work against the same container name. Because Django matches migrations by `(app_label, name)` string only, this stale row caused `manage.py migrate` to silently report "no migrations to apply" without ever creating V2's actual `StageRun`/`JobApplicationStageState` tables.

This local, disposable, non-source-controlled database was reset (`docker compose down -v` then `up -d`) before M1's real migration was applied, and re-verified against the resulting fresh schema. This is not a "discard another agent's work" situation (`docs/HANDOVER_PROTOCOL.md` §B) — the data was V1 experimentation state with no relationship to `cvbuild2`'s git history, not uncommitted work belonging to this branch or task. Recorded here so a future agent does not mistake a similarly-contaminated local database for a genuine V2 migration failure.
