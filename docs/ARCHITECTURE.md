# Architecture (Proposed)

Status: proposed, pre-implementation. This document describes the smallest architecture believed
to satisfy `requirements.md`. Every boundary and component below is connected back to a
requirement ID (see `docs/REQUIREMENT_TRACEABILITY.md` for the full catalog). Items that go beyond
what requirements.md states verbatim are marked **[recommendation — see DECISIONS.md]**; treat
those as proposals, not settled fact.

## 1. Guiding constraint

STACK-001..008 (requirements.md §11) is the starting stack: Django, PostgreSQL via Docker,
server-rendered templates, synchronous execution, Pydantic for structured-output validation,
`.env` secrets, no Redis/Celery, no SPA, no cloud deployment. Nothing below introduces
infrastructure beyond that without a stated reason tied to a requirement ID. In particular:

- **No LangGraph for v1** (D-001 / STACK-006, **APPROVED** 2026-09-02). The two human review
  gates are DB-state preconditions (HITL-004), not paused executions — a graph framework's
  checkpoint/resume machinery would be solving a problem this design doesn't have, and the durable
  business state already belongs in PostgreSQL (`JobApplication` lifecycle, stage artifacts,
  versions, approvals, feedback, freshness, current-version pointers) — a second authoritative
  workflow-state representation through LangGraph checkpoints must not be created alongside it.
  Stage-service boundaries are kept clean specifically so LangGraph, LangChain agents, or the
  OpenAI Agents SDK could be evaluated later without redesigning the domain model. **An explicit
  architecture review checkpoint is scheduled for after the integrated workflow is functioning
  (approximately Milestone M7)** — see §7 below. Observability is designed independently of the
  orchestration framework and is not, on its own, a reason to adopt one of these frameworks (see
  §7).
- **No Celery/Redis** (STACK-003/NG-004). Single operator, one job application in flight, every
  stage already pauses for human review — synchronous in-request LLM calls cost a few seconds of
  latency, which is not a real problem at this scale.
- **No SPA** (STACK-004). Server-rendered Django templates are sufficient for one local operator.

## 2. Component boundaries (Django apps)

Each app is a request-time module with models, one or two services, and (where relevant) views/
templates. No app calls another app's models directly for anything the owning app should be
responsible for validating — cross-app access goes through a small service function, not raw ORM
queries reaching into another app's internals, so that responsibility (e.g. "only confirmed claims
are eligible") lives in exactly one place.

### 2.1 `llm_provider`

**Responsibility**: the entire LLM provider abstraction (requirements.md §9) — registry, adapter
interface, per-provider adapters, structured-output validation scaffolding, retry policy, error
taxonomy, call audit logging. This is the one app every other pipeline app depends on for making
an LLM call; no other app talks to an LLM SDK directly.

**Owns**: `LLMProvider`, `LLMModel`, `StageModelAssignment`, `LLMCallLog` (DATA-008/009), the
`NormalizedLLMRequest`/`NormalizedLLMResult` types (LLM-002), the adapter interface and concrete
OpenAI/NVIDIA-NIM/Gemini adapters (LLM-007), the retry policy (LLM-008), and the typed error
taxonomy (LLM-009).

**Satisfies**: GOAL-002, LLM-001..011, NFR-003/004/005, STACK-005.

### 2.2 `candidate_memory`

**Responsibility**: the memory-build prerequisite (requirements.md §4) — accepting free-form
markdown, running the memory-build LLM call (via `llm_provider`), producing a versioned
`CandidateMemory` of `MemoryClaim` records, and the confirmation UI where the operator marks
claims `confirmed`/`retired`.

**Owns**: `CandidateMemory`, `MemorySourceDocument`, `MemoryClaim` (DATA-001..003).

**Satisfies**: MEM-001..006, PIPE-001, GOAL-003 (first review checkpoint — confirming claims *is*
a human-in-the-loop step even though requirements.md doesn't number it as a formal "gate" the way
Gates 1/2 are).

### 2.3 `job_intake` (Agent Jobber)

**Responsibility**: job posting intake and analysis (requirements.md §5) — URL fetch with
pasted-text fallback, language detection, the AJ LLM call, and storage of the structured
`JobRequirementAnalysis`.

**Owns**: `JobRequirementAnalysis` (DATA-004), its child `JobRequirement` rows with stable IDs
(D-014), the URL-fetch-with-fallback service.

**Satisfies**: AJ-001..006, D-014's AJ portion.

### 2.4 `candidate_matching` (Agent Candidate)

**Responsibility**: retrieval of relevant `MemoryClaim`s and the fit/gap assessment (requirements.md
§6) — the AC LLM call and its output validation (no-concealment check).

**Owns**: `FitAssessment` (DATA-005), its child `RequirementAssessment` rows (D-014), the
retrieval service (queries `candidate_memory` for confirmed claims relevant to a given
`JobRequirementAnalysis`, never the whole profile).

**Satisfies**: AC-001..003, NFR-002, D-014's AC portion.

### 2.5 `resume_builder` (Agent Builder)

**Responsibility**: positioning and drafting (requirements.md §7) — the AB LLM call, the
post-generation no-fabrication validator, markdown rendering, and versioned `ResumeDraft` storage.

**Owns**: `ResumeDraft` (DATA-007) and its structured `ResumeElement`s (D-014), the no-fabrication
validator, and the markdown renderer defined in `docs/RESUME_OUTPUT_STRUCTURE.md`.

**Satisfies**: AB-001..004, NFR-001, D-014's AB portion.

**Dependency (resolved)**: D-007 previously blocked this app on an undefined template; that is now
resolved via `docs/RESUME_OUTPUT_STRUCTURE.md`, so this app is unblocked for Milestone M6.

### 2.6 `reviews`

**Responsibility**: the shared mechanics of both human review gates (requirements.md §10) — status
fields, the generic approve action, `ReviewFeedback` storage, and freshness/staleness checks. Kept
as its own app (rather than duplicated inside `candidate_matching` and `resume_builder`) because
HITL-004/005/006/007 describe the *same* mechanism applied at two points in the pipeline; one
implementation avoids two subtly-different copies of gate logic drifting apart.

**Owns**: `ReviewFeedback` (DATA-006), the generic approve/needs_rework view logic, and the
freshness-check helper used at the start of AC's and AB's runs.

**Satisfies**: HITL-001..007, NFR-002 (jointly with `candidate_matching`).

### 2.7 `job_applications` (D-012, **APPROVED** 2026-09-02)

**Responsibility**: the aggregate/root entity for one tracked vacancy/application. Groups the
versioned chain of `JobRequirementAnalysis` → `FitAssessment` → `ResumeDraft` (D-010) via
current-version pointers, owns the dashboard lifecycle (`pipeline_phase`/`application_outcome`,
requirements.md §17), and is the entity the freshness checks in D-006 compare against.

**Owns**: `JobApplication` — `current_jra`, `current_fit_assessment`, `current_resume_draft`
pointers; `pipeline_phase` (`NEW`/`ANALYSIS`/`PREPARATION`/`READY`); `application_outcome`
(`NOT_APPLIED`/`APPLIED`/`INTERVIEWING`/`REJECTED`); `created_at`/`updated_at`. Also owns the
dashboard views/templates (list of job applications, per-application detail linking into the
other apps' screens at that application's current state).

**Satisfies**: the dashboard requirement (requirements.md §17), and is a load-bearing dependency
of D-006's freshness model and D-010's versioning model — not just a UI convenience.

**Sequencing note**: per D-012's own consequence, this app should be established early enough
(Milestone M1 scaffolding, populated starting M4) that `job_intake`, `candidate_matching`, and
`resume_builder` naturally FK into it from the start, rather than retrofitting the aggregate onto
already-built stage models at M7.

### 2.8 Operator UI

No separate `ui` app. Views and templates live inside the owning app for each screen (e.g. the
memory-confirmation screen lives in `candidate_memory`, Gate 1's combined AJ+AC view lives in
`reviews` since it renders records from two other apps but the *gate* itself is `reviews`'
responsibility). This keeps STACK-004's "no SPA" simple without inventing a cross-cutting app that
has no clear ownership.

## 3. Provider abstraction design (detail on LLM-001..011)

No adapter code is written in this phase — this is the design-level contract to be implemented in
M2.

### 3.1 `NormalizedLLMRequest`

Carries: the message/prompt content for the call, the target output schema (a Pydantic model —
see D-005), generation parameters (temperature, max tokens, reasoning/thinking effort where
applicable), and a `stage` identifier (`MEMORY_BUILD` / `AJ_ANALYZE` / `AC_MATCH` / `AB_BUILD`) so
the adapter layer knows which `StageModelAssignment` and which `LLMCallLog` row this call belongs
to.

### 3.2 `NormalizedLLMResult`

Either: parsed structured content (validated against the requested Pydantic schema), usage
metadata (input/cached-input/output/total token counts where reported, latency — token-first per
D-008; no cost computation at M2, pricing is optional/deferred) — or one of the typed errors from
the LLM-009 taxonomy (configuration, auth, rate-limit, timeout, schema-validation,
provider-internal). Pipeline code (the four agent apps) only ever sees this normalized shape; it
never sees a raw provider response object.

### 3.3 Per-provider translation layer

One adapter class per provider, each responsible for exactly the parts of LLM-007 that are
provider-specific:

- **OpenAI adapter**: passes the Pydantic-derived JSON schema through directly as strict
  structured output.
- **NVIDIA NIM adapter**: OpenAI-compatible `response_format`-style schema, but capability
  (whether a given self-hosted/managed model actually supports strict schema mode) must be
  checked against `LLMModel.supports_structured_output` before assuming it, since NIM model
  support is not uniform.
- **Gemini adapter**: translates the Pydantic schema into Gemini's reduced dialect — flattening
  `$ref`/`$defs`, and dropping `enum` constraints from the schema sent to Gemini entirely
  (accumulated enum constraints have been observed elsewhere to cause opaque 400s). Enum-typed
  fields are instead re-validated in Python against the Pydantic model's allowed values after the
  response is parsed. The Gemini adapter is pinned to REST transport, never gRPC (gRPC has been
  observed to reject payloads that succeed over REST with identical content). Thinking-budget/
  reasoning-effort parameters are passed defensively — e.g. never assume a literal zero value is
  accepted for "disabled reasoning" — since these parameters are still evolving across Gemini
  model generations.

New providers added later implement the same adapter interface and get their own isolated
translation class; no shared pipeline code changes (NFR-005).

### 3.4 Retry policy (LLM-008)

Lives in the adapter layer, not in pipeline code — pipeline code calls the adapter once and gets
back either a result or a (post-retry) typed error; it never sees individual retry attempts.
Retries only fire for errors classified as transient (rate-limit, 5xx/provider-internal), never
for auth or schema-validation errors. For streaming calls specifically: retry only if no output
token has streamed yet for that attempt — once any content has streamed, the adapter must return
whatever partial-failure error it has rather than retrying (to avoid duplicated/garbled output).
Retry attempts are capped with backoff between them.

### 3.5 Error handling and sanitization (LLM-009)

Every typed error carries a safe, human-readable category and message; the adapter layer is
responsible for stripping any raw provider response body or exception text that might echo
request/response content (which can include candidate data) before it reaches `LLMCallLog` or any
log line.

### 3.6 Audit logging (LLM-010)

The adapter's single call path is the one place an `LLMCallLog` row gets written — provider,
model, stage, token usage (input/cached-input/output/total, per D-008's token-first priority),
latency, retry count, sanitized error category. No cost computation happens at M2; if pricing is
added to `LLMModel` later, a `cost_usd` snapshot can be added to this write additively, using the
`LLMModel`'s pricing at call time, without reworking the token fields. Because this write happens
in one place (the adapter layer), no pipeline app has to remember to log a call itself.

## 4. Domain model (semantics, not migrations)

Field lists here are the important fields only, not a final schema — per the task's instruction,
semantics and invariants come first.

### `CandidateMemory`
- **Responsibility**: versioned container for one candidate's confirmed profile. Each version is
  a complete logical snapshot (D-002, **APPROVED WITH MODIFICATION**).
- **Key fields**: `version` (int, monotonic), `created_at`.
- **Relationships**: has many `MemorySourceDocument`, has many `MemoryClaim` (both scoped to this
  version).
- **Lifecycle**: a version is created once, then immutable — no in-place edits to a past version
  (MEM-006).
- **Versioning** (D-002, approved): a new revision is a **snapshot + incremental** process, not a
  full reprocess. Source documents are matched by immutable content hash (D-003); unchanged
  documents are not re-sent through Memory Build, and their claims may carry `confirmed` status
  forward into the new snapshot when claim identity is demonstrably unchanged (per D-003's
  hash+location fields). New/changed documents are (re)processed, and claims from new/changed
  material begin `unconfirmed`. If claim identity can't be safely established, the claim requires
  operator reconfirmation rather than silently inheriting `confirmed`. Prior versions remain
  immutable and retained.
- **Invariants**: only one version is "current" (used by `candidate_matching`'s retrieval) at a
  time.

### `MemorySourceDocument`
- **Responsibility**: one uploaded markdown file, linked to the `CandidateMemory` version it was
  processed into, identified by an immutable content hash.
- **Key fields**: `filename`, `raw_content`, `content_sha256` (D-003, approved — identifies this
  document for the D-002 unchanged/changed comparison), `candidate_memory` FK, uploaded_at.
- **Relationships**: has many `MemoryClaim` (claims extracted from this document).
- **Invariants**: immutable once processed — re-uploading the same conceptual document as part of
  a revision creates a new `MemorySourceDocument` row under the new `CandidateMemory` version
  (with a new or matching `content_sha256`), not an edit to the old one.

### `MemoryClaim`
- **Responsibility**: one extracted fact/achievement, the atomic unit of retrieval and review.
- **Key fields**: `resume_section`, `confirmation_status` (`unconfirmed`/`confirmed`/`retired`),
  `source_document` FK, a stored verbatim quote from the source, and (D-003, **APPROVED WITH
  MODIFICATION** — provenance is not quote-only) `source_start_line`/`source_end_line`.
- **Relationships**: FK to `MemorySourceDocument` (and transitively to `CandidateMemory`); later
  referenced by `FitAssessment`'s `RequirementAssessment.supporting_memory_claim_ids` (D-014) and
  by `ResumeDraft`'s `ResumeElement.supporting_memory_claim_ids` (D-014).
- **Lifecycle**: `unconfirmed` (just extracted) → `confirmed` (operator reviewed and trusts it) or
  `retired` (operator rejects it). Only `confirmed` claims are eligible for retrieval in AC-001 or
  as evidence in D-014's structured outputs.
- **Invariants (MEM-005/NFR-001)**: claim text must be traceable to its stored source quote — the
  memory-build step organizes/phrases, it does not synthesize content the source doesn't support.
  This is enforced by a validator at build time, not by prompting alone.
- **Provenance** (D-003, approved): `source_document` FK + `content_sha256` (via the FK) + stored
  quote + `source_start_line`/`source_end_line` together *are* the provenance record. The hash
  proves which immutable source content the location/quote belongs to; the quote supports human
  review; the line range supports deterministic navigation. Loose semantic similarity is
  explicitly not the provenance mechanism.

### `JobRequirementAnalysis`
- **Responsibility**: Agent Jobber's structured output for one job posting.
- **Key fields**: `source_type` (`url`/`pasted`), original raw text/URL, mandatory requirements,
  preferred requirements, responsibilities, ATS keywords, screening risks, implied expectations.
- **Child entity `JobRequirement`** (D-014, approved): one stably-identified requirement within
  this immutable JRA version — `requirement_id` (e.g. `JR-001`), `category`
  (mandatory/preferred/responsibility/ATS-signal/implied-expectation), `text`, source/context. The
  ID is what `RequirementAssessment` and `ResumeElement.matched_job_requirement_ids` reference —
  it never changes within a version.
- **Versioning** (D-010, approved): gains a `version` int; a re-run triggered by Gate-1 feedback
  targeting AJ creates a new version rather than overwriting; grouped under one `JobApplication`
  (D-012) via `JobApplication.current_jra`.
- **Invariants**: always stores the original raw input (AJ-005), regardless of how many versions
  exist, so every version remains traceable to the same source posting. `JobRequirement` IDs are
  stable and immutable within a version — a re-run creates a new JRA version with its own
  (possibly renumbered) `JobRequirement` set, not a mutation of the old one's IDs.

### `FitAssessment`
- **Responsibility**: Agent Candidate's structured output — the fit/gap picture for one
  `JobRequirementAnalysis` version.
- **Key fields**: FK to `JobRequirementAnalysis` (specific version), matched-requirements list
  (each referencing the supporting `MemoryClaim`(s)), explicit gaps list, risk notes carried from
  AJ.
- **Child entity `RequirementAssessment`** (D-014, approved): one row per relevant
  `JobRequirement` — `requirement_id`, `disposition` (`MATCH`/`PARTIAL`/`GAP`/`UNKNOWN`),
  `supporting_memory_claim_ids`, `explanation`, `gap_or_limitation`. This *replaces* a purely
  free-form gaps/matches representation — every relevant `JobRequirement` gets exactly one
  disposition row, so coverage is enumerable and checkable, not just narratively described.
- **Versioning** (D-010, approved): versioned like `JobRequirementAnalysis`; a Gate-1 feedback
  re-run targeting AC creates a new version.
- **Invariants (NFR-002/D-014)**: every relevant `JobRequirement` has exactly one
  `RequirementAssessment`; `MATCH`/`PARTIAL` require at least one `confirmed` supporting
  `MemoryClaim`; `GAP`/`UNKNOWN` remain explicitly visible and are never silently dropped or
  upgraded to `MATCH` for lack of evidence. Checked by a validator, not left to prompting.
- **Freshness** (HITL-007/D-006, approved): stores `based_on_jra_id`; it is stale whenever that no
  longer equals `JobApplication.current_jra_id` — an **immutable-identity** comparison, not a
  timestamp comparison. On mismatch, the next step must block with an explicit operator prompt.

### `ReviewFeedback`
- **Responsibility**: the operator's free-text input at a review gate, and the trigger for a
  re-run.
- **Key fields**: free text, `target_step` (`AJ`/`AC`/`AB`), FK to the specific record version the
  feedback is about, timestamp.
- **Lifecycle**: a write to this table (HITL-006) is the mechanism that flips the target record's
  status to `needs_rework`; the next pipeline invocation for that step reads status fresh and
  incorporates the feedback as additional input.

### `ResumeDraft`
- **Responsibility**: Agent Builder's output — a structured resume representation, rendered to
  markdown only after validation (see `docs/RESUME_OUTPUT_STRUCTURE.md`).
- **Key fields**: FK to `FitAssessment` (specific version), the structured representation
  (`TargetPositioning`, `ProfessionalSummary`, `ExperienceSection`s, `PositioningTheme`s,
  `Achievement`s, `SkillCategory`/`SelectedResumeSkills`, `Certification`s,
  `LanguageProficiency`s, `PositioningGuidance` — see `docs/RESUME_OUTPUT_STRUCTURE.md`), the
  rendered markdown content (produced only after validation passes), `status`
  (`draft`/`awaiting_review`/`confirmed`), `confirmed_at`.
- **Child entity `ResumeElement`** (D-014, approved): every factual structured item (summary
  statement, experience bullet, achievement, skill, certification, language entry) is a
  `ResumeElement` — `text`, `supporting_memory_claim_ids`, `matched_job_requirement_ids`.
- **Versioning** (D-010, approved — consolidates the former D-013): each Gate-2 regeneration
  produces a new version; the operator-approved one is flagged `confirmed` as the final
  deliverable for that `JobApplication` (`JobApplication.current_resume_draft`).
- **Invariants (NFR-001/D-014)**: every `ResumeElement` must reference at least one `confirmed`
  `MemoryClaim` reachable through the `FitAssessment` it was built from; every referenced claim ID
  must exist, be confirmed, and be eligible for this `CandidateMemory` revision and job-application
  context — enforced by a post-generation validator against the *structured* representation,
  **before** markdown is rendered. This is an evidence-attachment/eligibility check, not a
  text-similarity check (semantic/embedding similarity is explicitly not the truth test — see
  D-014). Human review at Gate 2 remains responsible for judging whether the wording fairly
  represents the evidence.
- **Freshness** (HITL-007/D-006, approved): stores `based_on_fit_assessment_id`; it is stale
  whenever that no longer equals `JobApplication.current_fit_assessment_id` — the same
  immutable-identity comparison `FitAssessment` uses against `JobRequirementAnalysis`.

### `LLMProvider`
- **Responsibility**: one configured LLM backend (e.g. "OpenAI account A").
- **Key fields**: name, base URL, a *reference* to where its credential lives (an env var name),
  never the credential value itself (LLM-004/NFR-003).

### `LLMModel`
- **Responsibility**: one usable model under a provider.
- **Key fields**: FK `LLMProvider`, model identifier, capability flags (structured-output support,
  streaming support, reasoning support, max output tokens — LLM-005/D-009, approved as-is). No
  pricing fields at M2 (D-008, **APPROVED WITH REPRIORITIZATION** — token consumption is the v1
  priority; pricing is optional/deferred and, if added later, must not require reworking
  `LLMCallLog` — see below).

### `StageModelAssignment`
- **Responsibility**: maps a pipeline stage (`MEMORY_BUILD`/`AJ_ANALYZE`/`AC_MATCH`/`AB_BUILD`) to
  the `LLMModel` currently handling it.
- **Invariants (LLM-001/006)**: changing this mapping is the *only* action needed to move a stage
  to a different provider/model — no pipeline code reads provider identity any other way.

### `LLMCallLog`
- **Responsibility**: one row per LLM call — the audit ledger (not a cache; never read from to
  skip a call).
- **Key fields**: provider, model, stage, token usage — **(D-008, approved, token-first)** input
  tokens, cached-input tokens, output tokens, and total tokens, each where reported by the
  provider — latency, retry count, sanitized error category. No `cost_usd` field at M2; if pricing
  is added to `LLMModel` later, a `cost_usd` snapshot column can be added to this model additively
  without reworking its token fields.
- **Invariants (LLM-009)**: never stores a raw provider response body or unsanitized exception
  text.
- **Aggregation (NFR-004)**: must support summing token counts per `JobApplication`, per pipeline
  stage, per provider, and per model without extra instrumentation beyond this table.

### `JobApplication` (D-012, **APPROVED** 2026-09-02)
- **Responsibility**: the aggregate/root entity for one tracked vacancy/application — groups the
  versioned chain of `JobRequirementAnalysis` → `FitAssessment` → `ResumeDraft` (D-010) and owns
  the dashboard lifecycle (requirements.md §17).
- **Key fields**: `current_jra` FK, `current_fit_assessment` FK, `current_resume_draft` FK,
  `pipeline_phase` (`NEW`/`ANALYSIS`/`PREPARATION`/`READY`), `application_outcome`
  (`NOT_APPLIED`/`APPLIED`/`INTERVIEWING`/`REJECTED`), `created_at`/`updated_at`.
- **Lifecycle**: `pipeline_phase` advances automatically based on workflow state (NEW → ANALYSIS
  once AJ/AC work starts or Gate 1 is pending → PREPARATION once Gate 1 is approved and resume
  work is under way → READY once a resume is approved but not yet marked applied), so the operator
  is not doing repetitive manual status maintenance. `application_outcome` is operator-set
  explicitly (particularly to mark `APPLIED`/`INTERVIEWING`/`REJECTED`) and is independent of
  `pipeline_phase` — regenerating a resume after `APPLIED` does not revert the outcome.
- **Invariants**: this is the single source of "current version" truth that D-006's freshness
  checks and D-010's versioning model both depend on — no other model duplicates that state.

## 5. What is explicitly NOT built (and why)

- No Celery/Redis/message broker (STACK-003, NG-004) — synchronous calls are cheap enough at this
  scale and every stage already pauses for a human anyway. requirements.md §11 notes one possible
  future exception: "a simple DB-row-claiming worker (no broker needed)" if the UI ever needs to
  stay responsive during a long call — this is a future option, not adopted now, and would not
  require Redis/Celery even if it is adopted later.
- No LangGraph (D-001) — gates are DB preconditions, not paused executions; nothing here needs
  checkpointed resumability.
- No SPA/JS framework (STACK-004) — one local operator does not need a rich client.
- No auth/multi-tenancy (NG-002, ACTOR-001) — single operator, single candidate.
- No PDF/DOCX rendering (NG-001, AB-004) — markdown is the v1 deliverable.
- No cassette/recorded-response test infrastructure yet (NG-003, TEST-002) — deferred until
  outputs stabilize; the adapter interface (§3) is kept as the one seam so this can be added later
  without touching pipeline call sites.
- No shared/extracted provider-adapter package (LLM-011) — this codebase is fully independent of
  `career-intelligence`; the interface is kept clean enough that a future extraction wouldn't
  require a rewrite, but no such extraction happens now.
- No LangChain/LangGraph/LangSmith/OpenAI Agents SDK during M1/M2 (D-001) — not merely deferred by
  default but explicitly re-evaluated only at the post-M7 checkpoint in §7 below, and never
  adopted for observability reasons alone.
- No removal of Django's own admin/auth framework. **(v1.1 clarification)** requirements.md's "no
  multi-tenant auth" non-goal is about product-level accounts/tenants/roles, not about
  `django.contrib.admin`/`auth`/`sessions`/`contenttypes`, which M1 includes as standard framework
  infrastructure needed for provider/model registry administration (see §2.6 and D-012's
  dashboard, both of which assume the Django admin is present).

## 6. Structured AJ → AC → AB traceability design (D-014)

This section details the design behind requirements.md's new §16 and `docs/DECISIONS.md` D-014 —
the mechanism that makes the no-concealment (NFR-002) and no-fabrication (NFR-001) invariants
checkable in code.

- **`job_intake`** owns `JobRequirement` rows scoped to one `JobRequirementAnalysis` version, each
  with a stable `requirement_id` (`JR-001`, ...) and `category`. These IDs are immutable within a
  version; a re-run creates a new JRA version with its own `JobRequirement` set (§4).
- **`candidate_matching`** owns `RequirementAssessment` rows scoped to one `FitAssessment` version
  — one row per relevant `JobRequirement`, each with a `disposition` and, for `MATCH`/`PARTIAL`,
  `supporting_memory_claim_ids` restricted to `confirmed` claims. A validator (not the LLM prompt
  alone) enforces "every relevant requirement has exactly one disposition" and "no unevidenced
  `MATCH`" at the point this data is persisted, per D-014.
- **`resume_builder`** owns `ResumeElement`-shaped structured data (see
  `docs/RESUME_OUTPUT_STRUCTURE.md`) for every factual output; the no-fabrication validator runs
  against this structured data before the markdown renderer is ever invoked. The validator checks
  claim existence, confirmation, revision-eligibility, and job-application-context eligibility —
  an attachment/eligibility check, deliberately not a text- or embedding-similarity check (D-014
  rules that out as the fundamental truth test, precisely because similarity checks can be fooled
  by a plausible-sounding but unevidenced rewrite).
- **Human review boundary**: none of this validation judges whether generated wording is a fair,
  accurate representation of the cited evidence — that stays a Gate 1/Gate 2 human review
  responsibility. The structural validators only guarantee that every factual claim has *some*
  named, checkable evidence trail; they do not (and cannot) guarantee the trail is used honestly in
  prose. This split is deliberate, not a gap: it is exactly what "the operator remains responsible
  for approving final wording" (requirements.md §16) means architecturally.

## 7. Observability and future orchestration-framework compatibility

Companion detail to D-001's re-evaluation checkpoint (§1).

- `LLMCallLog` (§4) remains the durable **application** audit ledger — provider, model, stage,
  token usage, latency, retries, sanitized error category — and application correctness must never
  depend on a third-party tracing backend being present.
- The natural future instrumentation boundary, if tracing is added later, is around each stage
  invocation's internal steps: deterministic validation → provider call → retry → structured
  parsing → persistence, scoped under one `JobApplication`. Because stage-service functions are
  already clean, isolated call boundaries (§2), a tracing library (e.g. LangSmith or another
  backend) could wrap those boundaries later without the application's correctness depending on
  it being present.
- **Post-M7 checkpoint** (D-001): once the integrated per-job workflow (M7) is functioning,
  explicitly revisit whether concrete needs have emerged that justify an orchestration/agent
  framework — dynamic agent routing, parallel branches, tool-calling loops, long-running
  autonomous execution, resumability after process failure, agent-to-agent delegation,
  significantly more pipeline stages, or complex conditional execution. Wanting better
  observability/tracing is explicitly **not**, by itself, sufficient justification — observability
  is addressed independently, as described above.
