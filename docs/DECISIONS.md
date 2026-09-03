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
  revisit without evidence of a real retrieval-quality problem.

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
