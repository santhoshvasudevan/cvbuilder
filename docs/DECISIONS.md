# Architectural Decisions

Status vocabulary used below:

- **PROPOSED** — a recommendation made during analysis; not yet approved by the product owner.
- **APPROVED** — explicitly approved by the product owner (Santhosh). Approval dates are recorded.
- **SUPERSEDED** — a previously APPROVED decision later replaced; the old entry is kept (not
  deleted) with a pointer to what replaced it.

**2026-09-02 — product-owner review of the M0 baseline.** D-001 through D-013 were reviewed;
several were approved with modification, and one new decision (D-014) was added and approved.
Where a decision was approved with modification, this file keeps the original recommendation for
context and adds the approved decision underneath it — the original text is not deleted, so the
reasoning trail stays legible.

None of these decisions modify `requirements.md` except where `requirements.md` itself was
explicitly amended by the product owner (see its Amendment Log) — in those cases this file and
`requirements.md` describe the same approved decision from two angles (rationale here,
requirement text there).

---

## D-001: Orchestration mechanism

- **Status**: **APPROVED WITH FUTURE RE-EVALUATION** (2026-09-02)
- **Requirement**: STACK-006
- **Issue**: whether pipeline steps run as a plain ordered sequence of Django-view-triggered
  service functions, or as a LangGraph graph.
- **Original recommendation**: plain service-function sequence; requirements.md itself says not to
  adopt LangGraph by default.
- **Approved decision**: use ordinary Django application/service orchestration for v1. Do **not**
  use LangGraph as the workflow runtime for v1. The durable business state already belongs in
  PostgreSQL — `JobApplication` lifecycle, stage artifacts, artifact versions, approvals, feedback,
  freshness, current-version pointers — and a second authoritative workflow-state representation
  through LangGraph checkpoints must not be created alongside it.
- **Preserve for later**: keep clean stage-service boundaries so that LangGraph, LangChain agents,
  the OpenAI Agents SDK, or another orchestration/runtime could be evaluated later without
  redesigning the domain model.
- **Re-evaluation checkpoint**: an explicit architecture review is scheduled for **after the
  integrated workflow is functioning (approximately Milestone M7)**. At that checkpoint,
  reconsider an orchestration framework only if concrete needs have appeared, such as: dynamic
  agent routing, parallel branches, tool-calling loops, long-running autonomous execution,
  resumability after process failure, agent-to-agent delegation, significantly more pipeline
  stages, or complex conditional execution. **Observability alone is not sufficient reason to
  adopt LangGraph** — observability is designed independently of the orchestration framework (see
  D-001's companion note in `docs/ARCHITECTURE.md` §7 on tracing/LangSmith compatibility). Do not
  introduce LangChain, LangGraph, LangSmith, the OpenAI Agents SDK, or another agent framework
  during M1/M2 merely for future possibilities.
- **Consequence**: pipeline steps are ordinary Python functions/service classes invoked from
  Django views; no new dependency, no checkpoint store to reason about, until/unless the M7
  checkpoint concludes otherwise.

## D-002: CandidateMemory revision semantics

- **Status**: **APPROVED WITH MODIFICATION** (2026-09-02)
- **Requirement**: MEM-006
- **Issue**: does supplying new markdown create a new revision by reprocessing *all* currently
  active source documents into a fresh claim snapshot, or append incrementally?
- **Original recommendation**: full-snapshot revisioning — reprocess everything every time.
- **Approved decision**: **snapshot + incremental processing.** A `CandidateMemory` revision still
  represents one complete logical snapshot, but building a new revision does not require
  reprocessing every source document from scratch:
  1. Source documents are identified by an immutable content hash.
  2. Unchanged source documents do not need another LLM extraction.
  3. Claims derived from unchanged source content may be carried forward into the new logical
     snapshot.
  4. Their `confirmation_status` may remain `confirmed` when provenance and claim identity are
     demonstrably unchanged.
  5. New source documents are processed through Memory Build.
  6. Changed source documents are reprocessed.
  7. Claims arising from new/changed source material begin `unconfirmed`.
  8. Previous `CandidateMemory` revisions remain immutable.
  9. If safe claim identity cannot be established after source content changes, require operator
     reconfirmation rather than silently carrying confirmation forward. Conservative trust
     behavior is preferred over convenience.
- **Rationale**: reduces unnecessary LLM use and unnecessary repetitive human review while
  preserving the versioned-memory trust model — the concern the original recommendation was
  trying to protect (reviewer workload) is what this modification directly fixes.
- **Consequence**: implementation needs a claim-identity strategy (see D-003's content-hash +
  location fields) precise enough to say "this claim's provenance is demonstrably unchanged" — a
  real design task for Milestone M3, not a trivial diff.

## D-003: MemoryClaim provenance representation

- **Status**: **APPROVED WITH MODIFICATION** (2026-09-02)
- **Requirement**: MEM-003
- **Issue**: how a `MemoryClaim` points back to its source.
- **Original recommendation**: FK + stored verbatim quote only, no line/offset pointer.
- **Approved decision**: provenance must **not** be quote-only. For v1, at minimum:
  - FK to an immutable/versioned `MemorySourceDocument`.
  - The source document's immutable content hash (e.g. `content_sha256`).
  - The stored source quote/excerpt (supports human review).
  - Source location: start/end line numbers, as the preferred v1 source-location mechanism
    (supports deterministic traceability/navigation).

  ```
  MemorySourceDocument
      id
      filename
      raw_content
      content_sha256
      revision/version identity

  MemoryClaim
      id
      claim_text
      source_document_id
      source_quote
      source_start_line
      source_end_line
      confirmation_status
  ```

  The document hash proves which immutable source content the location/quote belongs to. Loose
  semantic similarity must not be used as the primary provenance mechanism.
- **Consequence**: this is also the mechanism D-002's "claim identity demonstrably unchanged" test
  relies on — the two decisions are implemented together in Milestone M3.

## D-004: URL fetching strategy for v1

- **Status**: **APPROVED IN PRINCIPLE** (2026-09-02)
- **Requirement**: AJ-006
- **Approved decision**: HTTP fetch → main-content extraction → quality/usability check → either
  continue, or explicitly report an unusable fetch and present the pasted-text fallback.
  Pasted-text intake remains a first-class path. Do **not** introduce Playwright/headless-browser
  infrastructure in v1.
- **Deferred to M4**: the exact extraction library is **not** frozen during M0/M1 — it is selected
  and documented as an implementation dependency when Milestone M4 starts, not before.
- **Consequence**: M1/M2 add no fetch/extraction dependency; that dependency choice is scoped to
  M4's own commit, keeping earlier milestones' dependency footprint smaller.

## D-005: Structured-output representation across providers

- **Status**: **APPROVED** (2026-09-02)
- **Requirement**: LLM-007
- **Approved decision**: Pydantic models are the canonical structured-output contract.
  Provider-specific schema translation belongs **entirely inside** `llm_provider`. Pipeline/domain
  code must never contain Gemini/OpenAI/NVIDIA-specific schema workarounds. The returned provider
  result is validated again against the canonical Pydantic model before becoming trusted
  application data (i.e., a round-trip: canonical schema out, provider-specific translation in the
  adapter, re-validation against the same canonical model on the way back in).
- **Consequence**: confirms `docs/ARCHITECTURE.md` §3's adapter design as approved, not merely
  proposed — M2 implements it as specified there.

## D-006: Freshness/staleness mechanism

- **Status**: **APPROVED WITH MODIFICATION** (2026-09-02)
- **Requirement**: HITL-007
- **Original recommendation**: block-on-stale, detected via an `updated_at` snapshot comparison.
- **Approved decision**: the **block-on-stale, operator-visible behavior is approved as-is** —
  silent regeneration remains prohibited. However, freshness must be determined **primarily by
  immutable upstream artifact identity/version references, not `updated_at` timestamps**. Example:
  `FitAssessment.based_on_jra_id` must equal `JobApplication.current_jra_id`;
  `ResumeDraft.based_on_fit_assessment_id` must equal
  `JobApplication.current_fit_assessment_id`. If these identities differ, the downstream artifact
  is stale — do not silently regenerate; show the operator that upstream context changed and
  require an explicit next action. Timestamps may remain audit metadata but must not be the
  fundamental freshness identity.
- **Consequence**: this decision is what makes `JobApplication`'s current-version pointers
  (D-012) load-bearing, not just a UI convenience — freshness correctness depends on them.

## D-007: Exact resume markdown template

- **Status**: **RESOLVED FOR V1 CONTENT STRUCTURE** (2026-09-02)
- **Requirement**: FUT-001
- **Resolution**: the product owner supplied a resume-content reference. Its factual content is
  **not** hard-coded anywhere in this repository — it is a structural/content-design reference
  only. The resolution is captured as a structured resume representation (produced by Agent
  Builder before any markdown exists) plus a deterministic v1 markdown rendering contract, fully
  specified in `docs/RESUME_OUTPUT_STRUCTURE.md`. Every factual structured element carries
  `supporting_memory_claim_ids` (and, where relevant, `matched_job_requirement_ids`); markdown is
  rendered only after the structured representation passes the no-fabrication validator (D-014).
- **Consequence**: Milestone M6's previous hard blocker is resolved — M6 can proceed against
  `docs/RESUME_OUTPUT_STRUCTURE.md` (see `docs/IMPLEMENTATION_PLAN.md`).

## D-008: Token/cost visibility

- **Status**: **APPROVED WITH REPRIORITIZATION** (2026-09-02)
- **Requirement**: NFR-004
- **Original recommendation**: add pricing fields to `LLMModel` and compute/store `cost_usd` on
  `LLMCallLog` at call time, positioned as a Milestone M2 concern.
- **Approved decision**: the product owner is primarily interested in **token consumption**, not
  provider pricing, at this stage. V1 must record and report, per LLM call: provider, model,
  pipeline stage, input tokens where reported, cached input tokens where reported, output tokens
  where reported, total tokens where meaningful/reported, latency, retry count, sanitized
  success/error category. The system must be able to aggregate token consumption per
  `JobApplication`, per pipeline stage, per provider, and per model. **Dollar-cost calculation is
  optional/deferred** and must **not** be a blocker for Milestone M2. The `LLMModel`/`LLMCallLog`
  design must allow pricing metadata to be added later without reworking `LLMCallLog`. If pricing
  support is added later, the operator manually configures model pricing, and historical cost uses
  pricing snapshotted at call time rather than recalculating against a changed future price (this
  part of the original recommendation is preserved, just deferred in priority, not discarded).
- **Consequence**: `LLMModel` does not need pricing fields at M2; `LLMCallLog`'s token fields
  (input/cached-input/output/total) are the M2 requirement, and pricing fields are additive later
  with no schema rework.

## D-009: Provider/model capability representation

- **Status**: **APPROVED** (2026-09-02, unchanged — low ambiguity, confirmed as originally
  proposed)
- **Requirement**: LLM-005
- **Decision**: simple boolean/integer flags exactly as requirements.md §9.2 names — structured-
  output support, streaming support, reasoning/thinking support, max output tokens. No richer
  capability schema.

## D-010: Stage artifact versioning semantics

- **Status**: **APPROVED** (2026-09-02) — consolidates the former D-010 and D-013 into one
  mechanism
- **Requirement**: DATA-004, DATA-005, DATA-007
- **Approved decision**: `JobRequirementAnalysis`, `FitAssessment`, and `ResumeDraft` are
  **append-only/versioned stage artifacts**. Feedback/regeneration creates a new version; previous
  versions remain available for audit. Approved/reviewed historical artifacts are never
  overwritten. (The former D-013, which proposed versioning `ResumeDraft` specifically as a
  separate concern, is folded into this single decision rather than kept as a parallel mechanism —
  one versioning rule for all three stage artifacts.)
- **Consequence**: `JobApplication` (D-012) is what tracks "current version" for each of the
  three, via `current_jra`, `current_fit_assessment`, `current_resume_draft` pointers, rather than
  scattered `is_current` flags on each versioned row where practical.

## D-011: Application lifecycle / abandonment

- **Status**: **APPROVED, FOLDED INTO DASHBOARD LIFECYCLE** (2026-09-02)
- **Issue**: the original proposal was a standalone `abandoned` flag.
- **Approved decision**: the standalone "abandoned" proposal is superseded by the broader
  `JobApplication` lifecycle/dashboard design (requirements.md new §17): a `pipeline_phase`
  (`NEW`/`ANALYSIS`/`PREPARATION`/`READY`) crossed with an `application_outcome`
  (`NOT_APPLIED`/`APPLIED`/`INTERVIEWING`/`REJECTED`), rather than one overloaded artifact status.
  If a distinct "abandoned" concept is still useful once this two-dimensional model is in use, it
  is retained as an explicit terminal/manual lifecycle state within that model (e.g. as an
  additional `application_outcome` value) rather than a separate overlapping field — this
  refinement is left to Milestone M4/M7 implementation, not decided further here, to avoid
  inventing a state the dashboard design doesn't clearly need yet.
- **Consequence**: no separate `abandoned` boolean is added to the schema now; the dashboard
  model in D-012/requirements.md §17 is the single source of lifecycle truth.

## D-012: JobApplication aggregate

- **Status**: **APPROVED** (2026-09-02)
- **Requirement**: derived from §8 (originally flagged as an added entity requiring approval);
  now also the explicit basis for the new dashboard requirement (requirements.md §17).
- **Approved decision**: `JobApplication` is the aggregate/root entity for one tracked
  vacancy/application. It groups the versioned chain of `JobRequirementAnalysis` →
  `FitAssessment` → `ResumeDraft` (per D-010) via current-version pointers
  (`current_jra`, `current_fit_assessment`, `current_resume_draft`), preferred over scattered
  `is_current` flags where practical. It is also the natural owner of the dashboard lifecycle
  (`pipeline_phase`, `application_outcome` — see D-011 and requirements.md §17).
- **Consequence**: `job_applications` is a real app in the architecture (not a pending proposal —
  see `docs/ARCHITECTURE.md`), and should be established early enough (Milestone M1/M4) that later
  stage models naturally belong to it rather than retrofitting the aggregate at M7.

## D-013: (superseded — merged into D-010)

- **Status**: **SUPERSEDED by D-010** (2026-09-02)
- This entry originally proposed versioning `ResumeDraft` as its own mechanism. The product owner
  directed that it be consolidated into D-010 rather than kept as a separate mechanism for one
  artifact type. See D-010 for the current, single versioning decision covering
  `JobRequirementAnalysis`, `FitAssessment`, and `ResumeDraft` alike.

## D-014: Structured AJ → AC → AB traceability

- **Status**: **APPROVED** (2026-09-02) — new decision, added during product-owner review
- **Requirement**: new explicit product requirement; see requirements.md new §16. Purpose: make
  the no-concealment and no-fabrication invariants **structurally** enforceable, not solely
  dependent on prompting quality.
- **Approved decision**:
  - **Agent Jobber** assigns a stable identifier (`JR-001`, `JR-002`, ...) to every material job
    requirement extracted, categorized (mandatory/preferred/responsibility/ATS-signal/implied
    expectation), stable within its immutable `JobRequirementAnalysis` version.
  - **Agent Candidate** produces, for every relevant `JobRequirement`, an explicit
    `RequirementAssessment` (`requirement_id`, `disposition` ∈ {`MATCH`,`PARTIAL`,`GAP`,`UNKNOWN`},
    `supporting_memory_claim_ids`, `explanation`, `gap_or_limitation`) rather than only free-form
    strengths/gaps text. Absence of evidence must never silently become `MATCH`; only `confirmed`
    `MemoryClaim`s may support a `MATCH`/`PARTIAL`.
  - **Agent Builder** produces structured `ResumeElement`s (`text`, `supporting_memory_claim_ids`,
    `matched_job_requirement_ids`) for every factual output before any markdown is rendered (see
    D-007/`docs/RESUME_OUTPUT_STRUCTURE.md`). The no-fabrication validator checks referenced-claim
    existence, confirmation, eligibility, and context-scoping, and that every factual element
    carries evidence — **not** exact/near-text or embedding-similarity matching, which are
    explicitly excluded as the fundamental truth test. Human review remains mandatory for whether
    generated wording fairly represents the underlying evidence; the machine invariant only
    guarantees evidence attachment and eligibility.
- **Consequence**: this is a real schema/validation addition across `job_intake`,
  `candidate_matching`, and `resume_builder` (Milestones M4, M5, M6 respectively) — not a
  documentation-only change. It directly strengthens NFR-001 and NFR-002 from "prompted for" to
  "validated."
