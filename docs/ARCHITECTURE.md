# CVBuilder V2 Architecture

**Status:** Proposed for V2 implementation  
**Date:** 2026-09-08

## 1. Architectural Objective

Make expert-quality candidate positioning the primary generation goal while preserving operator control, broad candidate context, provider independence, model experimentation, token visibility, durable workflow state, and advisory provenance/warnings.

V2 intentionally moves away from a pipeline dominated by strict claim-level blocking.

## 2. High-Level Flow

```text
JobApplication
   │
   ├── AJStageRun
   │     └── JobAnalysis + RecruiterDecisionModel
   ├── CandidateContextSnapshot
   ├── ACStageRun
   │     └── CandidateAssessment
   ├── APSStageRun
   │     └── PositioningStrategy
   ├── Human Gate 1
   ├── ABPlanRun
   ├── ABDraftRun
   ├── deterministic warnings
   ├── ABCritiqueRun
   ├── ABRefinementRun
   ├── optional QualityEvaluationRun
   └── Human Gate 2
          └── final structured ResumeDraft + markdown
```

## 3. Django App Boundaries

### `job_applications`

Owns `JobApplication` (job identity, pipeline phase, application outcome, timestamps), `StageRun` (a single execution attempt/version of any workflow stage, LLM-backed or deterministic), `JobApplicationStageState` (per `JobApplication` + stage: the current and approved `StageRun`), dashboard lifecycle, workflow navigation, and Human Gate transition rules. `JobApplication` does not hold a direct pointer per pipeline artifact — see §5. Domain artifacts (`JobAnalysis`, `CandidateContextSnapshot`, `CandidateAssessment`, `PositioningStrategy`, `ResumeContentPlan`, `ResumeDraft`, `RecruiterCritique`) are owned by their own domain apps but each references the `StageRun` that produced it.

### `job_intake`

Owns URL/pasted intake, job fetch/fallback, `JobRequirementAnalysis`, stable `JobRequirement`, `RecruiterDecisionModel`, and AJ stage service.

### `candidate_memory`

**Closed in M0.2 follow-up (V2-D030).** Owns durable candidate knowledge and remains the source of candidate truth: candidate source documents, `MemoryClaim`s, Candidate Profile, operator corrections/preferences, career direction, `CareerEngagement`-equivalent data, and `StaticResumeProfile`/`ExperienceSlot` source data.

### `candidate_context`

**Closed in M0.2 follow-up (V2-D030).** A distinct Django app, not an optional submodule of `candidate_memory`. Owns the job-specific projection: `CandidateContextSnapshot`, the five context buckets, retrieval/selection logic, token-aware compaction, context quality validation, and the context inspection/edit workflow. `CandidateContextSnapshot` consumes `candidate_memory` and the current job/AJ artifacts as inputs but must never become a source of candidate truth in its own right — corrections and confirmations happen in `candidate_memory`, never in a context snapshot.

### `candidate_matching`

Owns `CandidateAssessment`, `RequirementFit`, AC assessment, evidence strength, experience-level classification, gap analysis, and transferable framing.

### `positioning_strategy`

New first-class app/boundary. Owns `PositioningStrategy`, APS generation/versioning, candidate thesis, lead/support/de-emphasize decisions, career narrative strategy, and title strategy.

### `resume_builder`

Owns `ResumeContentPlan`, structured `ResumeDraft`, AB draft, recruiter critique, refinement, deterministic warning checks, optional quality evaluation, and markdown renderer.

### `reviews`

Owns Human Gates 1/2, `ReviewFeedback` (operator feedback — see §5), edit/approval status, and artifact version relationships.

### `llm_provider`

Owns provider/model registry, stage defaults, runtime overrides, provider adapters, structured-output translation, retry/error handling, `LLMCallLog`, and model comparison support. Does not own `StageRun` — see §5/§13 (V2-D022).

## 4. Durable State

PostgreSQL is the source of truth. No workflow correctness depends on an in-memory agent session.

Major artifacts are versioned/immutable after creation. Operator edits create a working or derived version while preserving the original provider result.

## 5. JobApplication Aggregate, StageRun, and JobApplicationStageState

**Closed in M0.2 (V2-D022).** `JobApplication` stays a small, stable aggregate. It does not grow a `current_*` foreign key per pipeline artifact. Per-stage execution state is owned by two workflow-layer models instead.

```text
JobApplication
    employer
    job_title
    source_type
    source_url
    pipeline_phase
    application_outcome
    created_at
    updated_at
```

Pipeline phase (internal preparation progress only): `NEW`, `ANALYSIS`, `POSITIONING`, `PREPARATION`, `READY`.

Application outcome (external, independent of pipeline phase): `NOT_APPLIED`, `APPLIED`, `INTERVIEWING`, `REJECTED`.

These two enums are never merged into one operator-facing status list (V2-D021). `pipeline_phase` advances as internal preparation completes; `application_outcome` is set by the operator once the resume is `READY` and moves independently thereafter.

```text
StageRun
    id
    job_application            (FK)
    stage                      (canonical stage identifier — see Canonical Stage Vocabulary below)
    status                     (PENDING / RUNNING / SUCCEEDED / FAILED / EDITED / APPROVED)
    input_snapshot             (JSON — exact normalized input for this attempt)
    provider                   (FK -> llm_provider.LLMProvider, null for deterministic stages)
    model                      (FK -> llm_provider.LLMModel, null for deterministic stages)
    reasoning_level
    max_output_tokens
    temperature
    raw_structured_output      (JSON — immutable once written)
    working_output             (JSON — operator-editable)
    lock_version                (optimistic concurrency)
    created_at
    approved_at

JobApplicationStageState
    job_application             (FK)
    stage                       (canonical stage identifier)
    current_stage_run           (FK -> StageRun — the run currently feeding downstream stages)
    approved_stage_run          (FK -> StageRun, nullable — the run that has passed its Human Gate)
    updated_at

    unique_together: (job_application, stage)
```

**Implementation note (V2-D036):** M1 implements `StageRun`'s provider-independent fields only (`job_application`, `stage`, `status`, `input_snapshot`, `raw_structured_output`, `working_output`, `lock_version`, `created_at`, `approved_at`). `provider`, `model`, `reasoning_level`, `max_output_tokens`, and `temperature` are added by an M2 migration once `llm_provider.LLMProvider`/`LLMModel` exist — this is a sequencing detail, not a change to the target schema below.

`StageRun` represents one execution attempt/version of a workflow stage — LLM-backed or deterministic. The same stored `input_snapshot` can be re-run against a different provider/model/reasoning override to produce another, independent `StageRun` (satisfying LLM-010/011's model-comparison requirement) without losing prior attempts. `JobApplicationStageState` tracks, per `JobApplication` + stage, which `StageRun` is currently selected for downstream use and which one (if any) has been approved at a Human Gate.

Domain artifacts owned by their own apps (`JobAnalysis` in `job_intake`, `CandidateContextSnapshot` in `candidate_context`, `CandidateAssessment` in `candidate_matching`, `PositioningStrategy` in `positioning_strategy`, `ResumeContentPlan`/`ResumeDraft`/`RecruiterCritique` in `resume_builder`) each hold a FK to the `StageRun` that produced them, rather than `JobApplication` holding a direct pointer to each artifact. To find "the current resume draft for this JobApplication," resolve `JobApplicationStageState` for `(job_application, AB_DRAFT)` → its `current_stage_run` → the `ResumeDraft` referencing that `StageRun`.

**App-boundary note:** `LLMCallLog` (owned by `llm_provider`, §12) references the initiating `StageRun` by FK. This is a deliberate, accepted exception to the general rule that `llm_provider` has no dependency on pipeline apps — `llm_provider` depends on `job_applications.StageRun` for this one FK, and the FK is nullable to support standalone/manual smoke-test calls that are not tied to any `StageRun`.

### Canonical Stage Vocabulary

**Closed in M0.2 (V2-D022).** One stage vocabulary, owned by the workflow layer (`job_applications`), is shared by `StageRun`, `JobApplicationStageState`, and `llm_provider.StageModelAssignment`.

LLM-capable stages (may have a `StageModelAssignment`):

```text
AJ_ANALYZE
AC_ASSESS
APS_POSITION
AB_PLAN
AB_DRAFT
AB_CRITIQUE
AB_REFINE
QUALITY_EVAL
```

Deterministic/workflow stages (no provider/model — `StageModelAssignment` does not apply):

```text
CANDIDATE_CONTEXT_BUILD
VALIDATE_DRAFT
VALIDATE_REFINED
GATE_1
GATE_2
RENDER
```

Candidate Memory ingestion (`MEMORY_BUILD` in V1) is candidate-scoped, not `JobApplication`-scoped, and remains outside this vocabulary — it continues to use `CandidateMemory`'s own revision lifecycle rather than `StageRun`.

This replaces V1's stage enum, which mixed the discarded `AC_NORMALIZE`/`AC_RANK`/`AC_MATCH` chain into `StageModelAssignment.Stage`. See `docs/V2_REUSE_AUDIT.md` for the historical mapping.

### ReviewFeedback

**Closed in M0.2 follow-up (V2-D032).** `reviews` owns `ReviewFeedback`, which targets a `StageRun` directly rather than a separately-maintained `Target` enum:

```text
ReviewFeedback
    job_application     (FK)
    stage_run           (FK -> StageRun, nullable)
    gate                (GATE_1 / GATE_2)
    comment
    created_at
```

`StageRun.stage` already identifies exactly which stage/run/version/model execution the feedback concerns, so `ReviewFeedback` needs no independently-evolving enum that could drift from the canonical stage vocabulary above. `stage_run` is nullable to support aggregate Gate 1/Gate 2 feedback not tied to one specific LLM run; `gate` identifies the review scope in that case.

## 6. StaticResumeProfile

Static resume metadata is separated from generated content.

```text
StaticResumeProfile
    candidate_name
    contact_metadata
    static_certifications
    static_languages

ExperienceSlot
    id
    static_resume_profile      (FK)
    sequence
    is_primary                 (distinguishes primary slots from any future non-primary entries)
    is_active
    company_name
    role_title
    location
    start_date
    end_date_or_present
```

**Closed in M0.2 (V2-D024).** `ExperienceSlot` is a related, ordered collection (`sequence`), not three hardcoded database columns on `StaticResumeProfile`. For the current V2 release, a deterministic HARD_INTEGRITY check (§11) requires exactly three `ExperienceSlot` rows with `is_primary=True, is_active=True` per `StaticResumeProfile` before AB-2 (Draft) may run. Because cardinality is a validation rule rather than a schema shape, it can change later without a model migration. Company name, role title, location, and dates remain static/operator-owned; AB receives slot identities and relevant context but generates only `bullets[]` for the three active primary slots.

**Closed in M0.2 follow-up (V2-D031).** `ExperienceSlot` selection is explicitly operator-controlled — the system never automatically chooses which three career engagements become the primary slots. The operator selects `CareerEngagement`-equivalent source records (owned by `candidate_memory`, §3) and creates/activates exactly three primary `ExperienceSlot`s with explicit sequence/order: **select engagement → create/activate slot → order (1/2/3) → edit static metadata if authorized → validate exactly three.** Whether `ExperienceSlot` metadata is copied at creation time or resolved by reference to its source engagement record is an M3A implementation detail.

## 7. Candidate Knowledge Architecture

```text
CandidateKnowledge
    ├── structured candidate profile
    ├── MemoryClaims / source-derived items
    ├── preferences/corrections
    ├── career direction
    └── positioning history

CandidateContextSnapshot
    ├── direct_match
    ├── differentiators
    ├── career_narrative
    ├── foundations
    └── gaps_constraints
```

The context snapshot is stored so the same exact context can be reused for model A/B comparisons.

**App boundary (V2-D030):** `CandidateKnowledge` (candidate source documents, `MemoryClaim`s, profile, preferences, `CareerEngagement`-equivalent data) is owned by `candidate_memory` and is the source of candidate truth. `CandidateContextSnapshot` is owned by `candidate_context`, a distinct app that consumes `CandidateKnowledge` and the current job/AJ artifacts to build a job-specific projection — it never becomes a source of truth itself; corrections happen in `candidate_memory`.

## 8. AJ Artifact

One AJ call returns structured requirements plus RecruiterDecisionModel. Stable requirement IDs support structured reasoning and comparisons but are not a rigid truth gate.

**Closed in M0.2 follow-up (V2-D033).** `JobRequirement` captures three orthogonal dimensions rather than one flat category:

```text
JobRequirement
    requirement_id          (JR-NNN, stable within the immutable AJ version)
    text
    requirement_priority    (MANDATORY / PREFERRED / CONTEXTUAL)
    requirement_domain      (TECHNICAL / DOMAIN / LEADERSHIP / CUSTOMER_FACING /
                              RESPONSIBILITY / EDUCATION_CERTIFICATION / OTHER)
    origin                  (EXPLICIT / IMPLIED)
    source_context
    related_ats_terms[]
```

Exact stored enum naming may be refined during M4 implementation; these three dimensions are the architectural contract. `RecruiterDecisionModel` may rank requirements across these dimensions. `RequirementFit` (AC, §9) continues to reference requirements only by their stable `JR-NNN` ID, so this taxonomy change is invisible to AC's own schema.

## 9. AC Artifact

```text
CandidateAssessment
    overall_fit_summary
    differentiators[]
    career_narrative
    underused_strengths[]
    recruiter_misunderstanding_risks[]
    requirement_fits[]

RequirementFit
    requirement_id
    disposition
    evidence_strength
    experience_level
    candidate_evidence[]
    transferable_evidence[]
    gap
    risk
    safe_positioning
    possible_positioning
```

**Closed in M0.2 (V2-D023).** `RequirementFit.experience_level` uses a dedicated `FitExperienceLevel` enum (`AWARENESS`, `LEARNING`, `PROTOTYPE`, `HANDS_ON`, `PRODUCTION`, `ARCHITECTURE`, `LEADERSHIP` — AC-005), distinct from `candidate_memory.MemoryClaim`'s own source/claim-level experience classification. These are different concepts at different layers: a claim's experience classification describes what the source evidence itself shows; `FitExperienceLevel` describes AC's judgment of the candidate's demonstrated level against a specific requirement. No database enum migration is required between them, and `MemoryClaim` retains its existing classification unchanged.

## 10. APS Artifact

APS is a first-class durable artifact. Downstream AB stages consume the approved/current PositioningStrategy rather than reconstructing strategy ad hoc.

## 11. AB Architecture

### AB-1 — Plan

Small structured call returning `ResumeContentPlan`.

### AB-2 — Draft

Larger structured call returning:

```text
ResumeDraft
    target_title_options[3]
    recommended_target_title
    professional_summary
    experience_sections[3]
    key_achievements[]
    skills[]
```

### Static experience merge

Each generated experience section references `experience_slot_id`; renderer joins bullets with static metadata.

### Warning pass

**Closed in M0.2 (V2-D026).** Deterministic checks split into two classes:

- **HARD_INTEGRITY** — blocks the draft from proceeding (fail closed): malformed structured output, references to a nonexistent claim/experience-slot/object ID, invalid schema, incorrect required `ExperienceSlot` cardinality (§6), or any attempt to replace static company/title/date/location metadata.
- **SOFT_REVIEW_WARNING** — attached to the draft for operator review, never blocks: weak evidentiary support, transferable-experience wording, inferred positioning, possible overstatement, a suspicious metric, technology-depth uncertainty, or wording that needs operator confirmation.

Only `SOFT_REVIEW_WARNING` items are advisory per FACT-003. `HARD_INTEGRITY` failures are not softened — they protect structural/factual invariants (schema validity, referential integrity, static-metadata ownership), not wording quality. Human Gate 2 remains the final factual authority over content that passes `HARD_INTEGRITY` and carries only soft warnings.

### AB-3 — Critique

Small structured recruiter-style assessment.

### AB-4 — Refine

Prefer delta-based changes against the existing structured draft.

### Optional evaluator

Advisory quality scores only.

## 12. LLM Control Plane

```text
LLMProvider
    name
    adapter_type
    base_url
    credential_env_variable
    enabled

LLMModel
    provider
    model_identifier
    supports_structured_output
    supported_reasoning_levels   (set drawn from NONE / LOW / MEDIUM / HIGH / XHIGH — canonical source of truth)
    max_output_tokens
    enabled

StageModelAssignment
    stage
    model
    default_reasoning_level
    default_max_output_tokens
    default_temperature
```

**Closed in M0.2 follow-up (V2-D034).** `supported_reasoning_levels` is the single source of truth for a model's reasoning capability. A model with no reasoning capability stores `{NONE}` (or an equivalent empty/`NONE`-only representation) — there is no independently-stored `supports_reasoning` boolean that could drift from this set; `supports_reasoning` may exist only as a derived helper computed from `supported_reasoning_levels`. Provider adapters translate these canonical values into provider-specific request parameters. `StageModelAssignment.default_reasoning_level` must be valid for its assigned model's `supported_reasoning_levels`; per-call UI overrides are validated against the same set.

Runtime UI overrides do not mutate stage defaults unless explicitly saved. `StageModelAssignment.stage` is restricted to the LLM-capable subset of the canonical stage vocabulary (§5) — deterministic/workflow stages never have a `StageModelAssignment` row.

**M2 implementation note:** all pre-flight "fails before HTTP" checks (inactive provider/model, missing/unset credential, unsupported structured output, unsupported reasoning level, output budget exceeding model capability, temperature capability/range — see M2 correction note below) are centralized in `llm_provider.validation.validate_call_configuration`, called by both the routing entrypoint (`llm_provider.adapters.get_adapter_for_stage`/`get_adapter_for_model`, before an adapter instance is even returned) and defensively again inside `BaseLLMAdapter.generate()` (before any adapter's `_call_once` — the actual HTTP boundary — runs). Neither this function nor any other M2 code ever selects a different provider/model than the one explicitly requested.

**M2 correction note (V2-D042, defaults corrected by V2-D044):** `LLMModel` carries two additional explicit capability fields beyond this section's base list — `supports_temperature` and `supports_temperature_with_reasoning` (both default `False` — fail-closed; a model may use `temperature` only after explicit per-model opt-in, consistent with `supports_structured_output`/`supported_reasoning_levels`) — so `temperature` is validated pre-flight the same way every other call parameter already was, via `llm_provider.validation.validate_temperature_supported`. `supports_temperature_with_reasoning=True` requires `supports_temperature=True`; `LLMModel.clean()` rejects the inconsistent combination. A canonical numeric range (`MIN_TEMPERATURE`/`MAX_TEMPERATURE`, 0.0–2.0) is enforced unconditionally. This is model-driven capability metadata, never inferred from a model identifier or provider name.

**M2 correction note (V2-D043):** the Gemini adapter sends its credential via the documented `x-goog-api-key` request header, never the URL/query string (an M2 audit BLOCKER — a URL-embedded credential is visible in `requests`/`urllib3` connection-level exception text). `llm_provider.errors.sanitize_error_message` additionally redacts sensitive URL/query-string parameters, URL user-info, and auth-scheme-prefixed tokens as a defence-in-depth invariant, independent of any single adapter's own credential-transport choice; adapters classify network-boundary exceptions (`NormalizedLLMError.from_network_exception`) rather than passing their raw text through at all.

## 13. LLMCallLog

**Closed in M0.2 (V2-D022).** `StageRun` is defined in §5 and owned by `job_applications` (workflow layer), not by `llm_provider`. `llm_provider` owns only `LLMCallLog`, which records token usage, latency, retries, and safe error category for a single provider call, and references the initiating `StageRun` by FK (nullable, to support manual smoke-test calls not tied to a `StageRun`). A single `StageRun` may accumulate multiple `LLMCallLog` rows across retries; `StageRun.raw_structured_output` reflects the successful attempt.

**M2 implementation note (V2-D039):** the implemented `LLMCallLog` names these fields `requested_provider`/`requested_model` (rather than `provider`/`model`) and adds `resolved_model_identifier`/`finish_reason` beyond this section's base list — see V2-D039 for the full rationale. No field on `LLMCallLog` ever stores a prompt, response body, or credential value.

## 14. Model Comparison

A/B execution reuses identical stored input. Alternative StageRuns reference the same input snapshot and remain independently inspectable.

## 15. Token Efficiency

Largest contexts are candidate context selection, AC, and AB draft. Small-output calls are APS, AB plan, critic, and optional evaluator.

Do not repeatedly resend the full candidate archive; use stored structured artifacts as compact downstream inputs.

## 16. Orchestration

Use plain Django services/views. Pipeline is user-triggered and database-backed. Do not introduce LangGraph/LangChain/OpenAI Agents SDK as workflow infrastructure in initial V2.

## 17. Reuse From `main`

The read-only reuse audit is complete; see `docs/V2_REUSE_AUDIT.md` for the full `REUSE_AS_IS` / `REUSE_WITH_ADAPTATION` / `DO_NOT_REUSE` matrix, exact files/commits, recommended reuse order, and identified risks. That document is an audit/history record, not a canonical architecture source — this document and `docs/DECISIONS.md` govern where they differ.

Confirmed reusable: Docker/Postgres, environment loading, generic Django settings, provider adapter primitives, provider/model registry concepts, token/reasoning configuration, LLM logging, generic templates/styles, and most of `candidate_memory`'s ingestion pipeline.

Confirmed redesign: V1's AC chain (`AC_NORMALIZE` → `AC_RANK` → `AC_MATCH`, three calls inside `candidate_matching`) plus the separate single-call `AB_BUILD` inside `resume_builder` — historically referred to together as a "four-call chain," which in fact spans two apps, not one. Also: the hard-reject factual validators (`no_fabrication.py`, `completeness.py` — see §11's HARD_INTEGRITY/SOFT_REVIEW_WARNING split), and the fixed, unbounded-roster renderer.

Never bulk-merge `main`.

## 18. Multi-Agent Continuity

**Closed (V2-D035).** The repository must support safe continuation by a different coding agent without access to a prior agent's conversational context. This is a repository-native property, not something any single agent's working memory can provide: it is achieved through the canonical docs listed throughout this document, Git history, deterministic tests, `docs/CURRENT_STATE.md` (verified rather than trusted), and an explicit handover protocol.

The tool-neutral entry point is `AGENTS.md`, backed by `docs/ENGINEERING_RULES.md` (the detailed engineering agreement — precedence rules, this architecture's invariants restated as a checklist, Git/database/testing/documentation/secrets rules), `docs/HANDOVER_PROTOCOL.md` (clean and emergency handover), and `docs/MILESTONE_COMPLETION_CHECKLIST.md`. Tool-specific files (e.g. `CLAUDE.md`) point to these rather than duplicating them.
