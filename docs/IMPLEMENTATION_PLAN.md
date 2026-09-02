# Implementation Plan

Status: pre-implementation. Milestones are dependency-ordered; none has started. Each is scoped to
be independently reviewable and committable. Requirement IDs reference
`docs/REQUIREMENT_TRACEABILITY.md`; component names reference `docs/ARCHITECTURE.md`.

Do not begin a milestone whose "Dependencies" are unmet. Do not skip a milestone's acceptance
criteria to reach a later one faster.

## Milestone dependency graph (corrected 2026-09-02 per product-owner review)

```
M0
 |
 M1
 |
 M2
 / \
M3   M4
 \   /
  M5
   |
  M6
   |
  M7  ---> Architecture/orchestration review checkpoint (D-001)
   |
  M8
```

`M3` (Candidate Memory) and `M4` (Agent Jobber) both depend only on `M2` — **not on each other.**
Agent Jobber does not read `CandidateMemory`, so there is no logical dependency from M4 to M3. Both
converge at `M5`, which needs both a confirmed memory and a job analysis to produce a
`FitAssessment`. Implementation may still proceed sequentially at the operator's discretion; the
graph is not an instruction to parallelize coding, only an accurate statement of what blocks what.

---

## M0 — Architecture and project controls

- **Objective**: produce the durable planning foundation before any code exists.
- **Requirements covered**: all (this milestone is the traceability/architecture work itself).
- **Scope**: `requirements.md` analysis, requirement ID catalog, architecture proposal, domain
  model, provider-abstraction design, milestone plan, decisions log, test strategy, traceability
  matrix, `CLAUDE.md`.
- **Out of scope**: any Django project, migration, dependency install, or Docker config.
- **Dependencies**: none.
- **Expected files**: `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/DECISIONS.md`,
  `docs/TEST_STRATEGY.md`, `docs/REQUIREMENT_TRACEABILITY.md`, `docs/CURRENT_STATE.md`,
  `CLAUDE.md`.
- **Acceptance criteria**: all six docs + CLAUDE.md exist; `requirements.md` is byte-identical to
  before this milestone; every requirement ID in requirements.md appears in the traceability
  matrix; no DECISIONS.md entry is marked APPROVED.
- **Verification**: `git diff` shows only new files, zero changes to `requirements.md`.
- **Completion evidence**: this milestone's own commit.
- **Risks**: none (documentation only).

## M1 — Django/PostgreSQL application foundation

- **Objective**: a running Django project against a local Dockerized PostgreSQL, with the app
  boundaries from ARCHITECTURE.md §2 scaffolded (empty apps, no business logic), including the
  `job_applications` app shell (D-012 is approved, not pending) created early — empty, like every
  other app — so later milestones have it to FK into without retrofitting the app itself.
- **Requirements covered**: STACK-001, STACK-002, STACK-003 (absence), STACK-004 (absence),
  STACK-006 (absence), STACK-007, STACK-008, NG-002 (product-level auth only — see clarification
  below), NG-004, NG-005, ACTOR-001.
- **Scope**: Django project skeleton, **including `django.contrib.admin`, `django.contrib.auth`,
  `django.contrib.sessions`, and `django.contrib.contenttypes`** — these are standard framework
  infrastructure needed for provider/model registry administration and are explicitly *not*
  excluded by NG-002's "no multi-tenant auth" non-goal (v1.1 clarification: that non-goal is about
  product-level accounts/tenants/roles, never about Django's own admin auth); `docker-compose.yml`
  for Postgres only; `.env.example` (placeholders only, covering local Postgres/Django config plus
  OpenAI/NVIDIA NIM/Gemini credential variable names) + `.gitignore` covering `.env`; empty
  `llm_provider`, `candidate_memory`, `job_intake`, `candidate_matching`, `resume_builder`,
  `reviews`, **`job_applications`** apps (models/views files present, no business-logic content
  yet). **Implementation decision (2026-09-02)**: `job_applications` is scaffolded empty like every
  other app at M1, not given the `JobApplication` model's fields early — its
  `current_jra`/`current_fit_assessment`/`current_resume_draft` FKs target models
  (`JobRequirementAnalysis`/`FitAssessment`/`ResumeDraft`) that don't exist until M4/M5/M6, so
  defining them at M1 would mean forward-referencing apps that aren't built yet. The
  `JobApplication` model is built in M4 instead (see M4 below); this resolves the ambiguity in the
  original "may get its model shape early" wording conservatively, in favor of keeping M1 pure
  scaffolding. Base template layout; repeatable **local** quality commands (Django system check,
  `manage.py test`, lint/static validation).
- **Out of scope**: any model fields for any app (including `job_applications`), any business logic, any
  LLM calls.
- **Dependencies**: M0.
- **Expected files**: `manage.py`, project settings package, `docker-compose.yml`,
  `.env.example`, `requirements.txt`/`pyproject.toml`, one `apps.py`-only Django app per boundary,
  a local lint/test/check script or Makefile target.
- **Acceptance criteria**: `docker compose up` brings up Postgres; `manage.py migrate` runs clean;
  `manage.py check` passes; the local quality commands (check + test + lint) run and pass
  repeatably; no secret value is committed (grep for common credential patterns finds none); the
  Django admin is reachable and a superuser can log in. **(v1.1 correction)**: a remote/CI "green
  link" is not required to complete M1 — if a remote CI environment already exists for this repo,
  adding minimal CI is acceptable, but its absence does not block M1. Local repeatability is the
  hard requirement.
- **Verification**: manual `docker compose up` + `manage.py runserver` smoke test; local quality
  commands run and pass.
- **Completion evidence**: this milestone's commit + a recorded local quality-command run.
- **Risks**: none significant — pure scaffolding.

## M2 — LLM provider abstraction, registry, and audit foundation

- **Objective**: the `llm_provider` app fully implemented per ARCHITECTURE.md §3, including a fake/
  test-only adapter so downstream milestones can be tested without live API keys.
- **Requirements covered**: LLM-001..011, NFR-003, NFR-004, NFR-005, STACK-005, TEST-001 (for this
  app's own logic).
- **Scope**: `LLMProvider`/`LLMModel`/`StageModelAssignment`/`LLMCallLog` models (token-first per
  D-008 — input/cached-input/output/total token fields; no pricing fields yet) + admin
  registration; `NormalizedLLMRequest`/`NormalizedLLMResult` types; adapter interface; retry policy
  module; typed error taxonomy + sanitizer; OpenAI, NVIDIA NIM, and Gemini adapters implementing
  D-005's schema-translation approach (**approved as specified**); a fake adapter for the
  deterministic automated test suite; **(v1.1 addition)** an explicit opt-in manual smoke-test
  harness for each real provider.
- **Out of scope**: wiring any pipeline stage to actually call this app yet (that happens in
  M3–M6); cassette/recorded-response testing (TEST-002, deferred); pricing/dollar-cost calculation
  (D-008 — deferred, not a blocker for this milestone).
- **Dependencies**: M1. D-005 and D-008 are both **approved**, so no part of this milestone is
  blocked on further decisions.
- **Expected files**: `llm_provider/models.py`, `llm_provider/adapters/{base,openai,nvidia,gemini,
  fake}.py`, `llm_provider/retry.py`, `llm_provider/errors.py`, `llm_provider/admin.py`,
  `llm_provider/tests/` (deterministic, no credentials), `llm_provider/smoke/` (opt-in manual
  scripts, one per provider).
- **Acceptance criteria — automated suite (must remain deterministic, must never require live API
  credentials)**: a call through the fake adapter produces exactly one `LLMCallLog` row with
  correct token/stage fields; retry-classification unit tests cover transient vs. non-transient
  errors and the "never retry after partial stream" rule; each of the three real adapters has at
  least a schema-translation unit test (no live API calls made); swapping a `StageModelAssignment`
  row changes which adapter a given stage would route to, verified by a unit test, with zero code
  change.
- **Acceptance criteria — manual opt-in provider smoke verification (v1.1 addition)**: for each
  configured provider (OpenAI, NVIDIA NIM, Gemini), a minimal structured-output request through the
  actual adapter, run only when the operator explicitly initiates it, using credentials from
  `.env`. This never runs automatically in CI or as part of `manage.py test`, never persists raw
  sensitive provider request/response bodies, and records safe `LLMCallLog` metadata where
  appropriate. A provider with no configured credential is reported as **not live-verified**
  rather than failing the deterministic suite — mocked-test success alone must never be reported
  as "this provider is operationally verified."
- **Verification**: `manage.py test llm_provider` passing (deterministic, no credentials); code
  review confirms no pipeline app imports a provider SDK directly; manual smoke-test run log for
  whichever providers the operator has configured.
- **Completion evidence**: passing test output + admin screenshot/manual check of registry CRUD +
  the manual smoke-test run log (or an explicit "not live-verified" note per provider).
- **Risks**: Gemini/NVIDIA schema quirks (LLM-007) may reveal gaps in this design only once the
  manual smoke tests are actually run against live providers — flag and revisit D-005 if so,
  recording any correction in DECISIONS.md rather than silently changing behavior.

## M3 — Candidate Memory build and confirmation workflow

- **Objective**: the `candidate_memory` app fully implemented — explicit bootstrap from the three
  operator-approved source files, through content classification, conflict detection, confirmed
  and versioned `CandidateMemory`, the ongoing-update workflow, and the full Candidate Memory UI
  (D-015).
- **Requirements covered**: MEM-001..006, PIPE-001, GOAL-003 (first review checkpoint), D-015 in
  full (bootstrap sources/precedence, canonical English + German expressions, evidence/constraint/
  positioning classification, multi-provenance supports, contradiction detection, explicit
  management-command bootstrap, ongoing UI updates, snapshot generation, bounded retrieval).
- **Scope**:
  - **Models**: `CandidateMemory` (UUID, version, `status`, `base_revision`, `activated_at`,
    `build_summary`), `MemorySourceDocument` (`logical_source_key`, `source_role`, `language`,
    `trust_status`, `precedence`, `content_sha256`, `unchanged_from`), `MemoryClaim` (`claim_id`,
    `stable_key`, `canonical_text` in English, `claim_type`, `subject_scope`, `experience_level`,
    `resume_eligible`, `confirmation_status` incl. `BLOCKED_CONFLICT`, `duplicate_group_key`,
    optional `valid_from`/`valid_to`), `MemoryClaimSupport` (claim FK, source-document FK, exact
    quotation, start/end line, quotation hash, source language, support role), `CandidateRule`
    (rule type, text, source, optional scope), `MemoryConflict` (conflict key, description,
    involved claims/supports, status, operator resolution, optional resolved claim) — all per
    `docs/ARCHITECTURE.md` §4/§8/§9 (D-015).
  - **Bootstrap command**: `bootstrap_candidate_memory --primary <path> --english <path> --german
    <path>` (name/args may be refined during implementation) — imports the three named committed
    files (`docs/AC/AC-MEMORY_PROFILE.md`, `docs/AC/AC-profile_english.md`,
    `docs/AC/AC-profile_german.md`), stores exact immutable content + `content_sha256`, classifies
    content into evidence/constraint/positioning planes (§8), extracts atomic canonical English
    `MemoryClaim`s, attaches one or more `MemoryClaimSupport` rows per claim, groups duplicate
    English/German expressions via `duplicate_group_key`, detects contradictions
    (`MemoryConflict`, `OPEN`) before activation, auto-confirms only non-conflicting
    resume-eligible factual claims that (a) originate from an `OPERATOR_APPROVED` source, (b) pass
    schema validation, (c) pass exact provenance validation (`MemoryClaimSupport` quotation/hash
    check), and (d) pass classification validation (evidence-plane only); blocks conflicting claims
    as `BLOCKED_CONFLICT`; leaves the revision in `NEEDS_REVIEW` for the operator to review
    conflicts and build statistics in the UI; a separate, explicit operator action (not the command
    itself) activates the revision (`status → ACTIVE`) — see the lifecycle/activation rules in
    `docs/ARCHITECTURE.md` §4 (`CandidateMemory`): activation is blocked if any validation failure
    could let unsupported/misclassified content become eligible, but an unresolved
    `MemoryConflict` does **not** by itself block activation as long as every claim it affects
    remains `BLOCKED_CONFLICT` — the activation UI warns the operator when this is the case; the
    human-readable snapshot (`docs/CANDIDATE_MEMORY_SNAPSHOT.md`-shaped export) is generated from
    the activated DB revision, not the other way around, and generating it is a read-only,
    deterministic export that never mutates the revision it reads from. **Does not run on
    `manage.py migrate`, `runserver`, `manage.py test`, or any startup path** — it is invoked
    explicitly by the operator, per D-015.
  - **Ongoing-update workflow** (UI, D-015): "Add experience or update profile" action **always
    creates a new working revision based on the current `ACTIVE` one** — it is the only way to
    change anything once a revision is `ACTIVE`, per the lifecycle rules in
    `docs/ARCHITECTURE.md` §4. Concretely: operator enters/uploads a new factual statement
    (context, employer/project, experience level, dates, actions, results, metrics) → stored as an
    immutable `OPERATOR_UPDATE` `MemorySourceDocument` → a new `CandidateMemory` revision is
    created in `status = BUILDING`, with `base_revision` pointing at the current `ACTIVE` one →
    unchanged documents/claims are reused (D-002, keyed via `logical_source_key` +
    `unchanged_from`) → only new/changed material is processed → the new revision moves to
    `NEEDS_REVIEW` and new conflicts surface in the UI → operator confirms/resolves within this
    new (still non-`ACTIVE`) revision → operator explicitly activates the new revision (subject to
    the same activation preconditions as bootstrap) → snapshot regenerates → the **prior** `ACTIVE`
    revision becomes `SUPERSEDED` and, like all revisions once they leave `NEEDS_REVIEW`, remains
    immutable and available for audit/rollback. Direct database editing is not the normal update
    path.
  - **Candidate Memory UI**: overview + active revision (read-only view — see below); source
    registry (filename, `source_role`, `language`, `trust_status`, `content_sha256`); build summary
    counts; searchable/filterable claims (by `subject_scope`, `claim_type`, `experience_level`,
    `confirmation_status`); claim detail showing every `MemoryClaimSupport` (exact quotation + line
    range); conflict-resolution inbox; confirm/correct/retire/restore actions; explicit revision
    activation; add/update profile workflow; snapshot export/regeneration action. **The
    confirm/correct/retire/restore actions and the conflict-resolution inbox operate only on a
    revision whose `status` is `BUILDING` or `NEEDS_REVIEW` (the current "working" revision) — the
    UI must not expose them against a revision whose `status` is `ACTIVE` or `SUPERSEDED`. The
    active revision's own screens are read-only: viewable and searchable, never editable in
    place.** Snapshot export/regeneration, when run against the active revision, is a deterministic
    read-only operation and must not alter that revision's stored content. Server-rendered Django
    throughout (STACK-004) — no SPA.
- **Out of scope**: retrieval by Agent Candidate (M5 consumes this app's output, doesn't build
  it); any vector database or embedding-based retrieval (D-015 — not required for v1); a stored
  translation table (optional per D-015 — v1 default is translate-on-demand for selected claims
  at draft time, in `resume_builder`, not a `candidate_memory` build-time responsibility).
- **Dependencies**: M2. D-002, D-003, and D-015 are all **approved**, so this milestone is not
  blocked on further decisions. The three source files already exist in the repository
  (`docs/AC/*.md`, committed) — no additional input is needed to begin.
- **Expected files**: `candidate_memory/models.py`, `.../management/commands/
  bootstrap_candidate_memory.py`, `.../services/classify.py` (evidence/constraint/positioning),
  `.../services/extract.py` (canonical claim + support extraction), `.../services/conflicts.py`,
  `.../services/revision.py` (content-hash comparison and carry-forward logic),
  `.../services/snapshot_export.py`, `.../views.py`, `.../templates/candidate_memory/*.html`,
  `.../tests/`.
- **Acceptance criteria**: running the bootstrap command against the three real source files
  produces a `NEEDS_REVIEW` revision whose `MemoryClaim`s each have at least one
  `MemoryClaimSupport` with an exact quotation resolvable to its `start_line`/`end_line` in the
  named source; every claim's `canonical_text` is English even when its support is a German
  passage; a fixture claim built from two duplicate EN/DE expressions shares one
  `duplicate_group_key`; a fixture positioning-plane sentence (e.g. a suggested target title) is
  never stored as a `MemoryClaim`; a fixture constraint-plane sentence (e.g. "currently learning
  Go") is stored as a `CandidateRule`, not a resume-eligible claim; a fixture with two
  contradicting claims produces an `OPEN` `MemoryConflict` and both claims read
  `BLOCKED_CONFLICT`, excluded from retrieval until resolved; a non-conflicting, well-supported,
  correctly classified claim from an `OPERATOR_APPROVED` source is auto-`confirmed`; a fixture
  extraction that is unsupported, malformed, or misclassified remains `unconfirmed`/rejected even
  though its source is `OPERATOR_APPROVED` (source approval is not extraction approval); supplying
  an **unchanged** source document as part of a new revision does not trigger re-extraction;
  supplying a **changed** or **new** document reprocesses only that document; a new
  `OPERATOR_UPDATE` through the UI creates a new revision, not an edit to the active one; prior
  `CandidateMemory` revisions remain untouched; the generated snapshot excludes any internal
  planning/positioning content per `docs/CANDIDATE_MEMORY_SNAPSHOT.md`'s own contract.
  **Lifecycle/activation acceptance criteria (planned, not yet implemented):**
  - an `ACTIVE` (or `SUPERSEDED`) revision's `MemoryClaim`, `MemoryClaimSupport`, `CandidateRule`,
    and `MemoryConflict` rows cannot be edited through the UI or service layer — attempting to
    confirm/correct/retire/restore a claim or resolve a conflict on such a revision is rejected;
  - using "Add experience or update profile" against an `ACTIVE` revision always creates a new
    `BUILDING` revision (`base_revision` pointing at the current `ACTIVE` one) rather than
    mutating it;
  - activating a revision atomically flips the previously-`ACTIVE` revision to `SUPERSEDED` in the
    same operation — never leaving two revisions `ACTIVE` even transiently;
  - a database-level/service-level invariant guarantees **at most one** `ACTIVE` revision at any
    time;
  - a fixture revision with an unresolved `MemoryConflict` may still be activated **only if** every
    claim it affects is `BLOCKED_CONFLICT` (unconfirmed/ineligible) at activation time — the
    activation view surfaces an explicit warning listing the excluded conflicts/claims;
  - resolving a `MemoryConflict` that was left open on an already-`ACTIVE` revision requires
    creating a new revision — there is no code path that mutates `MemoryConflict.status` on an
    `ACTIVE` revision directly;
  - a revision with a failing provenance validation (`MemoryClaimSupport` quotation/hash mismatch)
    or failing classification/eligibility validation cannot be activated until fixed.
  These are acceptance criteria to build toward in M3 — none of them are implemented yet.
- **Verification**: `manage.py test candidate_memory` passing; manual walkthrough — run the
  bootstrap command against the three real committed files, review the resulting claims,
  conflicts, and build summary in the UI, resolve at least one conflict, activate the revision,
  regenerate the snapshot, then create a second revision via one operator update and confirm
  unchanged content was reused.
- **Completion evidence**: passing tests + a recorded manual walkthrough note in
  `docs/CURRENT_STATE.md`.
- **Risks**: memory-build/classification prompt quality (correctly separating evidence from
  constraint/positioning content, and getting section-keyed atomic claims rather than one blob) is
  a real risk — expect prompt iteration; this is exactly why TEST-002 (cassette tests) is
  deliberately deferred until this stabilizes. The claim-identity comparison for D-002's
  carry-forward logic is a real correctness risk in its own right — an incorrect "unchanged" match
  would silently carry forward confirmation that shouldn't be trusted, so this comparison should
  err conservative (require reconfirmation) whenever it's ambiguous. The source corpora
  (`AC-profile_english.md`/`AC-profile_german.md`) are themselves consolidated tailored-resume
  inputs full of alternative titles, alternative summaries, and per-job instructions — the
  classification step has real work to do separating genuine evidence from the positioning noise
  that dominates those two files by volume; treat any evidence/positioning misclassification found
  during manual review as a correctness bug, not a style nitpick.

## M4 — Agent Jobber and job intake

- **Objective**: the `job_intake` app fully implemented — URL/paste intake through structured
  `JobRequirementAnalysis` with stable `JobRequirement` IDs; a `JobApplication` row created/
  populated for each intake.
- **Requirements covered**: AJ-001..006, D-014's AJ portion, `JobApplication` population
  (D-012)/dashboard `NEW`→`ANALYSIS` transition (requirements.md §17).
- **Scope**: `JobRequirementAnalysis` model + child `JobRequirement` rows (stable `JR-001`, ...
  IDs, categorized); intake view (URL or pasted text) creating/attaching to a `JobApplication`;
  fetch service with an extraction library **selected and documented at this milestone** (D-004 —
  approved in principle, library choice deferred to here) and best-effort/fallback logic; AJ LLM
  call via `llm_provider`.
- **Out of scope**: Agent Candidate/matching (M5); headless-browser fetching (FUT-005, deferred);
  the dashboard's own list/detail views (M7 scope) — this milestone only needs `JobApplication`
  rows to exist and advance `pipeline_phase` correctly, not the dashboard UI itself.
- **Dependencies**: M2 only — **not M3** (Agent Jobber does not read `CandidateMemory`; corrected
  per the dependency graph above). D-004's library choice and D-014 are both unblocked (approved).
- **Expected files**: `job_intake/models.py`, `.../services/fetch.py`, `.../services/analyze.py`,
  `.../views.py`, `.../templates/job_intake/*.html`, `.../tests/`; `job_applications/models.py`
  populated with real fields if not already done at M1.
- **Acceptance criteria**: pasted-text intake always works; a simulated fetch failure (e.g. a
  fixture URL returning an unparseable/too-short body) surfaces a clear UI error and lets the
  operator fall back to pasting; `JobRequirementAnalysis` always stores `source_type` + original
  raw input regardless of path taken; every material requirement gets a stable `JR-xxx` ID and
  category; output schema validates against the AJ Pydantic model; a new `JobApplication` is
  created (or attached to) on intake with `pipeline_phase = NEW` moving to `ANALYSIS`; language
  handling (AJ-002) is verified manually with at least one non-English fixture posting.
- **Verification**: `manage.py test job_intake` passing; manual test with one real URL and one
  pasted posting, plus one deliberately broken URL to confirm fallback UX.
- **Completion evidence**: passing tests + manual walkthrough note.
- **Risks**: real-world fetch reliability is inherently variable (requirements.md calls this an
  "ongoing maintenance concern," not a one-time build) — acceptance criteria test the fallback
  path, not a guarantee that fetching succeeds against arbitrary job boards.

## M5 — Agent Candidate, matching, and Human Review Gate 1

- **Objective**: `candidate_matching` and the gate-relevant parts of `reviews` fully implemented.
- **Requirements covered**: AC-001..003, HITL-001, HITL-002, HITL-004, HITL-005, HITL-006,
  NFR-002, D-014's AC portion, D-012's `ANALYSIS`→`PREPARATION` transition on Gate-1 approval.
- **Scope**: `FitAssessment` model + child `RequirementAssessment` rows (one per relevant
  `JobRequirement`, disposition `MATCH`/`PARTIAL`/`GAP`/`UNKNOWN` per D-014); retrieval service
  (confirmed **and** `resume_eligible` claims relevant to one `JobRequirementAnalysis`, from the
  `ACTIVE` `CandidateMemory` revision only, plus applicable `CandidateRule`s — never the complete
  source documents or the snapshot, per D-015's runtime-context boundaries in
  `docs/ARCHITECTURE.md` §9); AC LLM call; disposition-coverage
  validator (every relevant requirement has exactly one disposition; `MATCH`/`PARTIAL` require
  confirmed-claim evidence; `GAP`/`UNKNOWN` never silently disappear) satisfying NFR-002/D-014;
  `ReviewFeedback` model; generic approve/feedback view logic in `reviews`; Gate 1 combined AJ+AC
  template rendering per-requirement dispositions; re-run wiring for feedback targeting AJ or AC
  (creating new versions per D-010, **approved**).
- **Out of scope**: Agent Builder (M6); freshness/staleness checks against a *downstream* artifact
  (HITL-007 is fully exercised only once `resume_builder` exists — groundwork here is limited to
  storing `based_on_jra_id` and comparing it against `JobApplication.current_jra_id` per D-006).
- **Dependencies**: M3, M4 (both, per the corrected dependency graph — M5 is the convergence
  point). D-010 and D-014 are both approved, so this milestone is not blocked on further
  decisions.
- **Expected files**: `candidate_matching/models.py`, `.../services/retrieve.py`,
  `.../services/assess.py`, `.../validators/disposition_coverage.py`, `reviews/models.py`,
  `reviews/views.py`, `.../templates/reviews/gate1.html`, tests in both apps.
- **Acceptance criteria**: retrieval excludes unconfirmed/retired claims and claims unrelated to
  the job (verified with a fixture memory containing an obviously irrelevant claim); every
  relevant `JobRequirement` in a fixture receives exactly one `RequirementAssessment`; a fixture
  with a known `GAP` always surfaces that gap in the rendered output (no softening to `MATCH`);
  a `MATCH`/`PARTIAL` with no supporting confirmed claim is rejected by the validator; Gate 1
  shows AJ + AC output together, including per-requirement dispositions; approving flips status to
  proceed-ready and advances `JobApplication.pipeline_phase` to `PREPARATION`; submitting feedback
  writes a `ReviewFeedback` row, flips the targeted record to `needs_rework`, and creates a new
  version on re-run (D-010) without any in-memory/paused process involved (verified by restarting
  the dev server mid-"pause" and confirming state survives).
- **Verification**: `manage.py test candidate_matching reviews` passing; manual walkthrough of
  Gate 1 approve path and feedback-to-AC re-run path.
- **Completion evidence**: passing tests + manual walkthrough note.
- **Risks**: no-concealment is a prompting *and* validation problem — the disposition-coverage
  validator alone can catch an omitted known-gap fixture in tests and structurally guarantees
  every requirement gets *some* disposition, but real-world under-reporting via an overly
  generous `MATCH`/`PARTIAL` explanation is a quality risk to watch during manual review, not
  something a unit test can fully guarantee.

## M6 — Agent Builder and Human Review Gate 2

- **Objective**: `resume_builder` fully implemented — draft generation through the final markdown
  deliverable.
- **Requirements covered**: AB-001..004, HITL-003, HITL-007 (fully exercised here), NFR-001,
  D-014's AB portion, D-012's `PREPARATION`→`READY` transition on Gate-2 approval.
- **Scope**: `ResumeDraft` model + structured `ResumeElement`s (per
  `docs/RESUME_OUTPUT_STRUCTURE.md`); AB LLM call assembling JRA + FitAssessment + referenced
  MemoryClaims into the structured representation (target positioning, summary, experience,
  strengths, achievements, skills, certifications, languages, positioning guidance); the
  no-fabrication validator running against that **structured** data (evidence-attachment/
  eligibility check per D-014 — not text/embedding similarity); the markdown renderer (runs only
  after validation passes); Gate 2 rendered-markdown view; feedback-triggered regeneration (new
  version per D-010); freshness check against `JobApplication.current_fit_assessment_id` (D-006).
- **Out of scope**: PDF/DOCX rendering (permanently out of v1 scope, NG-001/AB-004).
- **Dependencies**: M5. **Previously blocking, now resolved**: the resume template question
  (D-007/FUT-001) is resolved via `docs/RESUME_OUTPUT_STRUCTURE.md` — this milestone is no longer
  blocked on undefined content structure. D-006, D-010, and D-014 are all approved.
- **Expected files**: `resume_builder/models.py`, `.../services/build.py`,
  `.../validators/no_fabrication.py`, `.../rendering/markdown.py`, `.../views.py`,
  `.../templates/resume_builder/*.html`, `.../tests/`.
- **Acceptance criteria**: a fixture structured draft containing a `ResumeElement` with no
  `supporting_memory_claim_ids`, or with an ID that doesn't resolve to an existing/confirmed/
  eligible `MemoryClaim`, is rejected by the validator before markdown is ever rendered; a valid
  fixture renders exactly the markdown structure in `docs/RESUME_OUTPUT_STRUCTURE.md` §4 (target
  title, summary, experience, skills, certifications, languages — internal planning sections like
  title alternatives and positioning notes are not rendered); Gate 2 renders the draft as rendered
  markdown (not raw source); feedback triggers a new draft version incorporating it; approving
  flags that version `confirmed`, sets `confirmed_at`, and advances `JobApplication.pipeline_phase`
  to `READY`; mutating the underlying `FitAssessment` (changing `JobApplication.current_fit_
  assessment_id`) after a draft exists causes the next view of that draft to show a staleness
  notice rather than silently allowing further action.
- **Verification**: `manage.py test resume_builder` passing; manual end-to-end walkthrough
  producing one real markdown resume from a real job posting and a real (test) CandidateMemory.
- **Completion evidence**: passing tests + the produced sample markdown resume + manual walkthrough
  note.
- **Risks**: this is the highest-stakes milestone for NFR-001 — per D-014, the validator's job is
  evidence attachment/eligibility (does the cited claim ID exist, is it confirmed, is it in
  scope), explicitly **not** text-similarity matching, so it cannot by itself catch a case where
  the LLM cites a real, confirmed, in-scope claim ID but writes wording that misrepresents it —
  that residual risk is exactly why Gate 2 human review of wording is still mandatory, not a
  redundant step.

## M7 — Integrated per-job workflow and markdown deliverable

- **Objective**: wire M3–M6 into one coherent per-job-application flow through `job_applications`,
  full HITL-007 enforcement across the whole chain, and **the job-application tracking dashboard**
  (requirements.md §17).
- **Requirements covered**: GOAL-001 (end-to-end), HITL-007 (chain-wide), the dashboard requirement
  (requirements.md §17), D-011's lifecycle behavior.
- **Scope**: the operator-facing "start a new job application" flow through to a downloadable/
  copyable final markdown file; cross-app freshness enforcement at every step boundary, not just
  the one pair exercised in M6; the dashboard list/detail views — company/title/dashboard-status/
  pipeline_phase/application_outcome/dates/AJ-AC-draft-existence-and-currency/review-required/
  staleness columns, opening a `JobApplication` to resume work from its actual current state, and
  the operator action to set `application_outcome` (`APPLIED`/`INTERVIEWING`/`REJECTED`).
- **Out of scope**: any new agent capability — this milestone is integration, not new pipeline
  logic.
- **Dependencies**: M3, M4, M5, M6.
- **Expected files**: top-level "job application" views/templates tying the four apps together;
  `job_applications/models.py` (fields, if not already scaffolded at M1); dashboard templates.
- **Acceptance criteria**: a full run — upload memory (if not already done) → intake a job →
  approve Gate 1 → approve Gate 2 → obtain a final markdown file — completes for at least one real
  job posting without manual DB intervention, with `pipeline_phase` advancing
  NEW→ANALYSIS→PREPARATION→READY automatically as that happens; a mid-pipeline edit (e.g. editing
  a confirmed FitAssessment after a draft exists) is caught by staleness checks at the next step,
  end to end; the dashboard lists this job application with an accurate derived status and lets
  the operator mark it `APPLIED`; marking it `APPLIED` and then regenerating the resume does not
  revert `application_outcome`.
- **Verification**: one full manual run recorded as evidence; automated tests covering the
  cross-app status-transition wiring and pipeline-phase/application-outcome transition rules (not
  LLM output quality, which stays manual per TEST-001).
- **Completion evidence**: the produced final resume file + a written run log in
  `docs/CURRENT_STATE.md` + a dashboard screenshot or template render showing the completed
  application.
- **Risks**: this is where gaps between milestone-level assumptions (e.g. what "current version"
  means across three versioned entities) tend to surface — treat any inconsistency found here as
  a signal to revisit the relevant DECISIONS.md entry, not to patch around it silently.

### Architecture review checkpoint (immediately after M7, before M8 quality work)

Per D-001: once this integrated workflow is functioning, explicitly revisit whether an
orchestration/agent framework (LangGraph, LangChain agents, OpenAI Agents SDK) is now justified —
only if concrete needs have appeared (dynamic agent routing, parallel branches, tool-calling
loops, long-running autonomous execution, resumability after process failure, agent-to-agent
delegation, significantly more pipeline stages, or complex conditional execution). Wanting better
observability is explicitly not sufficient justification on its own. Record the outcome of this
checkpoint as a new or superseding entry in `docs/DECISIONS.md`, whichever way it goes — including
"no change, defaults hold" if that's the honest outcome.

## M8 — v1 quality, error handling, observability, and acceptance review

- **Objective**: harden error paths, deliver token-consumption reporting (cost optional/deferred),
  close out remaining TEST-001
  coverage, and perform a full requirement-traceability review before calling v1 done.
- **Requirements covered**: NFR-003 (final review), NFR-004 (**token-first** reporting UI/query),
  remaining TEST-001 items across all apps, general error-path hardening implied by LLM-008/009 in
  real usage.
- **Scope**: a simple **token-consumption** report view (per-job-application and per-stage/
  provider/model breakdown, sourced entirely from `LLMCallLog` per NFR-004/D-008 — input/
  cached-input/output/total tokens; dollar cost omitted unless pricing metadata has since been
  added, which remains optional); hardening of error messages surfaced to the operator on provider
  failures; closing any TEST-001 gaps found during M1–M7; a full pass over
  `docs/REQUIREMENT_TRACEABILITY.md` updating every row to its true status.
- **Out of scope**: any new pipeline capability; TEST-002 (cassette suite, still deliberately
  deferred); PDF/DOCX (still out of v1); dollar-cost calculation (D-008, still optional/deferred).
- **Dependencies**: M1–M7, and the post-M7 architecture review checkpoint having been recorded
  (even if its outcome is "no change").
- **Expected files**: a reporting view/template in `llm_provider` (or a small top-level reporting
  module), updated tests across apps, updated `docs/REQUIREMENT_TRACEABILITY.md`.
- **Acceptance criteria**: the token-consumption report for a completed job application matches a
  manually computed total from its `LLMCallLog` rows; every requirement ID in the traceability
  matrix has a truthful, non-fabricated status; all TEST-001 items named in
  `docs/TEST_STRATEGY.md` have a corresponding passing test.
- **Verification**: full test suite passing; manual token-report cross-check; traceability review
  read against `requirements.md` one more time end to end.
- **Completion evidence**: passing full test suite + the reviewed traceability matrix + updated
  `docs/CURRENT_STATE.md` describing the true, final v1 state.
- **Risks**: the temptation at this stage is to mark things "done" for momentum — explicitly
  resist that; a requirement without real verification stays "Not implemented"/"Not verified," full
  stop.
