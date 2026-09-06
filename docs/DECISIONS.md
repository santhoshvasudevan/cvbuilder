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

### M4 follow-up: extraction library selected (2026-09-02)

- **Selected**: `readability-lxml` (import name `readability`), added to `requirements.txt`.
- **Purpose/rationale**: a small, maintained port of Mozilla's Readability algorithm, used only to
  strip chrome (nav/cookie-banners/scripts) from one fetched job-posting page at a time and keep
  the main body text. Chosen over heavier alternatives (e.g. `trafilatura`, which pulls in its own
  crawling/date-parsing dependency chain aimed at bulk corpus scraping) as the smallest maintained
  option that performs well at this narrow, single-URL task — nothing more.
- **Not a completeness guarantee**: this is a heuristic, density/link-ratio-based extractor, not a
  guarantee that any arbitrary job-posting page can be usefully extracted — short pages, heavily
  scripted pages, or pages that block automated access may still fail the deterministic usability
  check downstream. Pasted-text intake remains the guaranteed, first-class fallback in every case;
  URL fetching stays best-effort exactly as this decision's original text already states.
- **Consequence**: no change to the original approved decision above (fetch → extract → usability
  check → fallback, no headless browser); this note only records which library fills the
  already-approved "main-content extraction" step.

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

## D-015: Operator-approved Candidate Memory bootstrap, content classification, and reference export

- **Status**: **APPROVED** (2026-09-02) — new decision, added during M0.1 product-owner review.
  Governs the topics below as one decision; where a topic is a direct extension of an existing
  decision it is cross-referenced rather than re-decided from scratch.
- **Requirement**: extends MEM-001..006 (requirements.md §4); see also D-002 (revision semantics)
  and D-003 (provenance representation), both of which this decision builds on rather than
  supersedes.

### Bootstrap sources (new)

- Three committed markdown files are **operator-approved source evidence**:
  `docs/AC/AC-MEMORY_PROFILE.md`, `docs/AC/AC-profile_english.md`, `docs/AC/AC-profile_german.md`.
- Source precedence/order: `AC-MEMORY_PROFILE.md` is the primary curated profile and
  highest-precedence source for limitations, safe wording, and profile policy;
  `AC-profile_english.md` is operator-approved English evidence and expression corpus;
  `AC-profile_german.md` is operator-approved German evidence and expression corpus.
- Non-conflicting factual claims correctly extracted and deterministically traceable to these
  sources may begin `confirmed` during the initial bootstrap. Any contradictory claim must remain
  ineligible and unconfirmed (`BLOCKED_CONFLICT` or equivalent) until the operator resolves it in
  the UI. Source approval does **not** allow a malformed, unsupported, incorrectly classified, or
  non-traceable LLM extraction to become confirmed — approval status of the *source* never
  substitutes for validation of the *extraction*.

### Canonical language (new)

- English is the canonical language for stored `MemoryClaim` facts. German-language evidence may
  support the same canonical English claim (see `MemoryClaimSupport` in
  `docs/ARCHITECTURE.md` §4). German resume wording is generated only for the relevant selected
  claims when required — the complete memory does not need to be translated on every build. A
  separate stored translation table is optional, not mandatory, for v1; the default is
  translate-on-demand for selected claims.

### Revision semantics (extends D-002, does not replace it)

- D-002's snapshot + incremental model applies unchanged: unchanged source documents (matched by
  content hash) are not reprocessed; new/changed documents are (re)processed; conservative
  reconfirmation applies when claim identity can't be safely established across a change. D-015
  adds nothing new here beyond naming the three bootstrap files as the initial source set this
  model runs against.

### Provenance (extends D-003, does not replace it)

- D-003's guarantees (immutable source identity, SHA-256, exact quotation, deterministic line
  range, no semantic-similarity provenance) are preserved unchanged. D-015 extends the
  *cardinality*: a canonical claim may have multiple exact supporting passages (see
  `MemoryClaimSupport`, `docs/ARCHITECTURE.md` §4), rather than the single-FK model originally
  sketched — needed because German corroborating evidence and English primary evidence for the
  same canonical claim are different passages in different documents.

### Job title vs. positioning (new)

- Actual job titles (factual role-history evidence, e.g. "System Engineer" at Continental) and
  suggested target/resume titles (positioning guidance, e.g. "Senior Solutions Architect" as a
  suggested title for a specific application) serve different purposes and must not be conflated.
  There is no general rule that actual employment titles "win" over suggested titles, or
  vice versa — they answer different questions (what happened vs. how to position it). A suggested
  target title must never be presented as a historical employment title unless separately
  supported as fact.

### Operational memory vs. reference export (new)

- PostgreSQL Candidate Memory is the operational memory; the three markdown files remain immutable
  evidence sources. `docs/CANDIDATE_MEMORY_SNAPSHOT.md` is a concise human-readable export/
  reference, not an authoritative evidence source and not default runtime LLM context. The
  snapshot must never be re-ingested as Candidate Memory evidence.

### No vector database for v1 (new)

- No vector database or embedding-based memory is required for v1. PostgreSQL structured retrieval
  plus a bounded LLM relevance-ranking step is sufficient. `pgvector` remains a possible future
  optimization only if measured retrieval quality requires it — not adopted now, not a default to
  revisit without evidence of a real retrieval-quality problem. **Amended 2026-09-04 (D-020, D-021,
  M5/M6 audit hardening and recall repair)**: "sufficient" is verified against
  **requirement-level evidence coverage** — every `JobRequirement`'s important concepts have
  truthful, source-supported evidence somewhere in the bounded pool — not against surviving-exact-
  claim-ID recall on any specific gold set. A bounded per-requirement cap is expected to keep the
  strongest representative evidence for a concept, not every claim that happens to restate it;
  D-020 added a deterministic BM25-scored candidate-generation step plus a bounded LLM
  relevance-ranking stage (`AC_RANK`) to make that structured retrieval real and measurable, and
  D-021 added rarity-aware scoring plus a bounded requirement-normalization stage (`AC_NORMALIZE`)
  to bridge vocabulary mismatch (paraphrase, non-English requirements) that lexical scoring alone
  cannot close.

### Bootstrap mechanism (new)

- Initial bootstrap is an explicit, repeatable Django management command (conceptually
  `bootstrap_candidate_memory --primary <path> --english <path> --german <path>`), implemented in
  Milestone M3 — **not** automatic import during startup, migration, or deployment. Ongoing
  additions/corrections go through the M3 Candidate Memory UI, creating a new immutable
  `OPERATOR_UPDATE` source document and a new `CandidateMemory` revision (per D-002's snapshot +
  incremental model) — direct database editing is not the normal update workflow.

- **Consequence**: this is a real, non-trivial design and implementation scope inside Milestone
  M3 (see `docs/IMPLEMENTATION_PLAN.md` M3), not a documentation-only change. It is the governing
  decision for the domain-model refinement in `docs/ARCHITECTURE.md` §4
  (`MemoryClaimSupport`, `CandidateRule`, `MemoryConflict`, and the strengthened `CandidateMemory`/
  `MemorySourceDocument`/`MemoryClaim` fields).

## D-016: `FAILED` lifecycle state and bootstrap idempotency/recovery

- **Status**: **PROPOSED** — added while repairing audit findings against the M3 implementation;
  not yet product-owner-approved. Implemented now because the underlying bug (bootstrap could be
  rerun while a working revision was already pending, silently creating an orphaned second
  `NEEDS_REVIEW` revision with no recovery path, and a crash mid-build left a revision stuck at
  `BUILDING` forever) was assessed as a release blocker; the design choice below is what was
  implemented, offered here for confirmation or correction rather than presented as already
  settled.
- **Requirement**: extends D-015's bootstrap-mechanism decision; addresses a gap that decision did
  not originally cover (rerun safety, crash recovery).
- **Issue**: `bootstrap_candidate_memory` (and the "add/update profile" UI workflow) had no way to
  detect that a `BUILDING`/`NEEDS_REVIEW` revision already existed before starting another, and no
  way to distinguish "still legitimately in progress" from "crashed and stuck" — both looked
  identical (`BUILDING`), and only `NEEDS_REVIEW` was ever exposed as "review this," so a crashed
  build with partial content could be mistaken for a genuine, complete, reviewable one.
- **Decision made (pending confirmation)**:
  1. Add a fourth terminal status, `FAILED`, alongside `SUPERSEDED` — reachable only from
     `BUILDING` or `NEEDS_REVIEW`, never from `ACTIVE` (which still only ever moves to
     `SUPERSEDED`), and itself immutable once set (no resurrection; recovery means starting a
     genuinely new revision).
  2. Before starting a build, refuse outright if a `BUILDING`/`NEEDS_REVIEW` revision already
     exists (`services.revision.require_no_working_revision` /
     `ExistingWorkingRevisionError`) — never silently create a second one.
  3. An explicit `abandon_existing=True` argument (CLI: `--abandon-existing`; UI: an "abandon this
     working revision" action) marks the existing one `FAILED` and proceeds — an auditable,
     operator-initiated action, never an automatic side effect of merely retrying.
  4. An unexpected failure during the build itself (not the already-tallied, expected per-chunk/
     per-item extraction errors) marks the new revision `FAILED` with a recorded `failure_reason`
     and whatever partial `build_summary` had accumulated, then re-raises so the caller still sees
     the real error.
  5. No single long-held `transaction.atomic()` wraps the whole build (which would hold database
     locks across dozens of provider calls) — each write still commits as it happens; `FAILED` is
     reached via the try/except above, not via a transaction rollback.
- **Alternative considered**: an `ABANDONED` status distinct from a crash-induced `FAILED`, so an
  operator's deliberate abandonment reads differently from an unexpected crash. Not adopted here —
  both cases are "this revision never became usable and a new one should be started instead," and
  a single terminal `FAILED` status with a `build_summary["abandoned_reason"]` /
  `build_summary["failure_reason"]` key recording *why* seemed like less state to reason about for
  the same information. Flagged explicitly in case the product owner prefers the two-status split.
- **Consequence**: `CandidateMemory.Status` gained `FAILED` (migration
  `0003_alter_candidatememory_status`); `docs/ARCHITECTURE.md` §4's lifecycle diagram needs the
  same addition; `docs/REQUIREMENT_TRACEABILITY.md` needs a row/note for this if the product owner
  confirms the design.

## D-017: Line-wrapped source sentences can fail exact-quote provenance verification

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-02) — discovered during the second live
  NVIDIA qualification call of the M3 extraction-quality repair; the repair below was explicitly
  approved and implemented in the same session. The issue description immediately below is kept
  as originally written for the reasoning trail; see **Resolution** further down for what was
  actually built.
- **Requirement**: extends D-003's provenance representation; a gap that decision did not
  originally cover.
- **Issue**: `services/chunking.py::chunk_source` and `services/storage.py::_verify_quote_at_lines`
  both treat a source document's physical newline-delimited lines as the unit of provenance --
  correct and unambiguous when a logical sentence sits entirely on one physical line. During the
  live qualification call, a synthetic excerpt containing a sentence that word-wraps across two
  physical lines (e.g. "...assigned to\nclient Globex Corporation...", common in hand-wrapped
  markdown prose) caused Nemotron to reconstruct its `support.quote` by joining the wrapped halves
  with a space, while the source's `raw_content` has an actual newline at that point. The exact
  substring check in `_verify_quote_at_lines` then correctly, deterministically rejects the item --
  the item's semantic extraction (subject_scope, claim_type, structured payload) was otherwise
  entirely correct; only the provenance re-verification step fails, silently discarding an
  otherwise-valid claim. Because the same rejection was observed for every item whose supporting
  sentence crossed a physical line-wrap boundary in that call (3 of 5 items), and predates any
  Phase 2 change, this is a structural risk for the real bootstrap sources
  (`docs/AC/AC-profile_english.md`/`AC-profile_german.md`) if they contain hard-wrapped paragraphs
  -- not yet confirmed either way, since those files were not inspected for this specific question
  during the repair (see `docs/CURRENT_STATE.md`'s M3 Phase 2 section).
- **Not yet decided** (superseded by the Resolution below): whether to (a) normalize wrapped-
  newline-vs-space equivalence in `_verify_quote_at_lines`'s comparison (e.g. compare with internal
  whitespace collapsed), (b) instruct the extraction prompt more explicitly to always emit an
  accurate multi-line `start_line`/`end_line` span and a quote matching the source's actual line
  breaks, or (c) some combination. Any change here must preserve D-003's "exact quotation,
  deterministic line range, no semantic-similarity provenance" guarantee -- a whitespace-normalized
  comparison is still an exact match on normalized content, not a fuzzy/similarity match.

### Resolution: whitespace-normalized location, original-slice recovery (2026-09-02)

Option (a) was implemented, precisely scoped to preserve D-003 exactly: `services/quote_recovery.py`
(new) is a fallback path, tried only after `_verify_quote_at_lines`'s existing exact check has
already failed. It normalizes runs of whitespace (including newlines) to a single space in both
the model's quote and the source, and *locates* the quote in that normalized view -- but this is
still an **exact** match on normalized content, never fuzzy or semantic, and only a match that is
**unique** (exactly one occurrence) is ever accepted; zero or multiple normalized matches fail
closed (`recover_quote()` returns `None`, and `store_extracted_item` raises `ProvenanceError`,
never guessing). Once a unique location is found, the function maps back through an explicit
character-offset map to recover the **exact original source slice** at that position -- real
newlines and spacing intact, never the normalized string itself -- and computes real 1-based
`start_line`/`end_line` from those recovered offsets. `services/storage.py::store_extracted_item`
then re-runs the *same, unmodified* `_verify_quote_at_lines` validator against that recovered
slice before trusting it at all, and only the recovered slice/line-range (never the model's
original, inaccurate ones) is what gets persisted into `MemoryClaimSupport`/`CandidateRule`. The
content-hash check (`source_document.verify_content_hash()`) is untouched and still runs first,
unaffected by any of this. 17 new deterministic tests
(`candidate_memory/tests/test_quote_recovery.py`, `test_wrapped_line_provenance.py`) cover exact-
match-unchanged, multi-line wrapping (two and three physical lines), whitespace variety (spaces/
tabs/multiple newlines), ambiguous-duplicate-match rejection, missing-quotation rejection,
hash-tampering still rejected before recovery is attempted, a synthetic fixture matching
`AC-OPERATOR_FACT_RESOLUTIONS.md`'s own confirmed hard-wrapped-at-~90-columns style (see the
D-017 exposure audit below), and confirmation that the bulk corpus's own long-single-line style
needs no recovery at all.

### D-017 real-corpus exposure (read-only audit, 2026-09-02, prior to this fix)

A read-only, non-destructive inspection of all four `docs/AC/*.md` files (line-length and
wrap-boundary heuristics only -- no LLM call, no import, no bootstrap, no personal/contact content
reproduced) found this risk is **not evenly distributed**: `AC-MEMORY_PROFILE.md`,
`AC-profile_english.md`, and `AC-profile_german.md` each write one long logical paragraph/bullet
per physical line (sampled longest lines up to 1240 characters, each ending in `.` or a markdown
table `|`, never mid-word) -- **zero** confirmed mid-clause line-wrap boundaries found in any of
the three. `AC-OPERATOR_FACT_RESOLUTIONS.md` (101 lines), by contrast, is conventionally wrapped
at roughly 80-97 columns and showed **19** confirmed mid-word/mid-clause wrap boundaries -- a
widespread density for that one file. Because that file is the highest-precedence `OPERATOR_UPDATE`
source (precedence 0), this fix directly protects the exact content most at risk of being silently
under-represented in a real bootstrap.
- **Consequence**: flagged as a known, unresolved gap rather than silently accepted; the real
  four-source bootstrap may lose some otherwise-valid claims to this exact failure mode until
  resolved. Not treated as blocking the M3 qualification's own pass/fail verdict, since it is
  orthogonal to the subject_scope/legal-employer semantic-completeness defect that qualification
  round was scoped to fix (operator decision, 2026-09-02).

## D-018: Candidate Memory recovery -- durable chunk-attempt tracking, bounded truncation
## recovery, coarse-duplicate-grouping fix, and strengthened activation validation

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-03) -- directed and approved in the same
  session, following a full read-only review of the real revision-1 bootstrap (v1/id=6). That
  review found `max_output_tokens=4096` truncated 39 of 60 chunks (65%), and separately found that
  the coarse `subject_scope::claim_type` duplicate-grouping fallback (pre-existing since M3, not
  introduced by this session) had silently merged distinct facts sharing a scope+type -- most
  visibly ~30 distinct "responsibility" bullets, two distinct certifications, and two distinct
  degrees collapsed into single claims. Revision 1 (v1) is kept, unmodified and never activated,
  as audit evidence of both defects; recovery happens via a fresh, independent revision 2.
- **Requirement**: extends D-003 (provenance), D-015 (bootstrap mechanism/activation rules), and
  the M3 audit-repair's original (narrower) duplicate-grouping fix.
- **Decision, four parts**:
  1. **Durable per-chunk extraction-attempt tracking** (`ChunkExtractionAttempt`, new model):
     every chunk or sub-chunk attempt gets one row -- revision, source document, source content
     hash, start/end lines, attempt number, status (`SUCCESS`/`FAILED`/`SUPERSEDED`), sanitized
     error category, and the associated `LLMCallLog` row where one exists. This is what lets
     activation validation see "was every part of every source actually covered" without
     re-deriving it from `LLMCallLog` (no source/line reference) or stored claims (silent about a
     chunk that produced zero claims).
  2. **Bounded recursive truncation recovery**: a `finish_reason=length` failure now triggers
     splitting the chunk in half (preserving each half's own original-document line numbers
     exactly) and retrying each half independently, recursively, bounded by
     `MAX_SPLIT_DEPTH=4` and `chunking.MIN_SPLIT_CHUNK_LINES=5` -- reasoning stays disabled and
     `max_output_tokens` stays at its configured value throughout; only chunk size shrinks. A
     minimum-size chunk that still truncates fails closed (`FAILED`, blocks activation) rather
     than splitting forever or silently dropping the gap. A truncated response never has any
     parseable content, so a split chunk can never have already stored anything -- splitting can
     never duplicate a claim.
  3. **Coarse duplicate-grouping fix, extended to all claim types**: `services/storage.py::
     duplicate_group_key` no longer has *any* scope+type fallback, for comparable or non-comparable
     claim types alike -- only an explicit `duplicate_group_hint` (the model's own "same underlying
     fact across languages/passages" signal) merges two items. This is deliberately conservative
     (more, smaller claims, never a fuzzy/semantic identity test) and directly fixes the
     responsibility/certification/degree conflation found in revision 1.
  4. **Strengthened activation validation** (`services/lifecycle.py::activation_blockers`): (a) any
     unresolved (`FAILED`) `ChunkExtractionAttempt` now blocks activation outright --
     `SUCCESS`/`SUPERSEDED` never block; (b) **every** `OPEN` `MemoryConflict` now blocks activation
     outright, superseding D-015's earlier allowance that let one through (with only a warning) as
     long as its claims stayed ineligible -- that allowance is retired, not merely narrowed; (c)
     zero `CONFIRMED` `employment_dates`/`employment_location` coverage now blocks activation unless
     the caller explicitly passes `acknowledge_zero_employment_coverage=True` to
     `activation_blockers()`/`activate_revision()` -- a visible, intentional operator override,
     never a silent default.
  5. **Recovery/force-reprocess path**: `bootstrap_candidate_memory --force-reextract` (with
     `--dry-run` for preview) builds a completely independent new revision, bypassing the existing-
     working-revision guard *without* abandoning, editing, or reading from whatever revision
     already exists -- every supplied source is reprocessed from scratch regardless of content-hash
     match, and nothing is ever carried forward, so a prior revision's defects can never leak into
     the new one via carry-forward.
- **Alternative considered for (4b)**: keep D-015's original "open conflict + ineligible claims is
  only a warning" allowance and rely solely on the new employment-coverage/chunk-attempt blockers.
  Not adopted -- the real bootstrap's own German B1/B2 false-positive conflict (see D-017's
  companion review) showed that "warning only" is too easy for an operator to click past without
  actually reading; a hard block forces an explicit resolve/dismiss action first.
- **Consequence**: `candidate_memory.0004_chunkextractionattempt` migration adds the new model.
  `docs/CURRENT_STATE.md` records revision 1 (v1/id=6) as a completed-but-not-activatable audit
  artifact. Existing tests exercising activation without employment coverage now pass
  `acknowledge_zero_employment_coverage=True` explicitly (recorded as an intentional test update,
  not a weakening, since the check itself is new and correctly firing).

## D-019: The deterministic static-profile boundary

- **Status**: **APPROVED** (2026-09-03, product-owner directive: "implement the deterministic
  static-profile boundary" before M5/M6, and "record an approved architectural decision").
- **Issue**: `docs/RESUME_OUTPUT_STRUCTURE.md`'s `ExperienceSection` (§2.C) originally had Agent
  Builder's own structured output carry `employer`/`role_title`/`dates`/`location` directly,
  "only as supported by CandidateMemory" -- i.e. LLM-generated fields constrained by prompting and
  post-hoc evidence checking, not fields the LLM is structurally forbidden from ever emitting. Real
  `MemoryClaim` data for this candidate's own history (`organization:ford motors`,
  `organization:ford motor werk gmbh`, `organization:ford connectivity`, `organization:ford –
  köln`, ...) shows the same real employer expressed a dozen different ways across extraction
  passes -- exactly the kind of fact an LLM could plausibly restate *slightly* differently each
  time it tailors a resume, which is a materially different (and worse) risk than wording a bullet
  differently: an employer name, title, location, or date range has exactly one correct rendering,
  and any LLM-mediated path to it is one path too many.
- **Decision**:
  1. Employment identity (legal employer, client organisation, approved title(s), location, start/
     end dates) are operator-owned structured facts, recorded once in a new `CareerEngagement`
     model (`candidate_memory/models.py`) independent of any `CandidateMemory` revision's own
     BUILDING/NEEDS_REVIEW/ACTIVE/SUPERSEDED lifecycle -- an admin-editable registry, following the
     same pattern as `llm_provider`'s `LLMProvider`/`LLMModel`, gated by its own `approval_status`
     (`DRAFT`/`APPROVED`/`REJECTED`) rather than being frozen by `CandidateMemory` activation.
  2. Neither the planned M5 (Agent Candidate) nor M6 (Agent Builder) LLM call ever receives or
     produces these fields. M6's planned output schema (`EngagementNarrativeOutput`/
     `EngagementBullet`, `services/static_profile_boundary.py`) references only an `engagement_id`
     plus evidence-backed narrative bullets, with `extra="forbid"` so a provider that tried to
     smuggle an `employer`/`title`/`date` field through fails schema validation outright, not
     merely "gets ignored by convention."
  3. A resume experience-section header is rendered deterministically
     (`render_engagement_header`) exclusively from an `APPROVED` `CareerEngagement` record, resolved
     fresh from the database by `engagement_id` every time. An `engagement_id` that does not exist,
     or that is not `APPROVED`, fails validation (`UnknownOrUnapprovedEngagementError`) rather than
     rendering a placeholder or falling back to whatever text an LLM supplied.
  4. `MemoryClaim`s reference a `CareerEngagement` through a **separate** `ClaimEngagementMapping`
     table, never a field on `MemoryClaim` itself -- proposing, approving, or rejecting a mapping
     therefore never mutates a `MemoryClaim` row, so it is safe to run against claims belonging to
     an already-`ACTIVE` revision without violating the frozen-content invariant
     (`docs/ARCHITECTURE.md` §4 `CandidateMemory`). `services/engagement_mapping.py` proposes a
     mapping only on an exact, normalized match between a claim's `legal_employer`/
     `client_organization` (or, failing that, its `subject_scope`) and an `APPROVED` engagement's
     own fields -- never fuzzy/embedding similarity. Zero matches or more than one candidate match
     is left **unresolved** for the operator, never guessed; no source document is ever
     re-extracted to produce or refine a mapping.
  5. Deterministic calculations -- `CareerEngagement.duration_months`/`is_current`/
     `displayed_organization`/`title_for_language` (model methods) and
     `services/career_engagement.total_non_overlapping_experience_months` (interval-merges
     overlapping engagements so concurrent roles are never double-counted) -- give the planned M5
     stage a way to assess static, structural requirements (tenure, current/past status, location,
     employment relationship) **locally, without an LLM call**
     (`services/static_profile_boundary.assess_tenure_requirement_locally`/
     `assess_location_requirement_locally`).
  6. The planned M5 `RequirementAssessment` evidence-attachment shape gains
     `supporting_engagement_ids` alongside the existing `supporting_memory_claim_ids`
     (`RequirementEvidenceReference`, `services/static_profile_boundary.py`) -- a disposition may
     now be satisfied by engagement evidence, claim evidence, or both.
- **Explicitly not done by this decision**: no `JobRequirement`/`FitAssessment`/`ResumeDraft`
  Django model was created or modified (M5/M6 remain not started); no real `CareerEngagement` row
  was created for the operator's actual employment history -- that is deliberate future operator
  data-entry/review work through the normal admin/service workflow, never hardcoded into a
  migration or seed script; `requirements.md` was not amended (this refines the data model's
  "field-level detail... worked out during implementation" per §8, not a requirement change).
- **Consequence**: `candidate_memory.0006_careerengagement_claimengagementmapping` migration adds
  both new models; `docs/RESUME_OUTPUT_STRUCTURE.md` §2.C's `ExperienceSection` now carries an
  `engagement_id` instead of freeform `employer`/`role_title`/`dates`/`location`;
  `docs/ARCHITECTURE.md` §4 gains `CareerEngagement`/`ClaimEngagementMapping` entries and updates
  `FitAssessment`/`RequirementAssessment`/`ResumeDraft`; `docs/REQUIREMENT_TRACEABILITY.md` gains
  `CE-001`..`CE-00N` rows; `docs/TEST_STRATEGY.md` and `docs/IMPLEMENTATION_PLAN.md`'s M5/M6
  sections are updated accordingly.

### D-019 refinement: the static/narrative claim-eligibility boundary (2026-09-03, APPROVED)

The first pass above proposed mappings for exactly `employment_dates`/`employment_location`
claims -- backwards from the actual intent. Those two types (plus `position`/`position_title`,
verified against the real activated CandidateMemory's own `claim_type` vocabulary, not guessed)
are **static engagement claim types**: their entire factual content is now owned by
`CareerEngagement` directly, so they must never be proposed for mapping, never be eligible for
approval, and never enter a future M5/M6 LLM input projection.
`services/engagement_mapping.STATIC_ENGAGEMENT_CLAIM_TYPES` is the definitive, evidence-based list.
`ClaimEngagementMapping` exists only to link **narrative** evidence -- responsibilities,
achievements, projects, role-specific skills/technical delivery -- to the engagement a future M6
should place it under; `propose_claim_engagement_mappings` now excludes the static types (and
requires `resume_eligible=True`); `approve_mapping` refuses (`StaticClaimMappingError`) to approve
a static-type mapping even if a stale row exists from before this refinement; the admin's bulk
approve action is routed through `approve_mapping` per row (never a bulk status update) so the
refusal is enforced through the real workflow. `find_mappings_needing_review` gives a read-only
categorization of existing mappings against this boundary, and the
`cleanup_static_engagement_mappings` management command (report by default; `--apply` to act)
retroactively rejects the 9 pre-refinement mappings that were proposed under the old, backwards
rule. `approved_narrative_claim_ids_for_engagement` is the planned M6 renderer's lookup for which
narrative claims to place under one engagement's experience section -- the concrete form of item 6
above ("use mappings only to place narrative bullets under the correct engagement").

## D-020: M5/M6 audit-hardening -- bounded relevance retrieval, engagement-correct evidence,
## and gate/failure hardening

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-03) -- directed and approved in the same
  session, following an independent adversarial audit of the M5/M6 implementation (commits
  `aed6b32`/`88c8770`). Two release-blocking findings and several non-blocking gaps were confirmed
  live against the real ACTIVE CandidateMemory before being fixed.
- **Requirement**: corrects the M5 implementation of D-015's "PostgreSQL structured retrieval plus
  a bounded LLM relevance-ranking step" clause (that clause was written but never actually built --
  see the finding below); extends D-019's engagement-placement guarantees; hardens D-006/D-010's
  gate and versioning mechanics under concurrency and provider failure.
- **Finding 1 (blocking, fixed)**: `candidate_matching.services.retrieve.retrieve_context()` sent
  **the entire eligible pool** to both `AC_MATCH` and `AB_BUILD` -- measured at 1,122 claims/359
  rules, a ~203,000-character (~50,785-estimated-token) request against the real revision-2
  corpus, with no relevance filtering, no count/token bound, and no deduplication. This had been
  marked "Implemented" against MEM-015 on a 4-claim fixture test that never exercised scale.
  **Fixed**: a real, measurable bounded pipeline (`services/bounded_retrieval.py` and its
  supporting modules -- `dedup.py`, `candidate_generation.py`, `lexical_relevance.py`, `rank.py`,
  `rule_selection.py`, `retrieval_limits.py`) -- eligibility (unchanged) -> conservative
  deduplication (exact normalized text or an explicit `duplicate_group_key`, never fuzzy) ->
  deterministic per-`JobRequirement` lexical candidate generation with a guaranteed minimum
  candidate floor -> a genuinely new bounded LLM relevance-ranking stage (`AC_RANK`, a new
  `StageModelAssignment.Stage` value) that only ever sees the bounded candidate pool and never
  falls back to the full corpus on any error or malformed/truncated output -> capped final
  selection against documented hard limits chosen against the real corpus's own scale. Exceeding
  the final token budget after every count cap raises an actionable
  `RetrievalBudgetExceededError` rather than silently truncating. Every `FitAssessment` now carries
  an inspectable `retrieval_manifest` (counts, exclusion reasons, per-requirement selected IDs,
  cap configuration -- never raw prompts/responses). M6 was also found to be reusing M5's
  unbounded retrieval directly; it now builds its own `BuilderContext`
  (`resume_builder/services/context.py`) strictly from what the current `FitAssessment` already
  selected, re-verifying each claim/engagement is still eligible/approved rather than trusting a
  stored ID list or re-querying the full CandidateMemory.
- **Finding 2 (blocking, fixed)**: `resume_builder.validators.no_fabrication.validate_and_flatten`
  checked only that a cited `claim_id` existed somewhere in the retrieved context -- it never
  checked that the claim was actually approved for the *specific* engagement its bullet was placed
  under. Reproduced live: a claim mapped only to Engagement A was accepted under Engagement B's
  experience section with no rejection, contradicting the milestone's own "incorrect engagement
  placement fails closed" claim. **Fixed**: `RetrievedClaim.engagement_id` (a single nullable
  field) is replaced everywhere by `approved_engagement_ids` (the complete set of a claim's
  approved mappings -- a claim can legitimately be approved for more than one engagement, which
  the old shape could never represent), and every experience bullet is now checked against that
  complete set: a claim approved only for a different engagement fails the whole build closed;
  global (unmapped) evidence may supplement a bullet that already has at least one genuinely
  matching claim, but can never be its sole support.
- **Non-blocking findings, also addressed**: `get_adapter_for_stage` now refuses to route a real
  stage through a `FAKE`-typed provider outside an automated test run (`settings.TESTING`);
  `JobApplication.approve_gate1`/`approve_gate2` are now `select_for_update()`-locked and
  idempotent (a repeated approval of an already-approved, still-current artifact is a safe no-op,
  not an error), and the same version-allocation race is closed the same way in
  `build_fit_assessment`/`build_resume_draft`/`job_intake.services.intake.rerun_analysis`
  (`ConcurrentModificationError` on the residual, effectively-unreachable IntegrityError case);
  Gate 1 now shows screening risks and the retrieval manifest inline; Gate 2 now links every
  supporting claim ID to its CandidateMemory detail/quotation page and marks global/supplementary
  evidence distinctly.
- **Alternative considered**: relying on the LLM ranking step alone (no deterministic lexical
  pre-filter) to narrow 1,122 claims down to a bounded set in one call. Not adopted -- a single
  ranking call over the full corpus would itself be the same unbounded-request problem this
  decision fixes; the deterministic candidate-generation step is what keeps every individual
  request (including the ranking request itself) bounded.
- **Consequence**: `llm_provider.0002_alter_llmcalllog_stage_and_more` adds the `AC_RANK` stage
  choice; `candidate_matching.0002_fitassessment_retrieval_manifest` adds the manifest field. Real
  end-to-end verification (a rolled-back manual walkthrough against the real ACTIVE
  CandidateMemory and real APPROVED CareerEngagements) confirmed the bounded pipeline produces a
  real, working assessment and resume draft at a small fraction of the pre-fix request size. No
  live provider call was made or authorized for this work; `AC_RANK`/`AC_MATCH`/`AB_BUILD` have no
  real `StageModelAssignment` configured in the development database.

## D-021: Rarity-aware BM25 candidate scoring and a bounded requirement-normalization stage
## (AC_NORMALIZE) for D-015's lexical retrieval step

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-04) — directed by the product owner following
  an independent re-audit's recall finding against D-020's bounded retrieval pipeline; implemented
  and measured live against the real ACTIVE CandidateMemory in the same session. An initial
  verification pass used exact-claim-ID recall (19 predeclared critical claim IDs across five gold
  profiles) as its acceptance bar and reached 16/19, which the product owner then explicitly
  replaced with a **requirement-level evidence-coverage** standard: bounded retrieval must ensure
  every requirement's important concepts are truthfully supported by *some* candidate in the pool,
  not that every specific claim ID a prior audit happened to point at survives a bounded cap — a
  real corpus commonly contains several claims that separately restate the same underlying fact
  (different engagements, different phrasing, near-duplicate skill bullets), and a bounded
  per-requirement cap is expected to keep the strongest few, not all of them. An acceptance review
  (Finding 3 below) inspected the three claims that did not reach the pool under the exact-ID
  standard and confirmed each is genuinely redundant with claims that did — no requirement's
  evidence coverage is weakened, no requirement risks an incorrect GAP disposition, and no unique
  capability, engagement, or evidence strength was lost. Committed on that basis.
- **Requirement**: corrects two independent recall problems found in D-020's
  `candidate_generation.py`/`lexical_relevance.py` (the deterministic BM25/phrase/acronym scoring
  layer) and extends D-015's "structured retrieval plus a bounded relevance-ranking step" with one
  additional stage that runs *before* that scoring, per the product owner's explicit architecture:
  `JobRequirement -> bounded canonical English search representation -> BM25 candidate generation
  -> AC_RANK -> AC_MATCH`.
- **Finding 1 (fixed, same-day iteration)**: `lexical_relevance.py`'s first working version scored
  a claim by raw shared-token count with no rarity weighting at all, letting corpus-common domain
  words ("vehicle", "connected", "cloud") outweigh a genuinely rare, diagnostic term
  ("Kubernetes", ~2.7% document frequency in the real corpus) that should have dominated. **Fixed**
  with a from-scratch rewrite: BM25 (Robertson/Sparck-Jones IDF + term-frequency saturation) over
  the actual eligible-claim corpus given at call time, with a corpus-relative common-term
  dampening rule (document frequency > 8% of the corpus) on top of BM25's own log-IDF; a
  rarity-weighted exact-phrase (bigram/trigram) bonus, where a matched phrase's weight comes from
  the *average unigram IDF of its own constituent words* (not the phrase's own document frequency
  — tried first, and rejected: an exact multi-word phrase is inherently sparse as a string
  regardless of how common its individual words are, so phrase-level document frequency alone
  could not tell a common phrase like "connected vehicle" from a rare one like "adaptive cruise
  control"); a rarity-weighted acronym/technical-proper-noun bonus (a short acronym, or any other
  capitalized token not at the start of the text, weighted by its own unigram document frequency);
  and a corpus-driven singular/plural merge (a trailing "s" is stripped only when the resulting
  singular form is itself a real token elsewhere in the corpus, fixing a fragmentation artifact
  where "platforms" scored as artificially rare purely because most claims happened to say
  "platform" — while never touching a domain proper noun like "Kubernetes" that has no matching
  singular form in the vocabulary). Two further correctness bugs surfaced and were fixed during
  this same rewrite: (a) the tokenizer's word regex deliberately includes "." to keep decimal/
  version tokens ("3.5") intact, which glued an ordinary sentence-final period onto that sentence's
  last word ("platforms." as a token distinct from "platform"), corrupting that word's measured
  document frequency across the whole corpus; (b) the acronym/technical-proper-noun detector's
  "not the first word of the text" rule assumed a single sentence, so once
  `normalize.build_search_text` began concatenating a restated sentence after the original, that
  second sentence's own ordinary first word ("Experience...") was wrongly flagged as a technical
  term — and because a stopword's document frequency is never measured (stopwords are filtered out
  of `build_corpus_stats` entirely), the resulting lookup read that absence as "the rarest possible
  term" and awarded a large, spurious bonus to every claim containing that ordinary word. Both are
  now guarded explicitly (trailing-period stripping in tokenization; a stopword exclusion in
  acronym detection).
- **Finding 2 (fixed)**: two full-profile recall regressions were found and fixed during
  verification. A German-language query's grammatical filler words ("mit", "und", "für") were not
  filtered as stopwords (the existing stopword list is English-only), so they spuriously matched
  the ~30 German-language claims already present in the real ACTIVE CandidateMemory's own
  `canonical_text_en` field (a pre-existing bootstrap data-quality gap — not something this
  decision alters), drowning out genuinely relevant English technical claims. **Fixed** by adding a
  small set of common German connector/function words to the shared stopword list — the same class
  of noise word English "and"/"with"/"for" already are, not a translation layer. Separately,
  concatenating a requirement's near-duplicate canonical restatement (common for an
  already-clear-English requirement, where "normalize, don't reword" produces close to the
  original) was found to still shift the exact-phrase bonus via spurious sentence-boundary
  n-grams enough to push a borderline claim out of the bounded per-requirement cap, even after
  Finding 1's fixes. **Fixed** in `normalize.build_search_text`: a canonical restatement that is
  the same content as the original after whitespace/case normalization (the same equality check
  `dedup.py` already uses to decide two claims are the same content) is not concatenated a second
  time.
- **New stage (AC_NORMALIZE)**: added as its own `StageModelAssignment.Stage` value (matching
  D-020's `AC_RANK` precedent) rather than reusing an existing stage, since it is architecturally
  distinct (it runs *before* candidate generation, not after) and needs its own auditable
  `LLMCallLog`/registry assignment. `candidate_matching.services.normalize` receives only a
  requirement's `requirement_id`/`text` and the job posting's shared `posting_language` — never a
  MemoryClaim, a CareerEngagement, or any candidate/employment field — and returns one bounded,
  `extra="forbid"` `RequirementNormalizationItem` per requirement (canonical English text,
  ≤8 diagnostic terms, ≤6 equivalents, ≤10 preserved technical terms, each ≤60 characters, the
  restatement itself ≤500 characters — `candidate_matching.services.normalization_limits`),
  validated as a schema-level constraint (an over-large or malformed response is a
  `SCHEMA_VALIDATION` error, handled by the same uniform `BaseLLMAdapter.generate` path every
  other stage uses) rather than truncated after the fact. A response that adds, drops, or renames a
  requirement_id, or that errors, raises `NormalizationFailedError` in
  `bounded_retrieval.build_bounded_context` — propagated exactly like D-020's `RankingFailedError`,
  never silently falling back to unexpanded (degraded-recall) retrieval. The output is a retrieval
  hint only: `RequirementNormalizationItem` has no field that could carry a claim id or evidence,
  and is recorded on `FitAssessment.retrieval_manifest` for operator inspection (original text,
  canonical text, and the three bounded term lists per requirement) but is never read by
  `resume_builder`'s no-fabrication validator or rendered into a resume.
- **Finding 3 (acceptance review — resolved: all three genuinely redundant)**: re-running the same
  five gold profiles the independent re-audit used (with hand-authored, deterministic stand-ins for
  AC_NORMALIZE's output, written from each job requirement's own stated meaning only, never from a
  specific claim's wording) achieved 16 of 19 predeclared critical claim IDs reaching the
  pre-`AC_RANK` candidate pool — the Kubernetes-vs-common-word regression named in the original
  audit is fixed and independently reproducible (Profile 1 and the German Profile 5 both went from
  a partial to a full 4/4). The three claims that did not reach the pool under the exact-ID
  standard were each individually inspected against what *did* reach the pool for the same
  requirement, comparing actual capability/scope/engagement/evidence strength, not wording
  similarity:
  - **MC-7-0146** ("Lead end-to-end solution architecture and integration for connected-vehicle
    services... Google Cloud platforms... international alliance partners", engagement CE-0001,
    Profile 2): every distinct concept it carries — solution-architect-level ownership of
    connected-vehicle services (MC-7-1002, "acting solution architect responsibility for connected
    vehicle services in complex international alliance projects"), architecture-level cloud/
    connected-vehicle ownership at the *same* engagement (MC-7-1052, CE-0001), and
    integration/alliance/partner coordination at the *same* engagement (MC-7-0980, CE-0001;
    MC-7-0556, CE-0001, "Connectivity Cloud product ownership in Ford Joint Venture alliance
    program") — is independently present in the pool, several of them tied to the identical
    engagement. **REDUNDANT — COVERAGE PRESERVED.**
  - **MC-7-0175** ("Led E2E connectivity integration in the Ford-VW alliance for a Ford-branded
    customer experience", unmapped/global, Profile 2): the same real-world achievement class
    (connectivity/alliance integration in the Ford joint-venture context) is present in the pool
    via MC-7-0556 (CE-0001, explicitly "Ford Joint Venture alliance") and MC-7-0980 (CE-0001,
    "integration and release activities between OEM, partner, cloud, and development teams") — and
    both are *engagement-mapped*, which is source-attributable, stronger evidence than MC-7-0175's
    own unmapped/global status. The "Ford-branded customer experience" framing adds no distinct
    capability relevant to the requirement (product ownership/OEM stakeholder coordination) beyond
    what those two already establish. **REDUNDANT — COVERAGE PRESERVED.**
  - **MC-7-0033** ("Improved connected vehicle data accuracy above 99% through automated
    validation, dbt checks, data contracts, and monitoring", unmapped, Profile 3): notably, this
    claim does not itself name SQL, Airflow, or BigQuery — it was already a comparatively weak
    direct match for a requirement asking for evidence of building pipelines with that specific
    tool stack. Its core substance (automated data-quality validation via dbt, yielding measurably
    high accuracy) is present in the pool via MC-7-0376 (CE-0001, "automated quality checks and
    high data accuracy"), MC-7-0121 ("dbt-style data-quality controls"), and monitoring is
    explicitly present via MC-7-1026/MC-7-1027 ("dbt, and monitoring") and MC-7-0564
    ("metric-based alerts"). "Data contracts" as an exact term is not repeated verbatim elsewhere
    in the pool, but is not itself a distinct capability the requirement asks for beyond the
    data-quality/validation theme those claims already establish. **REDUNDANT — COVERAGE
    PRESERVED.**
  In all three cases the pool retains, for the same requirement, at least one candidate with equal
  or stronger evidence (often engagement-mapped where the excluded claim was unmapped) covering
  every distinct concept — no requirement's evidence coverage is weakened and none would
  incorrectly surface as a `GAP` because of the exclusion.
- **Alternative considered**: raising `MAX_CANDIDATES_PER_REQUIREMENT`/`MAX_RANKING_CANDIDATES` to
  force every exact predeclared claim ID into the pool regardless of redundancy. Explicitly
  rejected by the product owner's own instruction ("do not increase limits as the primary fix," and
  later "do not tune retrieval merely to force specific claim IDs into the pool") — the caps are a
  D-020 architectural boundary, not a tuning knob for one gold set, and forcing in a claim already
  redundant with pool content would not improve the fit assessment's truthfulness.
- **Requirement-level coverage, not exhaustive duplicate inclusion**: bounded retrieval's guarantee
  is that every requirement's important concepts have truthful, source-supported evidence
  *somewhere* in the bounded candidate pool — never that every claim a human reviewer might
  independently point to survives the cap. A real CandidateMemory routinely contains several claims
  restating the same underlying fact (different engagements, different phrasing, near-duplicate
  skill bullets); the bounded per-requirement cap is designed to keep the strongest representative
  evidence, not all of it. This is the acceptance standard this decision is verified against, not
  exact-claim-ID recall against a fixed gold set (which risks overfitting the scorer to that set).
- **Consequence**: `llm_provider.0003_alter_llmcalllog_stage_and_more` adds the `AC_NORMALIZE`
  stage choice. `RetrievalManifest.requirement_normalization` is a new field
  (`candidate_matching.services.bounded_retrieval`) — no model migration, since `FitAssessment.
  retrieval_manifest` is already a JSONField (D-020). No live provider call was made or authorized;
  `AC_NORMALIZE` has no real `StageModelAssignment` configured in the development database.

## D-022: Agent Jobber semantic sanity gate — reject a schema-valid, substantively empty analysis

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-04) — directed by the product owner after a
  live controlled Gate-1 preparation run against a real posting produced JobApplication id=9: a
  schema-valid `AgentJobberAnalysis` (posting language and role title correctly detected, an
  articulate 14-item `screening_risks` list) with **zero** `JobRequirement` rows. `run_intake`
  persisted it as the application's current, "successful" analysis anyway, because
  `schemas.AgentJobberAnalysis` only ever validated *shape* (a `requirements` list of any length,
  including empty, was always schema-legal — see `test_minimal_response_with_no_requirements_
  still_validates`, unchanged by this decision), and nothing downstream of schema validation asked
  whether the result was substantively usable.
- **Root cause (inspected: prompt, schema, persistence, validation path)**: four independent
  factors compounded into one failure, none of them a schema bug:
  1. `services/analyze.py`'s prior `SYSTEM_PROMPT` described "screening risks" as "things that
     might get a candidate filtered out" without ever stating Agent Jobber has no candidate
     context — an invitation, not a prohibition, to phrase posting content as a judgment about an
     unseen candidate.
  2. Nothing in the prompt said a zero-`requirements` result for a real posting is itself wrong;
     the model was free to fold every responsibility/qualification into `screening_risks` instead
     of `requirements` and never be told that was a category error.
  3. `schemas.AgentJobberAnalysis.requirements` had (and, deliberately, still has — see the M2
     Candidate Memory precedent of schema-permits/service-rejects) no minimum-length constraint,
     so this was never a `SCHEMA_VALIDATION` error the existing `BaseLLMAdapter.generate` path
     would have caught.
  4. `services/intake.py::run_intake`/`rerun_analysis` went straight from a schema-valid
     `AgentJobberAnalysis` to persistence — there was no semantic check in between at all.
- **Fix — strengthened AJ contract (`services/analyze.py::SYSTEM_PROMPT`,
  `schemas.py::RequirementCategory`/`ScreeningRisk`)**: the prompt now states explicitly that
  Agent Jobber has no candidate/history/Candidate-Memory context and must never write a sentence
  that judges whether "the candidate" has, lacks, or falls short of a capability (with the exact
  gap-phrasing patterns to avoid named); that every explicit responsibility/qualification/skill/
  experience expectation becomes one atomic `requirements` item (never summarized away into
  `screening_risks`); that MANDATORY is used only when the posting states or clearly requires it,
  PREFERRED only for preferred/desirable/advantageous/nice-to-have content; and that
  `screening_risks` holds *only* explicit hiring constraints/conditions the posting itself states
  (work authorization, mandatory on-call, clearance, relocation, and similar), each requiring a
  verbatim `source_context` quotation — if it can't be quoted, it isn't a screening risk. The
  schema's new `ScreeningRisk` model (replacing a bare `list[str]`) makes `source_context`
  structurally mandatory for every risk (`field_validator`, non-blank), mirroring
  `ExtractedRequirement`'s existing optional one — a risk without grounding text is not an
  explicit constraint at all.
- **Fix — deterministic semantic sanity validator (`job_intake/validators/sanity.py`,
  `find_sanity_violations`)**: a new layer between schema validation and persistence, lexical and
  deterministic throughout (never a fuzzy/semantic similarity test, consistent with
  `candidate_matching.services.dedup`/`candidate_memory.models.MemoryClaimSupport.
  verify_against_source`'s established convention). Rejects, at minimum: zero `requirements` for a
  posting at or above `SUBSTANTIVE_TEXT_MIN_CHARS` (300 chars — comfortably above
  `intake.MIN_PASTED_TEXT_CHARS`'s 20-char floor, which only guards near-empty input, not "too
  short to be a real posting"); a non-empty `screening_risks` list produced alongside zero
  `requirements` (the exact observed failure shape); any requirement or screening-risk text
  containing a candidate-gap marker phrase ("lack of", "no experience", "no proven", "no track
  record", "insufficient", "absence of", "unable to", and similar — `GAP_LANGUAGE_MARKERS`,
  matched case-insensitively as a literal substring) — enforcing the "no candidate context"
  prohibition mechanically, not just in the prompt; a duplicate requirement (same category and
  same normalized text extracted twice from one posting); and a MANDATORY/PREFERRED/RESPONSIBILITY
  requirement or *any* screening risk whose `source_context` is not a real, exact substring of the
  posting text actually analyzed (`ATS_SIGNAL`/`IMPLIED_EXPECTATION` are exempt from this
  requirement — the former is often a bare keyword, the latter is by definition not stated
  outright). `run_intake`/`rerun_analysis` call this after a successful, schema-valid provider
  result and before the persistence transaction begins; any violation raises
  `SemanticValidationError` (a subclass of the existing `AnalysisFailedError`, so the intake view
  and Gate-1 feedback re-run already handle it via their existing exception handling, with no view
  change needed for that path).
- **Atomicity preserved, nothing new persisted on rejection**: `SemanticValidationError` is raised
  strictly before `transaction.atomic()` opens (same structural position as the pre-existing
  `result.is_error` check) — no partial `JobRequirement` row, no `JobApplication`/
  `JobRequirementAnalysis`, no `current_jra`/pipeline-phase advancement is ever created for a
  rejected analysis. The underlying provider call's `LLMCallLog` row is still written (LLM-010:
  every call is logged regardless of what happens next) and, as always, stores only token counts/
  latency/error-category metadata — never the raw posting or response content — so nothing
  sensitive is retained by this new failure path either.
- **M5 precondition, applies to every current JRA including pre-existing ones**:
  `candidate_matching.services.fit_assessment.build_fit_assessment` now raises
  `AgentCandidateError` immediately if the current JRA has zero `JobRequirement` rows, before any
  retrieval or LLM work begins. This is a fresh runtime check against whatever the current JRA
  actually contains — it is not something baked in at JRA-creation time — so it correctly covers
  JobApplication id=9's real, legacy, pre-D-022 JRA (version 1, left permanently unedited per
  `JobRequirementAnalysis`'s append-only guarantee) without touching that row at all.
- **UI, read-only**: `job_intake` 's analysis-detail page now computes (never stores)
  `is_incomplete = jra is not None and jra.requirements.count() == 0` and shows a clear warning
  banner when true ("INCOMPLETE ANALYSIS ... not eligible for Gate 1"), covering both a
  newly-impossible-to-create case and this exact pre-existing legacy JRA. The "Go to Gate 1" link
  is left in place rather than hidden — `reviews.views.gate1_view` already catches
  `AgentCandidateError` cleanly, so clicking through and attempting a run surfaces the same clear
  error rather than a dead end, and the page's Gate-1 feedback ("re-run Agent Jobber") action
  remains the intended recovery path.
- **Alternative considered**: minimum-length `Field` constraint on `AgentJobberAnalysis.
  requirements` (schema-level rejection). Rejected — Pydantic has no way to make a minimum
  conditional on posting substantiveness (a genuinely short/non-substantive posting legitimately
  has zero requirements, per `test_short_posting_with_zero_requirements_is_accepted`), and schema
  validation cannot see the gap-language/duplicate/provenance problems at all; a dedicated
  post-schema semantic layer was necessary regardless.
- **Consequence**: no model migration — `JobRequirementAnalysis.screening_risks` remains the same
  `JSONField`, now storing `{"text": ..., "source_context": ...}` objects instead of bare strings
  (a legacy JRA's plain-string risks still display correctly; `analysis_detail.html` falls back to
  the raw string via `{{ risk.text|default:risk }}`). No live provider call was made or authorized
  by this decision; JobApplication id=9's real, legacy JRA (v1) was inspected read-only and left
  completely unedited throughout.

## D-023: Course-correcting D-022 -- deterministic code protects integrity, never semantics

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-04) — directed by the product owner
  immediately after reviewing D-022's implementation, stated as a durable boundary: *"LLMs decide
  meaning and wording. Deterministic code protects truth, boundaries and lifecycle. The operator
  approves semantic quality."* This decision does not reverse D-022's diagnosis (JobApplication
  id=9's zero-requirement analysis was a real, correctly-identified failure) or its lifecycle/
  provenance/atomicity machinery (all retained, see below) — it corrects *how far* the deterministic
  layer D-022 introduced was allowed to reach into judging meaning.
- **What D-022 got wrong**: `job_intake/validators/sanity.py` (as originally committed) went
  beyond objective integrity into three kinds of semantic judgment that belong to the AJ LLM and
  the operator, not to deterministic code:
  1. `GAP_LANGUAGE_MARKERS` — a ~26-phrase substring blocklist ("lack of", "no experience", "no
     proven", ...) that rejected requirement/screening-risk text containing those words, regardless
     of whether the wording was a legitimate quotation from the posting itself (a real posting can
     say "No experience necessary" — rejecting that for containing "no experience" is exactly the
     failure mode this decision exists to prevent) or a reasonable recruiter framing the LLM/
     operator should be free to choose and review, not have vetoed by a fixed word list.
  2. `SUBSTANTIVE_TEXT_MIN_CHARS` (300) — a posting-length threshold deciding whether zero
     requirements was suspicious. Length is not meaning, and the threshold was itself an arbitrary
     number standing in for a judgment ("is this posting real") that only reading the posting can
     make.
  3. The "screening risks present, requirements absent" heuristic — an attempt to infer *why* an
     analysis was empty (misclassification) rather than simply that it was empty. Inferring intent
     from a correlation between two counts is exactly the kind of interpretive leap reserved for
     the LLM/operator.
- **Corrected responsibility boundary**: Agent Jobber's LLM remains fully responsible for
  recruiter-style interpretation — responsibilities/qualifications, MANDATORY vs. PREFERRED,
  ATS signals, screening constraints, implied expectations, working in the posting's own language.
  Deterministic code (`job_intake/validators/integrity.py`, renamed from `sanity.py` to make the
  narrower scope legible in the module name itself) checks three objective properties only, none of
  which requires reading for meaning:
  1. **At least one requirement exists** — unconditional, no length threshold. A response with zero
     `requirements` is classified incomplete and never becomes the current usable JRA, regardless
     of how short or long the posting was (a short posting with one explicit requirement must
     reject a zero-requirement response exactly as readily as a long one).
  2. **No exact duplicate requirement** — same category and same normalized (whitespace/case-
     folded) text extracted twice. This is string equality, not interpretation.
  3. **Verifiable provenance** — a MANDATORY/PREFERRED/RESPONSIBILITY requirement's or a screening
     risk's `source_context` must be an exact substring of the posting actually analyzed (mirroring
     `MemoryClaimSupport.verify_against_source`'s established convention). This proves the quoted
     text exists; it never judges what that text *means* or whether building a requirement/risk
     from it was the right call.
  `ATS_SIGNAL`/`IMPLIED_EXPECTATION` remain exempt from the provenance rule, unchanged from D-022 —
  the former is routinely a bare keyword, and the latter is by definition not stated outright
  (its `source_context` is grounding context, never a literal quote, per `schemas.py`'s own
  docstring); holding either to an exact-quote bar would be a category error, not a safety gap.
- **What stayed exactly as D-022 built it** (all objective integrity/lifecycle machinery, none of
  it a semantic judgment): the strengthened AJ prompt's statement that Agent Jobber has no
  Candidate Memory/candidate context (guidance *to the LLM*, which is the correct place for
  semantic guidance to live — see below); Pydantic structured-output validation (`extra="forbid"`,
  enum-bound categories); `ScreeningRisk`'s mandatory `source_context` field; exact-substring
  provenance verification; stable, application-assigned, DB-unique `JobRequirement` IDs; exact
  normalized-duplicate rejection; atomic failure before any artifact persists (`run_intake`/
  `rerun_analysis` raise before `transaction.atomic()` opens — no partial `JobRequirement`, no
  pointer/phase advancement); append-only `JobRequirementAnalysis` versions; the sanitized
  `LLMCallLog` row retained on every failure (token/latency/error metadata only, never raw
  content); the M5 precondition (`build_fit_assessment` refuses a current JRA with zero
  `JobRequirement`s — a fresh runtime check, so it still covers JobApplication id=9's real, legacy,
  untouched JRA); and the read-only "incomplete analysis, not eligible for Gate 1" banner plus its
  server-side enforcement.
- **AJ prompt**: only lightly adjusted (`services/analyze.py::SYSTEM_PROMPT`) — the zero-
  requirements framing was reworded to be unconditional (no "if the posting has real content"
  hedge, matching the code exactly), and a short header note now states explicitly that this
  prompt is the *only* place AJ's semantic judgment is guided, precisely because the deterministic
  layer next to it must never also try to guide it. The candidate-context prohibition and example
  phrasing to avoid remain — they are instructions *to the LLM* about how to write, not rules
  *enforced in code* against what it wrote; D-023 removed the latter, never the former.
- **Human-review UX**: no new gate, no source-unit coverage subsystem (explicitly out of scope, per
  the product owner). The existing `job_intake` analysis-detail page already surfaces every
  requirement with its category and source excerpt, every screening risk with its excerpt, implied
  expectations visibly marked as inferred, and a link to Gate 1 — whose existing AJ-feedback form
  ("Agent Jobber (re-analyze the posting)") is the obvious rerun action. Gate 1 remains the one
  formal, combined AJ/AC operator approval point (HITL-001/002).
- **Alternative considered**: keep the phrase list but make it advisory (a warning shown to the
  operator, not a rejection). Rejected — a hardcoded phrase list is still an attempt to encode
  semantic judgment in deterministic code, just softened; it would still misfire on legitimate
  quoted posting language and would still need updating indefinitely as new phrasing patterns
  appeared. The correct fix is to not attempt this judgment in code at all, not to lower its
  severity.
- **Testing posture (see also `docs/TEST_STRATEGY.md`)**: `test_integrity_validator.py`/
  `test_integrity_validation_intake.py` test only the three objective properties above, including a
  dedicated case proving legitimate posting wording (e.g. "No experience necessary") is never
  rejected merely for its words. No test in this codebase may assert that deterministic code
  correctly judged a semantic classification — that would itself be evidence of the D-022 overreach
  recurring. Recorded/golden ("cassette") LLM-output testing remains explicitly deferred (TEST-002,
  unchanged) until several real AJ/AC/AB outputs have stabilized; this decision does not change that
  timeline or bring it closer.
- **Model-quality posture**: this decision does not assume the `MEMORY_BUILD` model
  (`nvidia/nemotron-3-super-120b-a12b`) is the right model for AJ/AC/AB — it was reused for
  `AC_NORMALIZE`/`AC_RANK`/`AC_MATCH`/`AJ_ANALYZE` (Gate-1 preparation session) only because it was
  already registered, credentialed, and structured-output-capable, as an operational convenience,
  not a quality judgment. Whether it is well-suited to each stage's actual task is something to
  evaluate from real run outcomes (once live runs happen) and the operator's Gate-1 review — never
  assumed from its role in Candidate Memory bootstrap.
- **Consequence**: no model/schema migration (`AgentJobberAnalysis`/`ScreeningRisk` are unchanged
  from D-022 — only the deterministic validator built on top of them was narrowed).
  `job_intake/validators/sanity.py` → `job_intake/validators/integrity.py`;
  `find_sanity_violations` → `find_integrity_violations`; `SemanticValidationError` →
  `AnalysisIntegrityError` (still a subclass of `AnalysisFailedError`, so existing callers are
  unaffected). No live provider call was made or authorized by this decision; JobApplication id=9's
  real, legacy JRA (v1) remains inspected read-only only, never rerun or edited.

## D-024: Stage-specific LLM output-token budgets -- separating model capability from request budget

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-04) — directed by the product owner after a
  live, authorized AJ_ANALYZE rerun of JobApplication 9 (post D-022/D-023 integration) truncated at
  exactly 4,096 output tokens (`finish_reason=length`) and produced no valid `JobRequirementAnalysis`.
  Root cause: `LLMModel.max_output_tokens` was being read by every one of the five stage-caller
  services (`AJ_ANALYZE`, `AC_NORMALIZE`, `AC_RANK`, `AC_MATCH`, `AB_BUILD`) as *both* the model's
  own provider capability ceiling *and* every stage's actual per-request budget — one field serving
  two distinct concerns, with no way to raise AJ_ANALYZE's budget (whose prompt and posting text
  are larger than the other four stages') without also raising every other stage's budget on the
  same model, or vice versa.
- **Requirement**: separate the two concerns without duplicating the resolution logic in each
  service, and without ever changing AJ's semantic prompt, output schema, integrity validator, or
  the human-review boundary (D-023) — this is a token-budget plumbing fix only.
- **Schema**: `StageModelAssignment.max_output_tokens` (new, nullable `PositiveIntegerField`,
  `MinValueValidator(1)`) — an optional per-stage request-budget override. `LLMModel.
  max_output_tokens` is unchanged in meaning (a capability ceiling) but gained the same
  `MinValueValidator(1)` for symmetry. `StageModelAssignment.clean()` rejects (via `ValidationError`,
  raised by `full_clean()` — the same path the admin's `ModelForm` already calls) a configured stage
  budget that exceeds its assigned model's own capability whenever that capability is set; a `None`
  model capability means no declared ceiling to check against (matching this field's existing,
  pre-D-024 optionality).
- **Resolution (the shared provider/registry layer, never duplicated in a pipeline app)**:
  `BaseLLMAdapter.__init__` computes `self.effective_max_output_tokens = llm_model.max_output_tokens
  or DEFAULT_MAX_OUTPUT_TOKENS` (4,096, the one canonical fallback, now defined once in
  `llm_provider.adapters.base` rather than as five duplicated per-service module constants) --
  this is what every adapter has even when constructed directly (e.g. `FakeAdapter(model, ...)` in
  tests, bypassing the registry entirely), so no existing test needed to change. `get_adapter_for_
  stage` (the sole production construction point) then *overrides* `effective_max_output_tokens`
  with the stage's own `StageModelAssignment.max_output_tokens` when one is configured -- after
  re-validating it against the model's capability and raising `InvalidStageBudgetError` (defense in
  depth for a row that reached the database without `full_clean()`, e.g. a fixture or script) if it
  doesn't fit, *before* returning the adapter, i.e. before any provider call could happen. Each of
  the five stage-caller services was changed from manually recomputing `adapter.llm_model.
  max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS` to simply reading `adapter.effective_max_output_
  tokens` -- `job_intake/services/analyze.py` (AJ_ANALYZE) no longer has any hard-coded token
  literal of its own driving its request budget, satisfying the product owner's explicit "not
  hard-code 8192 inside Agent Jobber" instruction.
- **Retry/classification preserved**: `finish_reason=length` truncation is still classified
  `CONFIGURATION` (excluded from `TRANSIENT_ERROR_CATEGORIES`) and therefore still never retried
  identically -- this decision changes *what budget is requested*, never how a truncation response
  is classified. Reasoning-disabled settings and temperature/top-p behavior are untouched (no
  adapter's `_call_once` or request-building logic beyond the `max_output_tokens` value itself was
  modified).
- **Audit metadata**: no `LLMCallLog` field was added to record the per-call requested budget. The
  effective budget for any given historical call is already fully and deterministically derivable
  from the `StageModelAssignment`/`LLMModel` state as of that call (`LLMCallLog.stage` plus
  `created_at` against `StageModelAssignment.updated_at`, in the rare case the assignment changed
  between calls) -- adding a redundant per-row snapshot field was judged unnecessary schema churn
  for a value the registry already reconstructs exactly.
- **Intended configuration (not applied live by this decision -- a separate, explicit follow-up
  action)**: model capability `16,384`; `AJ_ANALYZE` stage budget `8,192`; `AC_NORMALIZE`/`AC_RANK`/
  `AC_MATCH` remain `4,096` (unconfigured stage override, model capability providing the effective
  value); `MEMORY_BUILD` untouched (its own `services/bootstrap.py` per-chunk `max_output_tokens_
  override` mechanism is a separate, pre-existing concern this decision does not alter); `AB_BUILD`
  remains unconfigured (no stage override; falls through to the model capability). Verified valid
  (via `full_clean()`) as a test fixture, but the real development database's `LLMModel`/
  `StageModelAssignment` rows were left exactly as they were (`max_output_tokens=4096` on the model,
  no stage overrides) -- only the schema migration was applied, not this configuration.
- **Consequence**: `llm_provider.0004_stagemodelassignment_max_output_tokens_and_more` (additive:
  a new nullable field plus validator metadata on the existing field -- no data migration, no
  existing row's resolved behavior changes). JobApplication 9's JRA (v1) remains untouched and
  unrerun; no live provider call was made under this decision.

## D-025: Add OpenRouter as a first-class LLM provider

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-04) — implementation-only work package: add
  OpenRouter as an interchangeable `llm_provider` adapter behind the existing normalized request/
  response interface, with initial support for `z-ai/glm-5.2:free`. Explicitly out of scope for
  this decision: assigning OpenRouter to any pipeline stage, changing NVIDIA's existing stage
  assignments, running M5/M6, approving a gate, or making a live inference call — all deferred to
  a separate, later, explicitly-authorized operator action (see "Operator follow-up" below).
- **Requirement**: LLM-001/LLM-007 — a new provider must be addable with zero pipeline-app
  changes, and its structured-output/reasoning quirks must be isolated entirely inside its own
  adapter, matching how `nvidia.py`/`gemini.py` were added under M2.
- **Schema** (`llm_provider.0005_llmprovider_data_collection_policy_and_more`, additive only):
  `LLMProvider.ProviderType` gained `OPENROUTER`; `LLMProvider` gained `data_collection_policy`
  (a constrained `TextChoices` field, `DENY`/`ALLOW`, default `DENY` — never arbitrary JSON) that
  the OpenRouter adapter alone reads to build its request-level privacy routing directive. No
  other model changed; no data migration; every pre-existing `LLMProvider` row keeps its previous
  behavior (`data_collection_policy` defaults to `DENY`, which no other adapter ever reads).
- **Adapter** (`llm_provider/adapters/openrouter.py`, registered in `ADAPTER_CLASSES`): reuses
  `openai.py`'s `build_chat_completion_body` (messages/temperature/`max_tokens`/`response_format`)
  and `parse_openai_style_chat_completion` (response envelope, usage extraction, status-code
  classification) wherever their OpenAI-compatible semantics genuinely match, adding only what is
  genuinely OpenRouter-specific: `stream: false`, optional `top_p`, the unified `reasoning`
  parameter, the `provider` routing object, and the two optional attribution headers. No second
  hand-maintained schema translator was written — `to_openai_strict_schema` (already used by NIM)
  is reused as-is.
  - **Credential**: resolved only from `LLMProvider.credential_env_var` (same mechanism every
    other adapter uses); never displayed, persisted, hashed, or logged. A missing credential
    returns a `CONFIGURATION` error before any HTTP call, proven by a dedicated test with
    `requests.post` mocked and asserted never called.
  - **Endpoint/headers**: `POST {base_url or https://openrouter.ai/api/v1}/chat/completions` with
    `Authorization: Bearer <key>` and `Content-Type: application/json`. `HTTP-Referer`/
    `X-OpenRouter-Title` are added only when `OPENROUTER_HTTP_REFERER`/`OPENROUTER_APP_TITLE` are
    non-empty in the environment — fixed, documented header/env-var names (not
    operator-configurable per provider row, since OpenRouter defines exactly these two), never a
    placeholder value invented when absent.
  - **Capability guards** (mirroring NIM's existing `supports_structured_output` pattern, since
    OpenRouter also routes to many underlying endpoints with uneven feature support): a model not
    marked `supports_structured_output` is rejected before any HTTP call; a request with
    `reasoning_enabled=True` against a model not marked `supports_reasoning` is rejected the same
    way — the first use of that capability flag as an enforced precondition rather than metadata
    only.
  - **Privacy routing**: every OpenRouter request carries
    `provider: {require_parameters: true, data_collection: "<deny|allow>"}` — `require_parameters`
    so OpenRouter only routes to an endpoint that actually supports the structured-output/
    reasoning parameters requested (never a silent downgrade), `data_collection` read from
    `LLMProvider.data_collection_policy` and lower-cased for the wire format. If that field ever
    holds a value outside `{DENY, ALLOW}` (only possible for a row that bypassed `full_clean()` —
    the same defense-in-depth posture `get_adapter_for_stage`'s stage-budget check already
    established for D-024), the adapter fails closed with `CONFIGURATION` and makes no HTTP call,
    rather than ever sending an unvalidated value or silently weakening the policy. ZDR
    (zero-data-retention) is deliberately **not** added as a default or a field in this decision —
    current OpenRouter endpoint metadata for the free tier was not verified to require or support
    it; it remains an explicit, optional, future configuration if a later operator verification
    step confirms it applies.
  - **Reasoning**: OpenRouter's unified `{"reasoning": {"enabled": true}}` is sent only when
    `request.reasoning_enabled` is explicitly `True` — never for `False`/`None`, and never as a
    blanket per-adapter default (the same opt-in, request-driven pattern `nvidia.py` established
    for `chat_template_kwargs.enable_thinking`, translated to OpenRouter's own parameter shape).
    Reasoning tokens are accepted as ordinary completion/output usage, exactly as OpenRouter
    reports them — no separate reasoning-token field was added. The final answer is parsed only
    from `choices[0].message.content`; `reasoning`/`reasoning_content`/`reasoning_details` are
    never read as a substitute, proven by a dedicated test asserting an error (not a fabricated
    result) when `content` is missing even though a reasoning field is present alongside it. No
    reasoning/chain-of-thought text is ever persisted to `LLMCallLog` or exposed in admin —
    structurally guaranteed, since `LLMCallLog` has no content field of any kind for any provider,
    proven here by a test that greps every `LLMCallLog` field for reasoning text after a call whose
    mocked response included some.
  - **Multi-turn `reasoning_details` continuation**: intentionally out of scope. Every pipeline
    stage this codebase has (`MEMORY_BUILD`/`AJ_ANALYZE`/`AC_NORMALIZE`/`AC_RANK`/`AC_MATCH`/
    `AB_BUILD`) is single-shot — one request, one response, no follow-up call in the same
    conversation — so there is nothing to carry `reasoning_details` between. If a future
    multi-turn or tool-calling workflow is ever built, it must preserve `reasoning_details`
    unmodified in memory and pass it back verbatim on the next call (OpenRouter's own documented
    contract) — noted here for that future workflow's benefit, not implemented now, and no
    conversation-state subsystem was built to anticipate it.
  - **Error classification**: `parse_openai_style_chat_completion` (shared by OpenAI/NIM/
    OpenRouter) gained two status branches applicable to all three, since HTTP semantics here are
    provider-neutral, not OpenRouter-specific: `402` → `CONFIGURATION` (quota/billing/account
    restriction, non-transient) and `408` → `TIMEOUT` (transient, eligible for the existing
    bounded retry policy). `400`/`413`/`422` already fell through to the existing generic
    `SCHEMA_VALIDATION` branch, which was already non-transient — satisfying "invalid request/
    schema/configuration; non-transient" without a new branch. `404`/`410` already classified
    `CONFIGURATION`; `429` already `RATE_LIMIT`; `500`/`502`/`503`/`524`/`529` already fall through
    to the existing `>= 500` → `PROVIDER_INTERNAL` branch, all already transient/retryable. No
    provider-specific JSON body parsing/repair was added inside the adapter; sanitization (never
    logging a raw request/response body) was already structural in `sanitize_error_message`/the
    existing status-branch messages, which never echo response content.
  - **Retry**: the existing shared `BaseLLMAdapter.generate()`/`execute_with_retry` path is reused
    unmodified — bounded attempts (`RetryPolicy.max_attempts`, default 3), retried only for
    `RATE_LIMIT`/`TIMEOUT`/`PROVIDER_INTERNAL`, never for `AUTH`/`CONFIGURATION`/
    `SCHEMA_VALIDATION`. `Retry-After` header honoring was **not** added — the existing retry
    abstraction has no mechanism to read response headers at all (linear backoff only), so there
    was nothing to wire this into without a broader retry-policy change out of scope here; flagged
    as a possible future enhancement, not implemented. Free-model unavailability/rate-limiting
    never triggers model switching or a paid fallback — the adapter always sends
    `self.llm_model.model_id` verbatim, proven by a test asserting the sent model slug is
    identical across a failed-then-retried request pair.
  - **Model exactness**: the adapter never hardcodes or substitutes a model id — whatever
    `LLMModel.model_id` the registry resolves to is sent verbatim, so there is no code path that
    could silently fall back from `z-ai/glm-5.2:free` to a paid model or `openrouter/free`.
- **Capability discovery**: no live OpenRouter model-metadata lookup was implemented — the
  architecture has no existing provider model-discovery mechanism to extend (`career-intelligence`
  is explicitly not a dependency, and no prior provider added one), so building a new one for this
  implementation-only task would be scope creep. The exact operator verification step is
  documented instead (see "Operator follow-up" below and `docs/CURRENT_STATE.md`).
- **Admin**: `LLMProviderAdmin.list_display` gained `data_collection_policy`; no other admin
  change was needed — `LLMModel`'s existing `supports_structured_output`/`supports_reasoning`/
  `max_output_tokens` fields and `StageModelAssignment`'s existing `full_clean()`-validated
  admin form already cover OpenRouter with zero additional code, exactly as they do for every
  other provider type.
- **Smoke test**: `llm_provider/smoke/run_openrouter.py` + `manage.py smoke_test_openrouter`
  (`--model`, `--reasoning`) follow the existing opt-in, never-automatic pattern exactly
  (`CREDENTIAL_ENV_VARS[OPENROUTER] = "OPENROUTER_API_KEY"` added to the shared selection harness);
  `run_smoke_test`/`select_default_model` needed no OpenRouter-specific branch since both were
  already generic over `provider_type`. `run_smoke_test` gained an optional `reasoning_enabled`
  parameter (defaults to `None`, i.e. no behavior change for OpenAI/NVIDIA/Gemini's existing smoke
  commands) so a later operator can smoke-verify a reasoning-enabled request without hand-editing
  the harness. **Not run in this session** — no credential was available or used, and none of
  `smoke_test_openai`/`smoke_test_nvidia`/`smoke_test_gemini`'s existing behavior changed.
- **Tests**: `llm_provider/tests/test_openrouter_adapter.py` (46 new deterministic tests, all
  `requests.post` mocked, covering registry selection, endpoint/auth, missing-credential
  short-circuit, attribution headers, exact-model-slug/no-fallback, request-body construction
  (messages/temperature/`max_tokens`/`stream`/`top_p`), structured-output request/capability
  guard, the `provider` routing object (`require_parameters`, default-`deny` and explicit-`allow`
  `data_collection`, fail-closed on an invalid stored value), reasoning enable/omit/reject,
  reasoning-fields-never-substitute-for-content, no-raw-reasoning-in-`LLMCallLog`, successful
  response normalization/usage extraction, empty/missing/malformed content, status-code
  classification (`402`/`408`/`429`/`524`/`529`/`400`/`413`/`422`/`404`/`410`), truncation
  non-retryability, bounded-retry success/exhaustion, no-retry-on-non-transient-categories,
  no-model-fallback-across-retries, stage-specific effective-budget propagation into the actual
  request body, and the network guard blocking a real call). One pre-existing test
  (`test_registry.py::AdminRegistryReachabilityTests::test_provider_and_model_creatable_through_
  admin`) needed its POST payload updated to include the new required `data_collection_policy`
  field — the same kind of explicit-field update that test already does for `base_url`, not a
  weakened assertion. Full suite: 875/875 passing (up from 829 before this change), zero live
  credentials, `NetworkGuardedTestRunner` active throughout. `manage.py check`,
  `makemigrations --check --dry-run` (no changes detected), and `ruff check .` all pass. The new
  migration was additionally verified applying cleanly from zero on a genuinely fresh, isolated
  PostgreSQL container (a throwaway `postgres:16-alpine` instance on a non-default port, separate
  from the real local dev database, removed immediately after verification).
- **Operator follow-up (not performed in this session — a separate, later, explicitly-authorized
  action)**:
  1. Verify `z-ai/glm-5.2:free`'s current context length, max-completion-tokens, structured-output
     support, and reasoning support directly against OpenRouter's model listing/docs (there is no
     in-repo discovery command to do this automatically — see above) before trusting any capability
     flag beyond what this decision registers provisionally in code comments/tests.
  2. Create the real `LLMProvider` row (`provider_type=OPENROUTER`,
     `credential_env_var=OPENROUTER_API_KEY`) and `LLMModel` row (`model_id=z-ai/glm-5.2:free`,
     `supports_structured_output=True`, `supports_reasoning=True`, `max_output_tokens` set only
     after step 1 confirms a real value) through the admin — never created live by this decision.
  3. Populate `OPENROUTER_API_KEY` (and optionally `OPENROUTER_HTTP_REFERER`/
     `OPENROUTER_APP_TITLE`) in `.env` — never committed.
  4. Run `python manage.py smoke_test_openrouter` (add `--reasoning` once step 1 confirms
     reasoning support) as a separate, explicitly-authorized action; review its output, including
     whether the free endpoint actually satisfies `require_parameters=true` for the features
     requested (a `require_parameters` rejection is expected, visible, non-transient behavior if
     no compliant free endpoint exists for a given request shape — never a signal to silently
     relax `data_collection` or fall back to a different/paid model).
  5. Only after that smoke test is reviewed and approved, assign OpenRouter to any
     `StageModelAssignment` — never implied or performed by this decision.
- **Consequence**: `llm_provider.0005_llmprovider_data_collection_policy_and_more` (additive: one
  new provider-type choice, one new field with a safe default — no existing row's resolved
  behavior changes). No `LLMProvider`/`LLMModel`/`StageModelAssignment` row was created or changed
  in the real development database; no live provider call was made; existing NVIDIA stage
  assignments and any concurrently in-progress M5 work in the primary checkout were untouched
  (this work was isolated in worktree `openrouter-provider` / branch
  `worktree-openrouter-provider`).

## D-026: Shared parser must never infer a missing final answer from reasoning content -- fix the real OpenRouter null-content incident and separate smoke budgets

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-04) -- directed by the product owner
  immediately after D-025 was merged to `main` (HEAD `a541a0a`) and its one authorized OpenRouter
  reasoning-enabled smoke test was run for real against the newly created `LLMProvider` (id 9,
  `OpenRouter`) / `LLMModel` (id 10, `z-ai/glm-5.2:free`) registry rows. That call reached
  OpenRouter, got a real HTTP 200 response shaped like `{"choices": [{"finish_reason": "length",
  "message": {"content": null, "reasoning_details": []}}], "usage": {...}}` -- the model spent its
  entire 64-token smoke budget on internal reasoning and returned no final answer -- and crashed
  with an uncaught `TypeError: the JSON object must be str, bytes or bytearray, not NoneType` from
  `json.loads(None)` inside `parse_openai_style_chat_completion`
  (`llm_provider/adapters/openai.py`, shared by OpenAI/NVIDIA NIM/OpenRouter). The exception
  propagated out of `BaseLLMAdapter.generate()` before reaching `_write_call_log()`, so the call
  left **no `LLMCallLog` row at all** despite genuinely spending real tokens -- a real audit-trail
  gap, not merely an unhandled edge case. The existing code already anticipated a reasoning model
  exhausting its budget (see the `finish_reason == "length"` -> `CONFIGURATION` branch and its
  comment), but the `except (KeyError, IndexError, json.JSONDecodeError)` clause it lived in never
  caught the `TypeError` that a literal `None` (as opposed to a missing key or an empty/malformed
  string) produces.
- **Root cause, precisely**: two independent gaps, both provider-boundary/transport concerns, never
  semantic ones. (1) The parser's content-extraction path assumed `message.content`, when present
  at all, would always be either a valid JSON string or absent/malformed in a way that raised
  `KeyError`/`IndexError`/`json.JSONDecodeError` -- it never considered `None`, a non-string type,
  or a present-but-empty/whitespace-only string, all of which a real provider can return. (2) The
  smoke harness's fixed 64-token output budget (fine for a minimal non-reasoning acknowledgement)
  was reused unchanged for a `--reasoning`-enabled request, even though reasoning tokens consume
  the same completion-token allowance as the final answer -- so a reasoning model has no room left
  to write one.
- **Boundary preserved, not weakened**: "Provider adapters normalize transport behavior. They do
  not infer missing final answers from reasoning content." Fixing this bug never means falling
  back to `reasoning`/`reasoning_content`/`reasoning_details` as a substitute final answer when
  `message.content` is unusable -- that would be exactly the kind of semantic inference this
  project's provider-adapter layer must never perform (adapters normalize *transport* shape; they
  never decide what a response *means*). The fix is a strictly wider, more precise *classification*
  of "no usable final content", not a new source of content.
- **Parser fix** (`llm_provider/adapters/openai.py::parse_openai_style_chat_completion`, shared by
  `OpenAIAdapter`/`NvidiaNimAdapter`/`OpenRouterAdapter`): `finish_reason` and `message.content` are
  now extracted defensively (`isinstance` checks throughout, never assumed to be a particular
  shape) before any JSON parsing is attempted. Final content is accepted only when it is a
  non-empty, non-whitespace-only `str` -- never `reasoning`/`reasoning_content`/`reasoning_details`
  or any other field. When content is absent, `None`, non-string, empty, or whitespace-only:
  `finish_reason == "length"` classifies as `CONFIGURATION` (a token-budget problem, not
  transient, never retried -- unchanged from the behavior D-024's comment already documented for
  the empty-string case); any other (or missing) `finish_reason` classifies as `SCHEMA_VALIDATION`
  (the same non-transient, never-retried bucket already used for a malformed response, since a
  present-but-empty/null/wrong-typed `content` field with a normal `finish_reason` is itself a
  malformed response). A content string that *is* present and non-empty but fails `json.loads`
  always stays `SCHEMA_VALIDATION`, regardless of `finish_reason` -- this narrows one pre-existing
  behavior (previously, any `JSONDecodeError` combined with `finish_reason == "length"` was also
  reclassified as `CONFIGURATION`, even for non-empty garbage content); no existing test asserted
  on that category, so this is a deliberate precision improvement, not a break. Usage and
  `finish_reason` (folded into the sanitized `error.message` text, the same mechanism already used
  for the length-truncation case -- no new `LLMCallLog` column) are preserved on every failure path
  exactly as on success. Because every path now returns a `NormalizedLLMResult` instead of raising,
  `_write_call_log()` is reached unconditionally -- restoring the audit trail for exactly the call
  shape that previously vanished. No raw response body, prompt, or reasoning content is ever
  included in a returned error message or logged field, matching every other classification branch
  already in this function.
- **Amendment (2026-09-04, same-day correction, commit on top of `002ff833`)**: the paragraph above
  describes this decision's *first* implementation pass, which got one case wrong and is preserved
  here rather than rewritten, per this project's standing rule against silently rewriting decision
  history. That first pass let a `finish_reason == "length"` response through to the ordinary
  `json.loads` success path whenever its content was a present, non-empty string -- including when
  that string happened to still parse as valid JSON. That is exactly the "accept truncated content
  because it happens to parse" bug this decision's own required invariant forbids: `length` means
  the provider stopped because it hit the output-token limit, not because it finished, so content
  present under a `length` finish reason may be an incomplete answer and must never be accepted as
  a genuine one merely because it parses. The corrected rule, now in effect: `finish_reason ==
  "length"` is checked **first**, before any content extraction or JSON parsing is attempted, and
  classifies as `CONFIGURATION` unconditionally -- regardless of whether `message.content` is
  missing, `None`, non-string, empty, whitespace-only, malformed JSON, or valid JSON. Every other
  finish reason keeps the behavior described above (missing/invalid content -> `SCHEMA_VALIDATION`;
  malformed JSON -> `SCHEMA_VALIDATION`; valid JSON -> success, unchanged). `llm_provider/tests/
  test_null_content_handling.py` was updated to match: the now-incorrect test asserting
  `SCHEMA_VALIDATION` for malformed-content-with-`length` was replaced by tests asserting
  `CONFIGURATION` for both malformed-and-length and (critically, the case that was actually wrong)
  valid-JSON-and-length, at both the shared-parser and representative-adapter levels, with an
  explicit amendment note in that file's own docstring.
- **Smoke-budget fix** (`llm_provider/smoke/common.py`): a new `resolve_smoke_max_output_tokens`
  resolves the smoke request's output-token budget -- 64 by default (unchanged for a plain
  request), 4,096 when `--reasoning` is set (`DEFAULT_REASONING_SMOKE_MAX_OUTPUT_TOKENS`), an
  explicit `--max-output-tokens` value always overriding either default -- and validates the
  resolved value (positive integer; must not exceed a fixed smoke-only safety ceiling of 8,192,
  `SMOKE_MAX_OUTPUT_TOKENS_CEILING`; must not exceed the selected model's own `max_output_tokens`
  capability when known) *before* any adapter is constructed or HTTP call made, raising
  `InvalidSmokeOutputBudgetError` (caught locally, printed, no provider call) rather than sending
  an invalid value and hoping. `smoke_test_openrouter` gained `--max-output-tokens`
  (`smoke_test_openai`/`smoke_test_nvidia`/`smoke_test_gemini` were left unchanged -- none of them
  support `--reasoning` in the first place, so this budget concern doesn't yet apply to them).
  This never touches `LLMModel.max_output_tokens`, `StageModelAssignment.max_output_tokens`, any
  existing stage's effective budget, or application-stage request construction -- it is a
  smoke-harness-only concern, exactly like the pre-existing `--model`/`--reasoning` flags.
- **Schema**: none. No field was added anywhere (finish_reason continues to flow through the
  existing sanitized `error.message` text, matching the pre-existing pattern for the
  `finish_reason=length` case; the smoke budget is a request-construction value, never a stored
  registry field) -- `manage.py makemigrations --check --dry-run` confirmed no migration is
  required, and none was created.
- **Tests**: `llm_provider/tests/test_null_content_handling.py` (new -- reproduces the exact real
  incident payload and its content-shape variants directly against the shared parser, then proves
  the same behavior end-to-end through representative `NvidiaNimAdapter`/`OpenAIAdapter`/
  `OpenRouterAdapter` calls: exactly one sanitized `LLMCallLog` row per logical call, zero retries,
  usage/finish-reason preserved, and planted prompt/reasoning/credential marker strings proven
  absent from every logged field) and `llm_provider/tests/test_smoke_output_budget.py` (new --
  `resolve_smoke_max_output_tokens`'s validation rules directly, plus `run_smoke_test` integration
  proving the resolved value reaches the real request body and an invalid value never reaches
  `requests.post`, and that no `LLMModel`/`StageModelAssignment` row is mutated). One new test
  (`test_openrouter_adapter_generate_never_reaches_the_network`) was added to the existing
  `test_network_guard.py`, closing a gap where OpenAI/NVIDIA/Gemini were already covered but
  OpenRouter was not. Full suite after the initial pass: 927/927 passing (up from 875); after the
  same-day amendment above (which added the valid-JSON-and-`length` coverage at both the
  shared-parser and adapter levels and corrected the now-invalid malformed-and-`length`
  assertion): **935/935 passing**. `manage.py check`/`makemigrations --check --dry-run` (no
  changes detected)/`ruff check .`/`git diff --check` all clean at every step. All migrations
  (unchanged in count/content by this decision, both before and after the amendment) were
  additionally re-verified applying cleanly from zero on a fresh, isolated, throwaway
  `postgres:16-alpine` Docker container on a non-default port, removed immediately after
  verification. This work was isolated in worktree `openrouter-null-content-fix` / branch
  `worktree-openrouter-null-content-fix`; the amendment is a separate commit on top of the
  initial `002ff833`, never an amended/rewritten commit.
- **Not done in this session (out of scope, per explicit instruction)**: no live provider call was
  made; no `StageModelAssignment` was created, changed, or pointed at OpenRouter; no M5/M6 process
  ran; no Gate was approved; JobApplication 9 and its JRA (id 10, v2) were untouched. A separately
  authorized re-run of `python manage.py smoke_test_openrouter --reasoning --max-output-tokens
  4096` against the real id-9/id-10 registry rows is the intended next step to confirm the fix
  against a genuine reasoning-enabled response, but was not performed here.
- **Consequence**: no migration. `llm_provider/adapters/openai.py`, `llm_provider/smoke/common.py`,
  `llm_provider/smoke/run_openrouter.py`, and `llm_provider/management/commands/
  smoke_test_openrouter.py` changed; two new test modules and one addition to an existing one.
  Every OpenAI-compatible adapter (OpenAI, NVIDIA NIM, OpenRouter) now classifies a missing/
  invalid final answer the same way and always produces an audit log row for it; a future
  reasoning-enabled OpenRouter smoke qualification has a budget mechanism that won't reproduce the
  same starved-budget failure mode.

## D-027: AC_NORMALIZE per-term length limit must be provider-visible, not Python-only

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-05) -- directed by the product owner after a
  read-only audit of a real M5 failure against `JobApplication` 9 / `JobRequirementAnalysis` 10
  (v2, 30 requirements). The controlled run's AC_NORMALIZE call (`LLMCallLog` id 309) returned 15
  schema-validation errors, all `items[N].equivalents` entries longer than `MAX_TERM_CHARS` (60) --
  e.g. `'build generative AI workflow orchestrations using AWS AI services'` (67 characters), a
  full action clause rather than a short synonym/term.
- **Audit finding**: `MAX_TERM_CHARS` (`candidate_matching/services/normalization_limits.py`) was a
  real, correctly-enforced, fail-closed limit -- but it reached the model through no channel at
  all. It was checked only by a Python-only `@field_validator` on
  `schemas.RequirementNormalizationItem` (`_bound_each_term_length`), which has no corresponding
  Pydantic `Field` constraint on the list item type, so it produced no `maxLength` in
  `RequirementNormalizationOutput.model_json_schema()` -- confirmed by generating the schema
  directly. `llm_provider.schema_translation.to_openai_strict_schema()` only adds
  `additionalProperties: false`; it does not add or remove length constraints, so nothing was lost
  in translation -- there was simply nothing to translate. The AC_NORMALIZE `SYSTEM_PROMPT`
  (`candidate_matching/services/normalize.py`) said only "Every list is short and bounded -- do not
  enumerate exhaustively," with no character number and no instruction against full sentences,
  action clauses, or requirement restatements. The model was validated against a rule it had no
  way to know existed, in either machine-readable (schema) or natural-language (prompt) form.
- **This is a contract-alignment defect, not deterministic overreach**: the audit found no evidence
  that 60 characters is itself unreasonable -- `candidate_generation.py`/`lexical_relevance.py`
  treat the whole `build_search_text` union as an unordered, rarity-weighted token bag (never
  exact-phrase matching), so a longer term does not distort scoring more than a short one; common
  connecting words are already dampened regardless of source field. The representative rejected
  value reads as a misplaced restatement (arguably canonical-text material), not proof that a
  faithful short synonym routinely needs 61+ characters. The fix below therefore only exposes the
  existing, unchanged limit -- it does not raise it, lower it, or add a different limit for any of
  the three bounded term-list fields.
- **Fix (structural, not semantic)**: `candidate_matching/schemas.py` now defines `BoundedTerm =
  Annotated[str, StringConstraints(max_length=MAX_TERM_CHARS)]` and uses it as the item type for
  `diagnostic_terms`, `equivalents`, and `preserved_technical_terms` (replacing the Python-only
  `@field_validator`, which is now fully redundant and removed rather than left as inconsistent
  duplicate validation). `MAX_TERM_CHARS` remains the single source of truth -- the literal `60` is
  never duplicated. This produces `{"type": "string", "maxLength": 60}` on every one of those
  fields' items in `model_json_schema()`, and therefore in `to_openai_strict_schema()`'s output and
  the final `response_format.json_schema.schema` every OpenAI-compatible adapter (OpenAI, NVIDIA
  NIM, OpenRouter) sends -- verified by building the real AC_NORMALIZE request locally (invented,
  non-personal requirement text) through the production request builder and the exact
  strict-schema/request-body-construction path those adapters use, with no network call made.
  `normalize.py`'s `SYSTEM_PROMPT` gained one sentence, built from `MAX_TERM_CHARS` (never a second
  hardcoded `60`): each entry in the three bounded lists "must be a short term or short phrase of
  at most {MAX_TERM_CHARS} characters -- never a complete sentence, an action clause, or a
  restatement of the full requirement." Boundary behavior (exactly 60 accepted, 61 rejected; list
  counts unchanged) is identical before and after -- confirmed by test.
- **Boundary preserved, not weakened**: this is exposure of an existing structural bound, never a
  deterministic semantic judgment. No term is truncated, dropped, or rewritten after generation; no
  keyword heuristic was added; `extra="forbid"`, canonical-text length enforcement, requirement-ID
  set-integrity checking, and fail-closed `SCHEMA_VALIDATION` behavior on any violation are all
  unchanged. `AgentCandidateAssessment`/`candidate_generation.py`/`lexical_relevance.py` and every
  request/token/retrieval bound (`retrieval_limits.py`) are untouched. `RequirementNormalizationItem`
  still has no field that could carry a claim id or evidence -- normalization output remains
  non-citable search assistance only.
- **Deferred, not attempted here**: raising or lowering `MAX_TERM_CHARS`, adding a bounded
  LLM repair/regeneration call on schema failure, and making `SCHEMA_VALIDATION` retryable are all
  explicitly out of scope for this correction. If AC_NORMALIZE still produces oversized terms after
  the model can actually see the rule in both the schema and the prompt, that is new evidence for a
  separate decision -- not something to pre-emptively design for here.
- **Consequence**: no migration (`normalization_limits.py`'s constants are unchanged in value).
  `candidate_matching/schemas.py` and `candidate_matching/services/normalize.py` changed; focused
  tests added to `candidate_matching/tests/test_normalize.py` covering the exact-boundary behavior
  for all three term-list fields, the generated Pydantic schema, the strict-schema conversion, the
  final provider-facing request body, and the prompt's stated rule. No live provider call was made;
  no `StageModelAssignment`/budget/provider row changed (AC_RANK remains at its separately
  authorized 16384; AC_NORMALIZE/AC_MATCH remain at 8192); no M5/M6 process ran; no Gate was
  approved; `JobApplication` 9, `JobRequirementAnalysis` 10, and `CandidateMemory` 7 were untouched.
  This work was isolated in worktree `ac-normalize-contract-alignment` / branch
  `worktree-ac-normalize-contract-alignment`, left uncommitted to `main` for independent re-audit.

## D-028: Provider/model fallback is an operator-authorized rerun, never an automatic or invisible switch

- **Status**: **APPROVED** (2026-09-05) -- an explicit Product Owner directive, recorded verbatim
  here before any implementation is attempted, per the standing rule that a decision only becomes
  actionable once the product owner sets it to `APPROVED` in a session. No code changes accompany
  this decision; it is a policy record governing how a future rerun/fallback capability must behave
  once built, and how an operator may already reason about a failed stage today using only actions
  that already exist (a plain rerun of the same stage through the existing registry).
- **Context**: D-027 (immediately above) independently qualified both NVIDIA and OpenRouter
  (`z-ai/glm-5.2:free`) for AC_NORMALIZE against the real `JobApplication` 9 / `JobRequirementAnalysis`
  10 (v2, 30-requirement) workload -- both succeeded, 30/30 requirement IDs, no length violations
  (`LLMCallLog` ids 310/311). This raised an operational question the codebase does not yet answer
  procedurally: when a stage fails, who decides whether to rerun it against a different qualified
  provider/model, and under what constraints. This decision answers that question as policy, ahead
  of any M5 run that might need it.
- **The decision** (Product Owner's own words, preserved verbatim as the operative text):

  > A failed LLM stage may be rerun using a different qualified provider/model. A different
  > registered model within the same provider, including OpenRouter, may also be selected.
  > Provider/model switching must be explicit, stage-specific, visible to the operator, and
  > audit-logged. It must never silently select a different model, incur paid usage, weaken privacy
  > routing, or bypass capability qualification. The future UI should let the operator inspect and
  > select the provider/model for each LLM call and explicitly authorize a fallback or rerun.
  > Completing the current M5/M6 workflow takes priority; implementing the full UI control and
  > automated orchestration is deferred.

  "Fallback" is interpreted as **an operator-authorized new invocation** -- a fresh, explicit call
  the operator chooses to make after a classified failure -- **never an invisible provider/model
  switch inside an already-running logical call**. A logical invocation (one `adapter.generate()`
  call and its automatic transient retries, per `retry.py`/D-008/LLM-008) keeps one fixed model
  slug for its entire duration; nothing in this decision authorizes changing that mid-flight.
- **Binding constraints this decision imposes on any future fallback/rerun capability**:
  1. Fallback is an operator-authorized rerun after a classified stage failure -- never automatic,
     never triggered by the pipeline itself deciding a provider "isn't working."
  2. A rerun may select another qualified provider, or another qualified registered model within
     the *same* provider (e.g. a different OpenRouter-routed model) -- both are equally valid
     fallback targets, never privileging one provider type over another as a matter of policy.
  3. Every fallback candidate must already satisfy, before it is offered as an option: credential
     configuration (`LLMProvider.credential_env_var` resolves to a present environment value),
     model capability (`LLMModel.max_output_tokens` covers the stage's required budget),
     structured-output support when the stage requires it, reasoning support when the stage would
     request it, a valid privacy/data-collection policy, a passed smoke qualification, and, where
     applicable (as for AC_NORMALIZE, D-027's own qualification runs), a passed stage-specific
     workload qualification. A candidate that has not cleared all of these is not "qualified" and
     must not be offered, regardless of how the operator's selection UI is eventually built.
  4. The exact model slug is fixed for one logical invocation and its automatic retries --
     reaffirming `ARCHITECTURE.md` §3.3's existing OpenRouter-adapter invariant ("no fallback to a
     paid or different model exists anywhere in the adapter") at the pipeline-orchestration level
     too: nothing may swap providers/models between retries of the same logical call.
  5. No paid-model fallback without explicit operator authorization -- an operator selecting a
     fallback target must be able to see that it is a paid model before authorizing the rerun; nothing
     may silently route a failure to a paid alternative.
  6. Privacy settings (`data_collection=deny`, `require_parameters=true` for OpenRouter, and the
     equivalent posture for any other provider) can never be weakened as a side effect of a
     fallback decision -- a fallback target inherits its own registry row's privacy policy exactly
     as configured, never a loosened one chosen to make the fallback succeed.
  7. Every invocation -- primary or fallback -- retains its own exact, distinct provider/model
     identity in its own `LLMCallLog` row (already true today via `BaseLLMAdapter._write_call_log`,
     LLM-010); a fallback rerun is a second, separately logged call, never a correction merged into
     the first row.
  8. A schema/configuration failure (`SCHEMA_VALIDATION`, `CONFIGURATION`) must never be
     mischaracterized as transient to justify an automatic retry or fallback -- `retry.py`'s
     existing `TRANSIENT_ERROR_CATEGORIES` boundary already enforces this for automatic retries,
     and this decision extends the same boundary to any future fallback UI: it must classify a
     failure honestly before offering fallback as a remedy, never blur the two so a real contract
     defect gets silently retried into passing.
  9. **What exists today remains manual and operator-controlled**: no automatic cross-provider
     fallback mechanism is being added now. An operator today can already rerun a failed stage by
     re-invoking the relevant service entry point (e.g. `expand_requirements_for_search`,
     `build_fit_assessment`) against whichever `StageModelAssignment` is currently configured, or by
     an authorized, explicit, in-memory adapter substitution for one investigative call (the
     technique both this decision and D-027's qualification runs used) -- never a standing
     mechanism the pipeline invokes on its own.
  10. **Future UI work** (deferred, not started) should expose, before each LLM call: the stage;
      the primary provider/model; the available *qualified* alternatives (per constraint 3 above);
      the output budget; the reasoning state; the privacy policy; an estimated call count/cost
      where available; and an explicit Execute/Rerun/Fallback authorization control -- never a
      one-click "just retry with anything."
  11. **Priority**: completing the current M5/M6 workflow takes priority over building this UI: the
      full provider/model-selection control and any automated orchestration around it is a
      deferred, traceable follow-up, not a prerequisite for the M5 run this decision was recorded
      ahead of.
- **Non-goal reaffirmed**: this decision does not authorize implementing the future UI, does not
  authorize assigning OpenRouter (or any alternative model) to any stage now, and does not authorize
  an automatic fallback mechanism of any kind in this session. It is a policy record only.
- **Consequence**: no code change, no migration, no `StageModelAssignment` change. Documentation-only
  commit to `docs/DECISIONS.md` (this entry) and `docs/CURRENT_STATE.md` (a corresponding summary
  update and a correction of D-027's now-stale "not yet merged to main and not yet re-verified"
  status line, since D-027 has since been independently audited, fast-forward merged to `main`, and
  qualified live against both NVIDIA and OpenRouter -- a factual-status correction, not a rewrite of
  D-027's own historical rationale, which is preserved unchanged above).

## D-029: Configurable per-stage HTTP read timeout, split from a fixed connect timeout

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-05) -- directed by the product owner as a
  read-only-audit-then-implement work package. No live provider call, M5/M6 run, or provider
  smoke test was made or authorized as part of this decision; no `StageModelAssignment`
  provider/model assignment and no operational timeout *value* was changed in the persistent
  development database -- only the mechanism and its inert-by-default schema exist now.
- **Context (read-only audit finding)**: every real adapter (`OpenAIAdapter`, `NvidiaNimAdapter`,
  `OpenRouterAdapter`, `GeminiAdapter`) passed a literal `timeout=60` to `requests.post()` --
  independently hardcoded in each of the four files, not a shared constant. Per `requests`'
  semantics, a single scalar `timeout=` value is a *combined* connect+read budget: a hung TCP/TLS
  handshake and a slow-but-connected streaming response were indistinguishable and shared the same
  60-second ceiling. All four providers shared the identical value; there was no way to give one
  stage (e.g. one with a larger prompt or reasoning enabled) more time without changing every other
  stage on the same provider. Retry interaction: `RetryPolicy(max_attempts=3, backoff_seconds=1.0)`
  retries a `TIMEOUT` classification with linear backoff (`sleep(backoff_seconds * attempt)`), so the
  pre-existing worst-case wall-clock wait for a call that timed out on every attempt was
  `3 * 60 + (1 + 2) = 183` seconds.
- **Design**: `StageModelAssignment.read_timeout_seconds` (new, nullable `PositiveIntegerField`,
  `MinValueValidator(1)`/`MaxValueValidator(300)`) -- an optional per-stage *read*-timeout override,
  resolved through the exact same path as D-024's `max_output_tokens` budget:
  `BaseLLMAdapter.__init__` sets `self.effective_read_timeout_seconds =
  DEFAULT_READ_TIMEOUT_SECONDS` (60 -- the exact prior hardcoded value, so an unconfigured stage's
  behavior is byte-for-byte unchanged); `get_adapter_for_stage` overrides it with the stage's own
  configured value when set, re-validating against `[MIN_READ_TIMEOUT_SECONDS,
  MAX_READ_TIMEOUT_SECONDS]` and raising `InvalidStageTimeoutError` (mirroring
  `InvalidStageBudgetError`) for a row that reached the database without `full_clean()`. Every
  adapter now passes `self.request_timeout` -- a new `BaseLLMAdapter` property returning
  `(DEFAULT_CONNECT_TIMEOUT_SECONDS, self.effective_read_timeout_seconds)`, `requests`' own
  `(connect, read)` tuple form -- instead of a bare scalar. `DEFAULT_CONNECT_TIMEOUT_SECONDS = 10`
  is a new, disclosed, non-per-stage-configurable constant: previously there was no distinct
  connect-phase budget at all, so this is additive risk-reduction (a hung connection now fails in
  10s instead of up to 60s) rather than a narrowing of any existing behavior -- 10s is generous for
  reaching any of the four configured providers' API hosts under normal conditions and was not
  observed to be a bottleneck in this session's test runs. The 300-second (5-minute) upper bound on
  `read_timeout_seconds` keeps the worst case computable: at the ceiling, three timed-out attempts
  plus backoff is `3 * 300 + (1 + 2) = 903` seconds (~15 minutes) -- bounded, not unlimited, and
  never silently exceedable by a persisted value. `StageModelAssignmentAdmin` exposes
  `read_timeout_seconds` and a computed `effective_timeout_display` (e.g. "`(10s connect, 60s read
  (built-in default))`") in its list view, so the operator never has to infer the resolved value.
- **Classification unchanged**: a client-side `requests.Timeout` (both `ConnectTimeout` and
  `ReadTimeout`, which both subclass it) was already, and remains, classified `TIMEOUT` regardless
  of which phase raised it -- this decision only changes *how long each phase is allowed to run*,
  never how a timeout is categorized or retried. `RetryPolicy` itself (`max_attempts=3`,
  `backoff_seconds=1.0`) is unchanged.
- **Consequence**: `llm_provider.0006_stagemodelassignment_read_timeout_seconds` (additive, nullable
  field -- no data migration, no existing row's resolved behavior changes). 19 new deterministic
  tests (`llm_provider/tests/test_stage_read_timeouts.py`) cover resolution/override/isolation,
  full_clean and admin-form rejection of zero/negative/excessive values, the `InvalidStageTimeoutError`
  defense-in-depth path, the exact `(connect, read)` tuple reaching all four adapters' `requests.post`
  calls, connect-vs-read `requests.Timeout` subclasses both classifying as `TIMEOUT`, and the
  worst-case wall-clock arithmetic above. No `StageModelAssignment.read_timeout_seconds` value was
  set on any real row in the development database.

## D-030: OpenRouter key-status service and sanitized 429 diagnostic retention

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-05) -- same work package as D-029. No live
  OpenRouter call (key-status or inference) was made in this session; no OpenRouter management key
  was requested, stored, or referenced anywhere.
- **Key-status service** (`llm_provider/openrouter_key_status.py`, new): `fetch_openrouter_key_status
  (provider)` performs exactly one `GET {base_url}/key` using the provider's existing
  `credential_env_var`-referenced inference credential -- **never** `/api/v1/credits`, which
  OpenRouter documents as requiring a separate management key this application does not have and
  this decision does not authorize acquiring. A short, bounded `(5, 10)`-second `(connect, read)`
  timeout applies (deliberately far shorter than any inference stage's budget -- this is a
  lightweight metadata lookup, never a generation call). Parses only the documented, non-secret
  fields under the response's `data` envelope (`label`, `is_free_tier`, `limit`, `limit_remaining`,
  `limit_reset`, `usage`, `usage_daily`, `usage_weekly`, `usage_monthly`); every failure mode
  (missing credential, non-OpenRouter provider, timeout, network error, non-2xx status, malformed/
  unexpected JSON) returns a typed `OpenRouterKeyStatusResult(success=False, error_message=<
  sanitized>)` rather than raising, using the existing `sanitize_error_message()`. The credential
  value itself never appears on the result object, in any log line, or in the new
  `OpenRouterKeyStatus` model (new, one row per `LLMProvider`, storing only the fields listed above
  plus `fetched_at`/`success`/`error_message` -- migration
  `llm_provider.0007_llmcalllog_rate_limit_diagnostics_and_more`). **Never invoked automatically**
  -- not during inference, not during a retry, not on every page load of the diagnostics view below;
  the only call site is the view's explicit POST "Refresh OpenRouter status" action.
- **429 diagnostic retention** (`llm_provider/adapters/openrouter.py`'s new
  `parse_openrouter_rate_limit`, scoped to `OpenRouterAdapter` only -- the shared
  `parse_openai_style_chat_completion` branch used by `OpenAIAdapter`/`NvidiaNimAdapter` for their
  own 429s is untouched): retains, as a small typed dict on the existing
  `NormalizedLLMError.rate_limit_diagnostics` field (new, `None` for every non-RATE_LIMIT error and
  for a RATE_LIMIT error with no extractable metadata) --
  - `retry_after_seconds` (from the documented `Retry-After` header, digits-only parse, dropped
    rather than guessed if malformed/negative);
  - `limit`/`remaining` (from `X-RateLimit-Limit`/`X-RateLimit-Remaining`, read defensively since
    OpenRouter's own docs for this endpoint document only `Retry-After` -- present if some gateway
    layer attaches them, silently absent otherwise, never assumed);
  - `reset` (from `X-RateLimit-Reset`, reduced to a bounded digits-only token, never the raw header
    value);
  - `source` (`"upstream"` only when the error body's own `error.metadata` names an upstream
    provider under `provider_name`/`provider`, else `"unknown"` -- **never** a positive
    `"openrouter"` classification, since no documented signal distinguishes an OpenRouter-key-level
    limit from an upstream one by its absence; a false "openrouter-level" claim would be worse than
    an honest "unknown");
  - `upstream_provider` (the named provider label, sanitized and bounded to 100 characters) when
    `source == "upstream"`.
  `BaseLLMAdapter._write_call_log` persists this dict verbatim onto the new
  `LLMCallLog.rate_limit_diagnostics` `JSONField` (null for every other row) -- never the raw
  response body or headers wholesale, never a prompt, generated content, or credential. No change to
  retry behavior: OpenRouter 429s were already, and remain, classified `RATE_LIMIT` (a transient,
  retried category) with the same `RetryPolicy` bound as every other transient error; `Retry-After`
  is retained for operator visibility only and does not (in this decision) alter
  `execute_with_retry`'s fixed linear backoff -- doing so would mean changing the shared retry
  abstraction `llm_provider/retry.py` itself, which the task scoping this work explicitly flagged as
  a separate follow-up rather than a change to fold in invisibly here.
- **Operator UI** (`llm_provider/admin.py`, integrated into the existing Django admin rather than a
  new app/route namespace): a `LLMProviderAdmin`-attached view at
  `admin:llm_provider_openrouter_diagnostics`, linked from the `LLMProvider` changelist's object
  tools, showing -- per OpenRouter provider row -- the last-refreshed key-status snapshot (or "never
  refreshed yet"), a POST-only, CSRF-protected "Refresh OpenRouter status" action
  (`admin:llm_provider_openrouter_diagnostics_refresh`) that is the *only* call site for
  `fetch_openrouter_key_status`, a locally-observed (this application's own `LLMCallLog` rows,
  explicitly labeled "not an OpenRouter-reported figure") today's-call count clearly separated from
  the authoritative fetched data, and the most recent 25 sanitized OpenRouter `RATE_LIMIT`
  `LLMCallLog` rows (timestamp, stage, exact provider/model, retry count, and the diagnostic fields
  above) -- with a standing on-page warning that free-tier/upstream capacity can still return 429
  even when the local estimate looks fine. The view queries only `llm_provider`'s own models
  (`LLMProvider`/`LLMModel`/`LLMCallLog`/`OpenRouterKeyStatus`), which hold no Candidate Memory,
  job-posting, resume, employer, or application content at all -- so no such content can leak into
  this view by construction, and no unrelated schema expansion was needed or made to correlate a
  call log to a specific `JobApplication` (that correlation does not exist and is out of scope here,
  per the task's own explicit "keep the view provider/stage-level" instruction). Authenticated via
  Django's standard admin `staff_member_required`/session auth (`self.admin_site.admin_view`) --
  the same mechanism every other admin page on this site already uses.
- **Consequence**: `llm_provider.0007_llmcalllog_rate_limit_diagnostics_and_more` (additive: one new
  nullable `JSONField` on `LLMCallLog`, one new model `OpenRouterKeyStatus`). 39 new deterministic
  tests across `test_openrouter_key_status.py` (11), `test_openrouter_rate_limit_diagnostics.py`
  (13), and `test_openrouter_diagnostics_view.py` (15) -- success/auth-error/timeout/malformed-
  payload/sanitization for the key-status service; header parsing, malformed/missing-header
  handling, upstream-attribution presence/absence, sanitization/bounding, and end-to-end
  `LLMCallLog` persistence for 429 diagnostics; authentication requirements, content rendering,
  credential non-leakage, the "never automatic" refresh guarantee, CSRF enforcement, and absence of
  other apps' content vocabulary for the admin view. No live OpenRouter call, no
  `OpenRouterKeyStatus` row, and no `StageModelAssignment` change exists in the development database
  as a result of this decision.

## D-031: `gpt-5` Chat Completions request-contract compatibility fix (no live call)

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-05) -- read-only-audit-then-fix work package,
  confirmed against current official OpenAI documentation
  (`developers.openai.com/api/docs/models/gpt-5`, the reasoning-models guide, and corroborating
  OpenAI Developer Community reports of the exact 400 error text). **No live OpenAI call was made or
  authorized by this decision** -- it is separate from, and does not consume, the independently
  authorized one-time GPT-5 M5 run. No `LLMProvider`/`LLMModel`/`StageModelAssignment` row was
  created in the development database; `AB_BUILD` (or any stage) was not assigned to OpenAI/gpt-5.
- **Confirmed model metadata for the exact `gpt-5` slug** (never `gpt-5-chat-latest` or any other
  variant): supports `v1/chat/completions` and `v1/responses`; context window 400,000 tokens; **max
  output tokens 128,000**; supported features include `structured_outputs` and `function_calling`;
  `reasoning.effort` accepts `minimal`/`low`/`medium`/`high` (not the `none`/`xhigh`/`max` values
  later, unrelated model generations added).
- **Confirmed incompatibility with this codebase's pre-existing shared request-body contract**
  (`build_chat_completion_body` in `llm_provider/adapters/openai.py`, shared by `OpenAIAdapter`,
  `NvidiaNimAdapter`, and `OpenRouterAdapter`), on two counts specific to OpenAI's reasoning-model
  family (o1/o3/gpt-5, ...), both independently corroborated by OpenAI's own documented 400 error
  text ("Unsupported parameter: 'max_tokens' is not supported with this model. Use
  'max_completion_tokens' instead." / "Unsupported value: 'temperature' does not support 0 with this
  model. Only the default (1) value is supported."):
  1. **Output-token parameter name**: `gpt-5` rejects `max_tokens` outright; the equivalent
     parameter is `max_completion_tokens`. The pre-existing shared builder always sent `max_tokens`.
  2. **Temperature**: `gpt-5` rejects any explicit `temperature` other than its own default (`1`).
     `NormalizedLLMRequest.temperature` defaults to `0.0` and the pre-existing builder always sent
     it -- an unmodified call against `gpt-5` would 400 on essentially every request, not just an
     edge case.
  `top_p` was never sent by `OpenAIAdapter` at all (only `nvidia.py`/`openrouter.py` send it), so no
  separate fix was needed for it; `reasoning_effort` was previously never read or sent by
  `OpenAIAdapter` at all (a pre-existing gap, not a regression this decision introduces). Structured
  output (`response_format: {"type": "json_schema", ..., "strict": true}`), the shared parser's
  `finish_reason=length`/null-content handling (D-026, already provider/model-agnostic), and usage
  parsing (`completion_tokens` already includes any reasoning-token consumption per OpenAI's own
  usage accounting -- no separate `reasoning_tokens` field needed to be added) were all confirmed
  compatible with `gpt-5` as-is, requiring no change.
- **Fix**: `build_chat_completion_body` gained two keyword-only parameters, `token_limit_key`
  (default `"max_tokens"`) and `include_temperature` (default `True`) -- both defaults reproduce the
  exact prior behavior for every existing caller (`nvidia.py`/`openrouter.py`, which call it with no
  overrides at all, and `OpenAIAdapter` itself on a non-reasoning model), so **zero behavior change**
  for NVIDIA NIM, OpenRouter, or any already-configured OpenAI model. `OpenAIAdapter._call_once` now
  branches on the existing `LLMModel.supports_reasoning` capability flag (the same flag
  `nvidia.py`/`openrouter.py` already use to gate their own reasoning parameters) -- when true, it
  requests `token_limit_key="max_completion_tokens"`/`include_temperature=False`, and additionally
  sends OpenAI's own top-level `reasoning_effort` field, but **only** when
  `NormalizedLLMRequest.reasoning_effort` is explicitly set by the caller (never a blanket default --
  matching this task's own "stage reasoning setting: unset unless the production stage explicitly
  supports and requests it" instruction) and fails closed with `CONFIGURATION` (no HTTP call) if
  `reasoning_effort` is requested against a model not registered as `supports_reasoning`, mirroring
  `nvidia.py`/`openrouter.py`'s identical guard for their own reasoning parameters.
- **Consequence**: no migration (no schema change). 11 new deterministic tests
  (`llm_provider/tests/test_gpt5_compatibility.py`) construct a registry `LLMModel` at the exact
  intended future metadata (`model_id="gpt-5"`, `supports_reasoning=True`,
  `max_output_tokens=128_000`, `supports_structured_output=True`) and confirm, with `requests.post`
  mocked (no network, no credential, no live call): `max_completion_tokens` sent and `max_tokens`
  absent; `temperature` entirely absent even when the request specifies the codebase's own `0.0`
  default; `reasoning_effort` sent only when explicitly requested and rejected closed on a
  non-reasoning model; the exact `"gpt-5"` slug reaches the request body unsubstituted; structured
  output, `finish_reason=length` classification, and usage parsing all behave correctly; and a
  parallel non-reasoning-model test class proves `gpt-4o`-class models keep the byte-for-byte
  original request shape. **Intended future registry row** (not created in this decision, per the
  task's "prepare the exact values, do not make operational DB changes" instruction): provider type
  `OPENAI`, credential env var `OPENAI_API_KEY`, model slug exactly `gpt-5`,
  `supports_structured_output=True`, `max_output_tokens=128000`, `supports_reasoning=True` (capability
  flag only -- no stage's `NormalizedLLMRequest.reasoning_effort` is set by this decision), no
  fallback/substitution model anywhere in the adapter or registry.

## D-032: OpenAI Structured Outputs strict-schema `required`-completion fix (no live call)

- **Status**: **APPROVED AND IMPLEMENTED** (2026-09-05) -- confirmed against a real, safe
  diagnostic HTTP 400 (never a raw response body -- only the sanitized `type`/`param`/`message`
  fields, reproduced here as a regression fixture) and against current official OpenAI Structured
  Outputs documentation (`developers.openai.com/api/docs/guides/structured-outputs`, "Supported
  properties" / "Some type-specific keywords are not yet supported" / "All fields must be
  required" sections, fetched and quoted verbatim during this work). **No live OpenAI call was
  made or authorized by this decision** -- schema correctness was verified entirely through
  deterministic Pydantic/JSON-Schema inspection and mocked-HTTP adapter tests. No `LLMProvider`/
  `LLMModel`/`StageModelAssignment` row, `JobApplication` 9, `JobRequirementAnalysis` 10,
  `CandidateMemory` 7, Gate, `FitAssessment`, or `ResumeDraft` was touched.
- **Confirmed incident**: a synthetic OpenAI `gpt-5` diagnostic request returned HTTP 400,
  `type=invalid_request_error`, `param=response_format`: "Invalid schema for response_format
  'RequirementNormalizationOutput': In context=(), 'required' is required to be supplied and to
  be an array including every key in properties. Missing 'diagnostic_terms'."
- **Root cause**: `RequirementNormalizationItem` (`candidate_matching/schemas.py`) has six
  properties; Pydantic's `model_json_schema()` only lists a field in JSON Schema `required` when
  it has no default, so `diagnostic_terms`/`equivalents`/`preserved_technical_terms` (all
  `default_factory=list`) were silently absent from `required`. OpenAI's own documented rule is
  "all fields must be required" -- there is no way to mark a property "optional" other than
  folding `null` into its own type (`anyOf [..., {"type": "null"}]`) -- so OpenAI rejects an
  incomplete `required` array before generation ever starts. The pre-existing
  `to_openai_strict_schema()` (`llm_provider/schema_translation.py`) added
  `additionalProperties: false` but never completed `required`.
- **Every-property-required fix**: `_complete_object_contract()` now recursively walks the entire
  JSON-Schema structure -- root object, `$defs`, nested objects, array item objects, and anything
  reached through `$ref` -- and, for every node describing an object, sets
  `additionalProperties: False` and `required = list(properties.keys())` in `properties`' own
  insertion order (deterministic). A field with a non-null default (an empty list, `""`, a fixed
  string) keeps its original, non-nullable type -- the provider must always supply a value, and
  the AC_NORMALIZE prompt/schema guide the model toward the empty/default shape. A field that was
  already `Optional[X] = None` is untouched beyond being added to `required` -- its schema already
  expresses "no value" via OpenAI's own documented nullable-union pattern.
  `_assert_object_contract_complete()` re-walks the result and raises
  `OpenAIStrictSchemaContractError` -- caught by each of `OpenAIAdapter`/`NvidiaNimAdapter`/
  `OpenRouterAdapter`'s `_call_once`, before `build_chat_completion_body`/`requests.post` are ever
  reached, and returned as `NormalizedLLMResult(error=NormalizedLLMError(category=CONFIGURATION,
  ...))` -- a local configuration/request-contract failure, never retried
  (`CONFIGURATION` is not in `TRANSIENT_ERROR_CATEGORIES`), never a provider response issue.
- **Official OpenAI supported-keyword assessment** (quoted from the live doc, 2026-09-05):
  supported `string` properties are only `pattern` and `format` (a fixed enumerated list:
  `date-time`/`time`/`date`/`duration`/`email`/`hostname`/`ipv4`/`ipv6`/`uuid`); supported
  `number` properties are `multipleOf`/`maximum`/`exclusiveMaximum`/`minimum`/`exclusiveMinimum`;
  supported `array` properties are `minItems`/`maxItems`; `additionalProperties: false` is always
  required on every object; composition keywords `allOf`/`not`/`dependentRequired`/
  `dependentSchemas`/`if`/`then`/`else` are never supported; `$ref`/`$defs` (including recursive
  schemas) and `enum`/`anyOf` are fully supported. **`minLength`/`maxLength` for strings are never
  listed as supported for any model** -- confirmed by omission from the documented "Supported
  `string` properties" list, independent of the separate "fine-tuned models additionally do not
  support..." caveat (which is a stricter subset for fine-tuned models only and does not imply
  base models support `minLength`/`maxLength` either). This project's only current use of a
  string-length constraint (`candidate_matching.schemas.BoundedTerm`,
  `MAX_TERM_CHARS=60`/`RequirementNormalizationItem.canonical_english_text`'s
  `MAX_TEXT_CHARS=500`) is therefore confirmed unsupported by OpenAI and stripped from its
  outbound schema only. A schema audit of every registry-used Pydantic model (`ChunkExtractionResult`,
  `AgentJobberAnalysis`, `RequirementNormalizationOutput`, `RelevanceRankingOutput`,
  `AgentCandidateAssessment`, `AgentBuilderOutput`, `SmokeTestOutput`) found `maxLength` as the
  *only* officially-unsupported keyword generated anywhere in this codebase; no `allOf`,
  `patternProperties`, or other unsupported composition keyword is ever generated.
- **Provider-specific schema dialect (D-032)**: `to_openai_strict_schema()` remains OpenAI's own
  dialect: full `required`-completion plus stripping `minLength`/`maxLength`
  (`_OPENAI_UNSUPPORTED_STRING_LENGTH_KEYS`). A new sibling, `to_openai_compatible_strict_schema()`,
  applies the identical `required`-completion fix for NVIDIA NIM/OpenRouter -- both OpenAI-
  compatible `response_format: {type: json_schema, strict: true}` endpoints, per the pre-existing
  adapter docstrings -- but does **not** strip `minLength`/`maxLength`: neither provider is
  confirmed (by documentation or an observed error) to reject them, and this project already
  relies on NVIDIA/OpenRouter enforcing `maxLength: 60` server-side
  (`candidate_matching/tests/test_normalize.py::ProviderFacingSchemaContractTests`, predating this
  decision). `OpenAIAdapter.translate_schema` calls `to_openai_strict_schema`;
  `NvidiaNimAdapter.translate_schema`/`OpenRouterAdapter.translate_schema` now call
  `to_openai_compatible_strict_schema` (previously all three called the same, OpenAI-named,
  function -- an accurate description before this decision, since it never diverged from what
  NVIDIA/OpenRouter needed; it does now, since only OpenAI has a confirmed keyword gap). This
  is the "explicit schema dialect/profile" the task's Phase D asked for, rather than provider-name
  conditionals scattered through pipeline/adapter call sites -- the branching exists in exactly
  one place per adapter (`translate_schema`), matching the adapters' pre-existing per-provider
  customization point.
- **Canonical Pydantic validation is unchanged and remains authoritative**: `BaseLLMAdapter.
  generate()`'s `request.output_schema.model_validate(result.content)` call (D-005) was not
  touched. `MAX_TERM_CHARS=60`, `MAX_TEXT_CHARS=500`, every array-count bound, `extra="forbid"`,
  and the requirement-ID-set integrity check all continue to reject non-conforming content after
  a provider response, regardless of whether the constraint that would have caught it earlier is
  present in the outbound schema OpenAI now receives -- verified directly: a mocked OpenAI
  response containing a 61-character `diagnostic_terms` entry, a 501-character
  `canonical_english_text`, and 101 `items` (all values a stricter OpenAI-bound schema would have
  previously rejected server-side) each still produce
  `NormalizedLLMResult(error.category=SCHEMA_VALIDATION)` through the unmodified post-response
  path (`llm_provider/tests/test_strict_schema_required_completion.py::
  PostResponseValidationStillEnforcesStrippedConstraintsTests`).
- **Prompt alignment**: `candidate_matching/services/normalize.py`'s `SYSTEM_PROMPT` gained one
  sentence stating `diagnostic_terms`/`equivalents`/`preserved_technical_terms` are always present
  in the output and must be an empty list, never omitted or fabricated, when a requirement has
  nothing to report for one of them -- the existing `MAX_TERM_CHARS`-based sentence (added under
  D-027) was left as the single source of truth for the length limit, not duplicated. Other
  registry prompts (`AJ_ANALYZE`'s `screening_risks`, `AB_BUILD`'s many `default_factory=list`
  fields) were reviewed and judged not to need a change: `AJ_ANALYZE`'s prompt already states an
  empty `screening_risks` is correct ("it is either an ordinary requirement... or nothing at all"),
  and `AB_BUILD`'s existing claim-ID-citation validator (`resume_builder/validators/no_
  fabrication.py`) already rejects any fabricated content regardless of prompt wording, so a
  defaulted-list field being forced into OpenAI's `required` array carries no new fabrication risk
  there.
- **HTTP-400 classification boundary: deferred, not fixed in this decision**. The task raised
  whether an OpenAI `invalid_request_error` with `param=response_format` should classify as
  `CONFIGURATION` (a request-contract problem) rather than the generic `SCHEMA_VALIDATION` bucket
  `parse_openai_style_chat_completion`'s catch-all `>= 400` branch currently assigns it (that
  branch does not parse the response body at all today). This decision's own fix eliminates the
  schema-shape cause of the confirmed incident before any HTTP call is made
  (`_assert_object_contract_complete`), so no residual classification gap exists for this specific
  failure mode going forward. Implementing body-parsing-based reclassification would require new
  JSON-body-parsing logic in that shared branch -- not "already available in the parser" per the
  task's own Phase G gating criterion -- so it is deferred as a distinct, independently-scoped
  follow-up decision rather than bundled into a schema-correction task, to avoid widening this
  change's blast radius. If taken up later: it must extract only `error.type`/`error.param` (never
  persist the raw body), remain non-retryable, and leave every existing status-classification test
  (`test_error_classification.py`) unchanged for every status code this decision did not touch.
- **Consequence**: no migration (no model/schema-field change). 26 new deterministic tests
  (`llm_provider/tests/test_strict_schema_required_completion.py`) plus 5 new/updated tests in
  `candidate_matching/tests/test_normalize.py::ProviderFacingSchemaContractTests` and one new test
  in `PromptContractTests` -- covering the regression fixture, recursive `required`-completeness
  across every registry-used schema in both dialects (including `$defs`, nested objects, array
  item objects, `$ref` targets, nullable-optional fields, and defaulted non-null lists),
  determinism, the OpenAI/NVIDIA/OpenRouter keyword-stripping split, the pre-HTTP invariant guard
  (mocked `requests.post` proven never called), the final request body for both dialects, and
  post-response canonical validation surviving the stripped keyword. Full suite: 1042/1042 passing
  (`llm_provider`, `candidate_matching`, `candidate_memory`, `job_intake`, `resume_builder`,
  `job_applications`, `reviews`); `manage.py check`/`makemigrations --check --dry-run`/
  `ruff check .`/`git diff --check` all clean; zero live provider calls.

## D-033: Accept ResumeDraft id=4 (JobApplication 9) as the v1 final deliverable despite named engagement/language evidence gaps

- **Status**: **APPROVED** (2026-09-06) -- an explicit operator decision, recorded here verbatim
  rather than inferred, ahead of Milestone M7's integration/dashboard work.
- **Context**: `JobApplication` 9 completed a real, live-provider M5/M6 run (`LLMCallLog` ids
  318-320 for `AC_NORMALIZE`/`AC_RANK`/`AC_MATCH`, id 321 for `AB_BUILD`, OpenAI `gpt-5` via
  `StageModelAssignment` id 24, budget 16384, read timeout 300s). Both Human Review Gates were
  manually approved by the operator: Gate 1 (after three AJ feedback/re-run cycles, landing on
  `JobRequirementAnalysis` id 10/version 2) and Gate 2, which confirmed `ResumeDraft` id 4/version
  1 (`FitAssessment` id 9/version 1) and advanced `JobApplication` 9 to `READY`.
- **Decision**: the operator accepts this draft as the v1 final markdown deliverable for
  `JobApplication` 9 despite three known, named limitations:
  1. no Continental-specific résumé bullets (CE-0002);
  2. no Maruti-specific résumé bullets (CE-0003);
  3. no generated `LanguageProficiency` elements.
- **What this decision is not**: it is not a correction to Candidate Memory (no `MemoryClaim`,
  support, or rule was edited, retired, or reinterpreted because of this draft), and it is not
  evidence that the missing Continental/Maruti/language facts do not exist in Candidate Memory --
  only that they did not surface through this particular bounded M5 retrieval pass for this
  particular job requirement analysis. The résumé's section *structure* (headings, ordering,
  certifications/languages-omitted-when-empty) is deterministic/static per
  `docs/RESUME_OUTPUT_STRUCTURE.md` and was not in question here; the *contents* of a language
  section, when one is generated, are never static facts hard-coded anywhere -- they must trace to
  eligible Candidate Memory evidence exactly like any other `ResumeElement`, the same as this
  draft's summary/experience/skill elements do.
- **Consequence**: no code change follows from this decision by itself -- it is a record of an
  operator judgment call about one specific artifact's acceptability, not a defect fix. The
  Continental/Maruti engagement-balanced retrieval gap and the absent language evidence are
  recorded as a future M5 retrieval-completeness improvement (broaden bounded retrieval to more
  reliably surface evidence for every `APPROVED` `CareerEngagement` referenced by a posting's
  requirements, and confirm eligible language evidence reaches the `FitAssessment`/Agent Builder
  context), not undertaken in this decision or in the M7 work package that accompanies it.
  `JobApplication` 9, `JobRequirementAnalysis` 10, `FitAssessment` 9, and `ResumeDraft` 4 were not
  regenerated, re-run, or edited as part of recording this decision.

## D-034: `application_outcome` may only be recorded once Gate 2 is approved and the chain is current

- **Status**: **PROPOSED** (2026-09-06) -- an implementation-level interpretation of DASH-003
  (requirements.md Sec 17), not yet product-owner-approved; recorded honestly as proposed rather
  than silently treated as settled.
- **Requirement**: DASH-003 (`application_outcome` is operator-set, independent of
  `pipeline_phase`, no speculative states beyond `NOT_APPLIED`/`APPLIED`/`INTERVIEWING`/
  `REJECTED`).
- **Gap**: requirements.md Sec 17 establishes that the two dimensions are independent but does not
  say whether an outcome may be recorded *before* a final resume actually exists.
  Recording `APPLIED` against a `JobApplication` with no `READY`, confirmed, current resume draft
  would mean the dashboard claims an application was submitted with no corresponding deliverable
  on record -- a misleading combination of durable state, not merely an early one.
- **Decision (proposed)**: `job_applications.services.set_application_outcome` refuses to move
  `application_outcome` away from `NOT_APPLIED` unless `pipeline_phase == READY` **and** the chain
  is not stale (the current resume draft is confirmed and current relative to the current fit
  assessment, which is itself current relative to the current job requirement analysis -- the same
  chain-wide freshness check, HITL-007, used everywhere else in this milestone). Once outcome has
  moved away from `NOT_APPLIED`, the operator may freely move among `APPLIED`/`INTERVIEWING`/
  `REJECTED` (e.g. to correct a mis-click) without an additional precondition, since `pipeline_phase`
  never regresses once `READY`. This never blocks `pipeline_phase` or the current-version pointers
  from advancing further (e.g. a later Gate-2 feedback re-run and re-approval) -- and,
  independently of this decision, nothing anywhere in the codebase ever writes
  `application_outcome` except this one action, so a later artifact regeneration never reverts an
  outcome already recorded (verified by `job_applications/tests/test_dashboard_services.py::
  ApplicationOutcomeServiceTests::test_outcome_survives_resume_draft_regeneration`).
- **Consequence**: no schema change (`application_outcome`'s choices are unchanged). If the product
  owner later decides an outcome should be recordable earlier (e.g. `APPLIED` recorded manually
  outside this system before the dashboard existed), this decision's precondition is the one place
  to relax, not a scattered set of view-level checks.

## D-035: D-033's "accepted limitation" characterization is superseded/rejected by the Product Owner; Continental/Maruti/German omission is an open retrieval defect, not an accepted limitation

- **Status**: **APPROVED** (2026-09-06) -- an explicit Product Owner correction, recorded verbatim
  rather than inferred, made during the M7 independent re-audit/UX follow-up work package. This
  entry supersedes D-033's characterization; it does **not** delete or rewrite D-033 itself, which
  remains above exactly as originally recorded, per this project's append-only decision history.
- **Context**: D-033 (above) recorded the operator's 2026-09-06 acceptance of `ResumeDraft` id 4 as
  `JobApplication` 9's v1 final deliverable, describing the missing Continental (`CE-0002`)/Maruti
  (`CE-0003`) bullets and the absent `LanguageProficiency` element as "known, non-blocking
  limitations" of that specific artifact. On further review, ordered as part of the M7 UX follow-up
  work package, the Product Owner explicitly rejected that characterization.
- **Product Owner correction (verbatim intent)**: omission of Continental, Maruti, or applicable
  confirmed language evidence is **not** accepted as intended final résumé behavior. D-033 must not
  be read as, and must not have stated, that this omission is an accepted limitation. The
  already-approved `ResumeDraft` id 4 remains an immutable historical version -- it is not edited,
  deleted, or regenerated by this decision -- but the retrieval/composition gap that produced it
  remains an **open defect** requiring diagnosis and a later versioned M5/M6 correction, not a
  closed or accepted matter.
- **What changed since D-033 was written**: a dedicated read-only diagnostic investigation (same
  work package, see `docs/CURRENT_STATE.md`'s corresponding entry for the full evidence) traced the
  omission to a specific, provable point in the M5 retrieval pipeline rather than leaving it as an
  unexplained gap:
  - `CandidateMemory` id 7 (`ACTIVE`) has ample confirmed, engagement-mapped, résumé-eligible
    evidence for both Continental (31 `APPROVED`-mapped claims, all confirmed/eligible) and Maruti
    (38, all confirmed/eligible), and one confirmed, eligible German-language-proficiency claim
    (`MC-7-0933`) -- the underlying evidence is **not** missing from Candidate Memory, contradicting
    any reading of D-033 as "the facts may not exist."
  - Reconstructing the deterministic stages of the M5 pipeline against `FitAssessment` id 9's own
    persisted `retrieval_manifest` (raw pool 238, capped pool 150 -- reproduced exactly, no LLM call
    needed) shows 2 Continental and 4 Maruti claims, and the one German claim, all reached the
    candidate pool actually sent to `AC_RANK` -- and zero of any of them were selected into
    `FitAssessment` id 9's 73 `retrieved_claim_ids`. The German claim only reached the pool at all
    via the deterministic minimum-candidate floor-backfill for two unrelated technology
    requirements, because none of JRA 10's 30 requirements concern language or location -- a
    structural symptom, not a one-off ranking miss.
  - No further loss occurred downstream: `ResumeDraft` id 4's `retrieved_claim_ids` are identical to
    `FitAssessment` id 9's, and the renderer correctly omits an engagement's header only because no
    `ResumeElement` cites that engagement -- there is no separate M6/rendering bug compounding this.
  - This is a mix of **direct proof** (Candidate Memory content, mapping counts, pool
    reconstruction, absence from `retrieved_claim_ids`) and **inference** (why `AC_RANK`/`AC_MATCH`
    specifically did not select these candidates once pooled) -- raw provider request/response
    bodies were never persisted for this run, so the model's own selection reasoning cannot be
    recovered; this limitation is stated honestly rather than papered over.
- **Root-cause assessment**: job-relevance retrieval (BM25 candidate generation, per-requirement and
  global pool caps, `AC_RANK`) is being used as the *sole* source for career-chronology
  completeness. `CareerEngagement` eligibility itself is not relevance-filtered -- every `APPROVED`
  engagement, including Continental and Maruti, is already in `FitAssessment` id 9's
  `retrieved_engagement_ids` -- but the claims that would actually populate an engagement's bullets
  are entirely subject to this job posting's specific 30 requirements, with no engagement-balance
  guarantee anywhere in the bounded-retrieval path. A posting that happens not to phrase any
  requirement in a way that scores Continental/Maruti/language claims into the selected set will
  reliably omit them, regardless of how much confirmed, eligible, engagement-mapped evidence exists.
- **Consequence**: no code change is made by this decision itself, and none of `JobApplication` 9,
  `JobRequirementAnalysis` 10, `FitAssessment` 9, `ResumeDraft` 4, or `CandidateMemory` 7 was
  regenerated, re-run, or mutated while recording it -- this is a documentation and status
  correction only. A future, separately authorized versioned M5 (and possibly M6) correction is
  required before this can be marked resolved. Candidate remedies (recorded for that future work,
  not decided here): (1) an engagement-balanced minimum-evidence floor inside M5's existing
  candidate-pool construction; (2) a deterministic baseline career-chronology bundle (title/dates/
  location plus a small fixed number of confirmed anchor claims per `APPROVED` `CareerEngagement`)
  supplied to Agent Builder independently of job-relevance ranking; (3) a hybrid of both -- a
  guaranteed baseline chronology for every approved engagement, with relevance-ranked evidence
  layered on top for tailored emphasis, extending the same deterministic, zero-LLM pattern
  `services/static_requirements.py` already establishes for other per-engagement facts (D-019). Of
  these, (3) is the recommended direction because it is the smallest change consistent with the
  existing no-fabrication/provenance/Gate-versioning invariants, but selecting and implementing a
  remedy is explicitly deferred to a separately authorized M5/M6 work package, not this one.
- **Also noted while investigating**: the codebase has no dedicated "reopen a `READY` application"
  action, but a de facto path already exists and requires no schema change -- `reviews.gate1_view`/
  `gate2_view` and the service functions behind them place no `pipeline_phase` precondition on
  re-running Agent Candidate or submitting Gate 1 feedback, and `resume_builder.services.build.
  build_resume_draft()` explicitly permits `PREPARATION` **or** `READY`. An operator can already
  reopen Gate 1 on a `READY` application, obtain a new `FitAssessment` version, rebuild and
  re-approve Gate 2 -- `pipeline_phase` never needs to regress. This was not exercised in this work
  package (doing so would count as an M5/M6 invocation, which this work package's authorization
  explicitly excludes) and remains available for the future corrective work referenced above.

## D-036: D-035's deterministic architecture correction -- hybrid baseline chronology implemented and tested; the real JobApplication 9 rerun remains separately authorized

- **Status**: **PROPOSED** (2026-09-06) -- implemented and tested on an isolated worktree/branch
  (`worktree-hybrid-chronology-fix`, branched from `fb91e60`), not merged to `main` and not yet
  reviewed/approved by the product owner in this session. Recorded here as the concrete design D-035
  asked for, per candidate remedy (3) (the recommended hybrid) in that entry.
- **What this decision covers**: the deterministic, zero-LLM correction described in full in
  `docs/ARCHITECTURE.md` §9c -- new `resume_builder/services/baseline_chronology.py`
  (`compute_engagement_anchors`, `compute_language_evidence`, `merge_retrieved_claims`), a
  `retrieval_reasons` provenance field added to `candidate_matching.services.retrieve.
  RetrievedClaim` (plus an `engagements_without_eligible_evidence` diagnostic field on
  `RetrievalContext`), `resume_builder/services/context.py::build_builder_context` rewritten to
  merge job-relevant + engagement-anchor + language evidence and to enumerate `APPROVED`
  engagements live rather than from `FitAssessment.retrieved_engagement_ids`, the Agent Builder
  prompt (`services/generate.py`) restructured into four explicitly labeled evidence categories,
  and the markdown renderer (`rendering/markdown.py`) changed to render every retrieved engagement
  unconditionally with an explicit diagnostic line when it has zero eligible evidence, rather than
  only an engagement a bullet happened to cite.
- **What this decision does not cover**: it does not resolve D-035 as operationally closed. No
  code change here touched `JobApplication` 9, `JobRequirementAnalysis` 10, `FitAssessment` 9,
  `ResumeDraft` 4, or `CandidateMemory` 7 -- confirmed unchanged before/after (see
  `docs/CURRENT_STATE.md`'s corresponding entry for the exact counts). Regenerating the real
  deliverable with this correction requires a separately authorized, versioned M5/M6 rerun (a new
  `FitAssessment` and `ResumeDraft` version for `JobApplication` 9), which this work package's
  authorization explicitly excludes.
- **Also delivered, same work package**: `job_applications.services.begin_new_version_from_ready`,
  the canonical, explicit-authorization entry point for the READY-revision workflow D-035's own
  investigation noted as a de facto, unformalized capability -- no migration required (see
  `docs/ARCHITECTURE.md`'s `JobApplication` entry and `job_applications/tests/
  test_revision_workflow.py`). Not exercised against `JobApplication` 9.
- **Verification**: 19 new deterministic tests (`resume_builder/tests/test_hybrid_chronology.py`,
  15; `job_applications/tests/test_revision_workflow.py`, 4) plus 2 existing M6 tests updated to
  match the corrected architecture (`resume_builder/tests/test_context.py`'s missing-`based_on_jra`
  fixture gap, exposed now that baseline-chronology computation always runs when an `ACTIVE`
  `CandidateMemory` exists regardless of job-relevant claim survival; `resume_builder/tests/
  test_engagement_placement_integration.py`'s cross-revision test, whose "context is entirely
  empty" assumption no longer holds now that an engagement's own legitimate anchor claim is
  deterministically present) -- full project suite green (1111/1111), `ruff check .` clean,
  `manage.py check`/`makemigrations --check --dry-run` clean, zero live provider calls (`FakeAdapter`
  only throughout).
