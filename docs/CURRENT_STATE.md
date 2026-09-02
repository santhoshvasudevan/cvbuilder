# Current State

Last updated: 2026-09-02 (M3 — Candidate Memory build, review, activation and update workflow
implemented and verified, then repaired against a read-only audit that found the deterministic
conflict detector was disconnected from the real extraction pipeline; **not** live-provider-
verified -- see the M3 verification and M3 post-audit-repair sections).

## Summary

This repository has completed **Milestones M1, M2, and M3**. M1 established the Django/PostgreSQL
application foundation (see git history for detail). M2 fully implements the `llm_provider` app.
M3 implements the `candidate_memory` app end to end: relational models with an enforced
`BUILDING -> NEEDS_REVIEW -> ACTIVE -> SUPERSEDED` lifecycle, an explicit bootstrap management
command, deterministic conflict detection, an operator-facing server-rendered UI, and a
deterministic snapshot export -- all routed through M2's `llm_provider` adapter interface, never a
provider SDK directly. Milestones M4 through M8 remain **not implemented** (confirmed untouched in
this session: `job_intake/`, `candidate_matching/`, `resume_builder/`, `reviews/`,
`job_applications/` show no changes in `git status`).

## What exists (M3 — new)

- **`docs/AC/AC-OPERATOR_FACT_RESOLUTIONS.md`** (new, operator-approved, dated 2026-09-02): the
  highest-precedence Candidate Memory source, recording the operator's resolution of specific
  factual conflicts in the existing corpora -- Ford client work began November 2017 (no invented
  end date); Continental client work was July 2015-October 2017 in Nuremberg, Germany (superseding
  "...-July 2017"/"Frankfurt am Main"); Maruti Suzuki began August 2012 (superseding September
  2012); German proficiency is B1 confirmed, B2 actively pursued but **not** attained; Ambigai
  Consultancy Services was the legal employer for the Ford/Continental client assignments, to be
  stored and presented separately from the client organization (default: client-centric). The
  three original source files (`AC-MEMORY_PROFILE.md`, `AC-profile_english.md`,
  `AC-profile_german.md`) were **not** edited -- superseded statements remain in place, resolved
  only via this new, higher-precedence source and the deterministic conflict detector.
- **`docs/CANDIDATE_MEMORY_SNAPSHOT.md`** updated to reflect the resolved facts above and to list
  the new operator-resolutions file first in its source manifest (still reference-only, still
  never re-ingested -- see its own header markers).
- **Models** (`candidate_memory/models.py`): `CandidateMemory` (lifecycle + a partial-unique DB
  constraint enforcing at most one `ACTIVE` revision at a time), `MemorySourceDocument` (immutable
  after creation -- `save()`/`delete()` raise on any attempted change), `MemoryClaim` (evidence
  plane; `structured_value` JSONField for deterministic conflict comparison;
  `legal_employer`/`client_organization`/`presentation_mode` for the legal-employer-vs-client
  split), `MemoryClaimSupport` (multi-support per claim; verifies its quotation against the exact
  source line range), `CandidateRule` (constraint/positioning planes -- never resume evidence),
  `MemoryConflict` (visible contradiction records; blocks every involved claim while `OPEN`). A
  shared `_RevisionScopedModel` guard on every content model raises `RevisionNotEditableError` on
  any write once the owning revision is `ACTIVE`/`SUPERSEDED` -- enforced at the model layer, so
  admin, shell, and direct ORM access are all blocked identically, not just service-layer callers.
  Two migrations applied cleanly against real local Postgres.
- **Admin** (`candidate_memory/admin.py`): all six models registered; a `_RevisionScopedAdminMixin`
  denies change/delete permission once the owning revision is frozen; `MemorySourceDocumentAdmin`
  always denies change/delete (immutable from creation).
- **Pydantic contracts** (`candidate_memory/schemas.py`): `ContentPlane`
  (`EVIDENCE`/`CONSTRAINT`/`POSITIONING`), `ExperienceLevel`, `RuleType`, `SourcePassage`,
  `ExtractedItem` (one unified extraction contract discriminated by `plane`, carrying
  `legal_employer`/`client_organization` for the operator-resolution split),
  `ChunkExtractionResult`, `ConflictCandidate` (reserved for a future optional LLM-assisted pass --
  not used by M3's deterministic detector), `ChunkClassificationOnly`.
- **Service layer** (`candidate_memory/services/`): `chunking.py` (bounded ~80-line chunks,
  original line numbers preserved for provenance); `extraction.py` (calls
  `llm_provider.adapters.get_adapter_for_stage(MEMORY_BUILD)` only -- no provider SDK import
  anywhere in this app, verified by grep); `classification.py` (plane validation, including a
  schema-level experience-level-inflation check: an `AWARENESS`/`LEARNING` item using
  delivery/production-grade phrasing is rejected, not just prompted against);
  `storage.py` (quote/hash/line provenance validation, duplicate/group EN+DE merging into
  multi-support claims, legal-employer/client wiring); `conflicts.py` (deterministic detection for
  `employment_dates`/`employment_location`/`language_proficiency`; auto-resolves via
  operator-update source precedence, recording a visible `RESOLVED` `MemoryConflict`, or leaves a
  genuinely unresolved conflict `OPEN` and blocks every involved claim); `confirmation.py`
  (auto-confirmation requires operator-approved source + valid hash/quote/line + resume-eligible +
  no blocking conflict -- source approval alone is never sufficient); `revision.py` (creates a new
  working revision; carries a claim forward only if *every* one of its supports comes from a
  source unchanged this round -- conservative, all-or-nothing); `lifecycle.py` (confirm/retire/
  restore/correct/resolve-conflict/dismiss-conflict, all revision-mutability-gated;
  `activation_blockers`/`activation_warnings`/`activate_revision`, the last transactional with
  `select_for_update()` plus the DB partial-unique-index backstop); `snapshot_export.py`
  (deterministic markdown generation from the `ACTIVE` revision only; never mutates it);
  `bootstrap.py` (`build_revision` for file-based sources, `build_revision_from_operator_text` for
  the UI's "add experience / update profile" workflow -- both delegate to one
  `build_revision_from_sources` core that decides carry-forward: any previous source not
  re-supplied this round is carried forward automatically, any re-supplied source is only
  reprocessed if its content hash actually changed).
- **Management commands**: `bootstrap_candidate_memory` (`--primary --english --german
  --operator-update`, repeatable; refuses to import the snapshot file; never auto-activates) and
  `export_candidate_memory_snapshot` (`--output`, optional). Neither runs automatically -- not on
  `migrate`, `runserver`, or `manage.py test`.
- **UI** (`candidate_memory/views.py`/`urls.py`/`templates/candidate_memory/*.html`, mounted at
  `/candidate-memory/` in `config/urls.py`): overview (active + working revision), revision detail
  (source registry, build summary, content counts), searchable/filterable claims list (subject
  scope, claim type, experience level, confirmation status, eligibility), claim detail (every
  supporting quotation with its exact source and line range, plus confirm/retire/restore/correct
  actions), candidate-rules list, conflict-resolution inbox (resolve/dismiss), an activation
  confirmation screen surfacing blockers and warnings before the operator commits, a read-only
  render for `ACTIVE`/`SUPERSEDED` revisions, an "add experience / update profile" form, and a
  snapshot-export action. Every state-changing view is `@require_POST` (CSRF-protected via
  Django's standard middleware) and delegates to `services/lifecycle.py` rather than touching
  models directly.
- **Automated test suite**: 119 new deterministic tests in `candidate_memory/tests/` (160 total
  across the whole project), all routed through the M2 `FakeAdapter` -- zero credentials, zero
  network calls, zero cassette/browser-automation/vector infrastructure. Covers: lifecycle
  transitions and immutability (including simulated direct-admin-style saves), bounded chunking,
  content-plane classification (evidence/constraint/positioning, title-vs-fact, experience-level
  non-inflation), provenance/duplicate-grouping storage, the named operator-resolution conflict
  scenarios (Ford/Continental/Maruti/German B1-B2) plus a genuine unresolved-conflict case,
  auto-confirmation policy, revision carry-forward (all-or-nothing), the full lifecycle service
  (including exactly-one-active-revision and activation blockers vs. warnings), bootstrap
  orchestration (unchanged-source reuse, changed-source reprocessing, the operator-text-update
  workflow, snapshot-import refusal), snapshot generation/export, legal-employer/client-
  organization separation, and the UI (authorization by revision state, POST-only/CSRF enforcement,
  filters).

## M3 post-audit repair (2026-09-02)

A read-only audit of the uncommitted M3 implementation found one release-blocking defect and
several proportionate follow-ups, all now fixed:

- **Release blocker, fixed**: `MemoryClaim.structured_value` was never actually populated by the
  real extraction/storage path (`schemas.ExtractedItem` had no field for it, `storage._store_claim`
  never wrote it), so `services/conflicts.py`'s deterministic detector could never fire on real
  data -- only on hand-built test fixtures that bypassed storage entirely. Fixed end to end:
  `schemas.py` gained typed `EmploymentDatesValue`/`EmploymentLocationValue`/
  `LanguageProficiencyValue` payloads (explicit fields on `ExtractedItem`, not a loose dict, so
  OpenAI's strict-schema translation stays valid); `services/comparable_values.py` is the new
  single source of truth for validating them and computing a fail-closed `structural_key()` (a
  claim whose structured data is missing/malformed gets a key unique to its own primary key, so it
  can never spuriously agree *or* conflict with a sibling claim -- it just stays excluded from
  grouping, `UNCONFIRMED`, and counted in `build_summary["structured_value_issues"]`);
  `storage.py`, `confirmation.py`, `conflicts.py`, and `services/lifecycle.py::activation_blockers`
  all now consume this. A second, deeper bug was found while proving this end to end:
  `storage.duplicate_group_key()`'s scope+type fallback silently merged two *different* candidate
  values sharing an employer/claim-type into one claim's supports (so a stale corpus date range
  and a corrected operator-update date range never became two comparable claims at all) --
  comparable-claim-type items now never use that coarse fallback unless the model explicitly
  supplies a `duplicate_group_hint`. New end-to-end tests
  (`tests/test_conflict_pipeline.py`, 8 tests) drive every named operator-resolution scenario
  (Continental dates, Continental location, Maruti dates, German B1/B2, Ford no-invented-end,
  missing-data-fail-closed, precedence-scoped-to-only-the-matching-fact) through
  `build_revision`/`build_revision_from_operator_text` with scripted `FakeAdapter` responses --
  never a hand-built `MemoryClaim` row.
- **Bootstrap idempotency/recovery, added**: a fourth `CandidateMemory.Status`, `FAILED`
  (migration `0003_alter_candidatememory_status`; D-016, **PROPOSED**, not yet product-owner-
  approved -- see `docs/DECISIONS.md`), reachable from `BUILDING`/`NEEDS_REVIEW`, frozen like
  `SUPERSEDED`. `build_revision_from_sources` now refuses outright
  (`ExistingWorkingRevisionError`) if a working revision already exists, rather than silently
  creating a second, orphaned one; `--abandon-existing` (CLI) / an "abandon this working revision"
  UI action / `services.revision.abandon_revision()` mark the existing one `FAILED` and proceed,
  as an explicit, auditable operator action. An unexpected failure mid-build (not the
  already-tallied expected per-chunk extraction errors) now marks the new revision `FAILED` with a
  recorded `failure_reason`, instead of leaving it stuck at `BUILDING` looking indistinguishable
  from an in-progress build -- still no single long-held `transaction.atomic()` around the whole
  build (would hold DB locks across dozens of provider calls); each write still commits as it
  happens. 6 new tests (`tests/test_bootstrap_recovery.py`) cover duplicate invocation,
  partial-multi-chunk failure, retry-after-failure, and zero impact on the `ACTIVE` revision.
- **Dry-run preflight, added**: `bootstrap_candidate_memory --dry-run` (new `services/preflight.py`)
  reports resolved filenames/roles/hashes, char/line/chunk counts (using the exact same
  `chunk_source()` calculation the real build uses), an approximate/labeled token estimate, the
  configured `MEMORY_BUILD` provider/model, whether its credential env var appears set (value
  never printed), any existing working revision, and whether a live call would occur -- with zero
  provider calls and zero database writes (10 tests, `tests/test_preflight.py`).
  `MemorySourceDocument`/`CandidateMemory` are never created during a dry run.
- **Bounded chunking, fixed**: `chunking.chunk_source()` was bounded only by line count -- a
  single pathologically long line (or many moderately long ones) could still produce an unbounded
  chunk. Added a configurable character bound alongside the line bound; a single line exceeding it
  is safely split into multiple fragments, each still tagged with its original line number (exact
  provenance is unaffected, since `_verify_quote_at_lines` always re-derives the excerpt from the
  source document's own immutable content at that line number, never from a chunk's rendered
  text). 9 tests including one extremely long line, multibyte Unicode, and exact line-range
  preservation.
- **Prompt hardening, added**: the extraction system prompt now explicitly delimits the source
  excerpt (`<source_excerpt>...</source_excerpt>`) as candidate-provided data to classify, states
  that instruction-like text inside it must never override the system task, and that unsupported
  fields stay null rather than being invented. Documented honestly as one defense-in-depth layer,
  not a claim that this makes hostile content perfectly safe -- the real backstop remains schema
  validation plus `storage.py`'s exact-quote provenance check, proven by a new test that scripts a
  "successfully injected" fabricated-but-schema-conformant response and confirms it is still never
  stored, because its quote does not resolve against the real source content (4 tests,
  `tests/test_extraction.py`).
- **`presentation_mode`, made functional**: it was stored, editable, and carried forward across
  revisions, but nothing read it -- `snapshot_export.py` always rendered the same text regardless
  of the operator's choice. Now `CLIENT_CENTRIC` (default) shows the client organization with a
  "client assignment" indicator and never names the legal employer; `LEGAL_EMPLOYER_EXPLICIT`
  names the legal employer plus the client assignment; `COMBINED` shows both. All three modes read
  the same underlying `legal_employer`/`client_organization` columns -- switching mode is a
  display choice only and never mutates them (5 new tests confirm Ambigai never disappears from
  PostgreSQL regardless of mode).
- **Atomic snapshot export**: `export_snapshot()` now writes to a temporary file in the
  destination directory, flushes and `fsync`s it, then swaps it into place with `os.replace()`
  (atomic on POSIX and Windows), cleaning up the temp file on any failure -- a crash mid-write can
  no longer leave a truncated snapshot, and the previous snapshot stays intact until the new one
  is fully written. 4 new tests including a simulated failure that leaves the prior snapshot
  uncorrupted.
- **Claim-deletion guard**: deleting a `MemoryClaim` referenced by any `MemoryConflict` (at any
  status) now raises `RevisionNotEditableError` rather than silently desyncing that conflict's
  `involved_claims` set from its frozen `description` text -- retirement
  (`services.lifecycle.retire_claim`) is the supported removal path.
- **Documented, not implemented (proportionate, explicitly out of this repair's scope)**: there is
  still no way to retire/remove a source document from a future revision -- a source not
  re-supplied is always carried forward automatically, by design, so once added it can never be
  dropped. The conflict-resolution inbox still shows involved claim IDs/status with a link to each
  claim's detail page, not inline quotations on the inbox page itself. Database indexes remain
  limited to the existing unique constraints and Django's automatic FK indexes -- adequate at the
  expected single-operator scale, deliberately not expanded here.
- **Trust-boundary honesty correction**: "enforced at the model layer" claims about
  `ACTIVE`/`SUPERSEDED` immutability are accurate for instance `save()`/`delete()` calls --
  everything services, views, forms, and Django admin's edit/delete forms actually do -- but do
  **not** cover `QuerySet.update()`/`bulk_update()` or raw SQL, which bypass instance hooks
  entirely and are not guarded against. This repository does not claim database-enforced
  immutability (no trigger/constraint backs it) -- only application-layer immutability through the
  normal ORM instance API. Raw-SQL protection is not required by any current M3 decision.
- **Verification re-run after all of the above**: `manage.py test candidate_memory` -> 164/164
  passing (up from 119 pre-repair); full suite -> 205/205 (up from 160); `ruff check .` clean;
  migrations clean (`0003_alter_candidatememory_status` applied); `git diff --check` clean; no
  provider SDK import; no secret-shaped literal; a real `--dry-run` against the four actual
  `docs/AC/*.md` files (see below) made zero provider calls and zero writes. The three original AC
  source files remain byte-for-byte unchanged; `docs/.DS_Store`'s pre-existing, unrelated
  modification (not caused by this or the prior session) remains unstaged.
- Dev-database hygiene note: an earlier ad hoc `manage.py shell` debug session (used to diagnose a
  test-fixture line-range mismatch while repairing the conflict pipeline) briefly left one
  `CandidateMemory`, one `LLMProvider`/`LLMModel`/`StageModelAssignment`, and one `LLMCallLog` row
  in the real development database -- caught during this repair's own dry-run smoke test (an
  unexpectedly-reported "existing working revision") and fully cleaned up before proceeding; the
  development database was re-verified at zero rows across all affected tables afterward.

## M3 verification performed (2026-09-02, pre-audit-repair baseline)

- `manage.py makemigrations --check` reports no pending model changes; `manage.py migrate` applies
  cleanly (idempotently re-run) against the real local Postgres instance; `manage.py check` passes
  clean.
- `manage.py test candidate_memory` -> 119/119 passing. `manage.py test` (full suite) -> 160/160
  passing (up from 41 at M2). (Superseded by the post-audit-repair counts above.)
- `make lint` (`ruff check .`) passes with zero errors across the whole repository.
- `grep` confirms no OpenAI/NVIDIA/Gemini SDK import anywhere under `candidate_memory/` (the only
  match is a docstring reminder not to do so); `git diff --check` reports no whitespace errors.
- Confirmed via `git status` that `job_intake/`, `candidate_matching/`, `resume_builder/`,
  `reviews/`, and `job_applications/` (M4-M6) have zero changes -- M3 did not reach ahead of scope.
- A manual, deterministic Django-test-client walkthrough (via the automated test suite plus an
  ad hoc shell session) exercised every UI route end to end: overview, revision detail, claims
  list with each filter, claim detail, confirm/retire/restore/correct, the conflict inbox
  (resolve/dismiss), the activation confirmation screen (both blocked and successful activation),
  the read-only rendering of an `ACTIVE` revision, the "add experience / update profile" form, and
  snapshot export -- all against the `FakeAdapter`, explicitly **not** a live provider.
- **Not done, and reported honestly rather than implied**: the real four-source Candidate Memory
  (`AC-MEMORY_PROFILE.md` + English + German corpora + the new operator-resolutions file) has
  **not** been bootstrapped in this environment, because `.env`'s `OPENAI_API_KEY`/
  `NVIDIA_NIM_API_KEY`/`GEMINI_API_KEY` are all empty (confirmed by direct check, values never
  printed) -- exactly the state carried over from M2. Real extraction against the operator's
  actual profile is **NOT LIVE-VERIFIED**. Nothing in this session used the fake adapter to
  populate a Candidate Memory revision and describe it as real extraction; every fake-adapter run
  used synthetic fixture text, created inside a test transaction or an explicitly-labeled ad hoc
  script, and left no residue in the persistent development database.

## What exists (M2 — new)

- **Registry models** (`llm_provider/models.py`): `LLMProvider` (name, `provider_type`, `base_url`,
  `credential_env_var` -- never a credential value), `LLMModel` (capability flags:
  `supports_structured_output`/`supports_streaming`/`supports_reasoning`/`max_output_tokens`),
  `StageModelAssignment` (one row per pipeline stage, unique constraint enforces exactly one
  assignment per stage), `LLMCallLog` (token-first audit ledger: input/cached-input/output/total
  tokens, latency, retry count, sanitized error category/message -- no raw body fields exist on
  this model at all, so there is nothing to accidentally log unsanitized). All four registered in
  Django admin (`llm_provider/admin.py`); `LLMCallLog` is admin-read-only by design (an audit
  ledger is never hand-edited).
- **Normalized types** (`llm_provider/types.py`): `NormalizedLLMRequest`, `NormalizedLLMResult`,
  `TokenUsage`. **Error taxonomy** (`llm_provider/errors.py`): `LLMErrorCategory` (six categories
  per requirements.md Sec 9.5) and `sanitize_error_message()`, which strips body-shaped content
  and truncates before anything reaches `LLMCallLog` or a log line.
- **Retry policy** (`llm_provider/retry.py`): `execute_with_retry()` retries only transient
  categories (`RATE_LIMIT`/`TIMEOUT`/`PROVIDER_INTERNAL`), never retries once
  `partial_output_received` is set (regardless of category), and caps attempts with linear
  backoff. Sleep is injectable so tests never actually wait.
- **Schema translation** (`llm_provider/schema_translation.py`): `to_openai_strict_schema()`
  (keeps `$ref`/`$defs`/`enum`, adds `additionalProperties: false` recursively -- OpenAI and NIM
  both use this) and `to_gemini_schema()` (inlines `$ref`/`$defs`, strips `enum`, converts type
  names to Gemini's uppercase OpenAPI-subset dialect).
- **Adapters** (`llm_provider/adapters/`): `BaseLLMAdapter` (abstract -- owns the shared call
  path: retry, re-validation of the provider's raw dict against the canonical Pydantic
  `output_schema` via `model_validate()`, latency measurement, and the single `LLMCallLog` write);
  `OpenAIAdapter`, `NvidiaNimAdapter` (checks `supports_structured_output` before assuming NIM
  strict-schema support), `GeminiAdapter` (REST-only, never gRPC; defensive `thinkingConfig`
  handling), all three via plain REST (`requests`), no provider SDK dependency anywhere; and
  `FakeAdapter` for deterministic tests. `adapters/__init__.py` provides
  `get_adapter_for_stage(stage)`, the one routing function pipeline code will call from M3 onward.
- **Opt-in manual smoke tests** (`llm_provider/smoke/` + three management commands
  `smoke_test_openai`/`smoke_test_nvidia`/`smoke_test_gemini`): each checks for its provider's
  credential env var, prints `NOT LIVE-VERIFIED` and exits cleanly if absent (verified manually
  with all three credentials unset), otherwise makes one real structured-output call. Never
  invoked by `manage.py test` or any automated path.
- **Automated test suite**: 41 deterministic tests across `llm_provider/tests/` (registry CRUD +
  admin-changelist reachability, stage-routing incl. zero-code-change reassignment, retry
  classification matrix incl. the partial-stream rule, error sanitizer, fake-adapter
  `LLMCallLog` correctness, schema translation for all three real adapters, and
  credential/capability configuration guards) -- all pass with zero live credentials.

## M2 verification performed (2026-09-02)

- `manage.py makemigrations`/`migrate` applied `llm_provider.0001_initial` cleanly against the
  real local Postgres instance.
- `manage.py test` -> 41/41 passing (up from 0 at M1), zero live credentials used or required.
- `make check`/`make lint`/`make test` all pass repeatably.
- Manually ran all three smoke-test management commands with no credentials configured; each
  correctly reported `NOT LIVE-VERIFIED` and exited cleanly rather than failing or attempting a
  network call.
- `grep` across the repository confirms no pipeline app imports a provider SDK directly (there is
  no OpenAI/Google-GenerativeAI/Anthropic SDK import anywhere) and no secret-shaped literal exists
  in any tracked file.
- **Not yet done**: none of the three real adapters have been exercised against a live provider
  (no credentials were available in this environment). Per the M2 risk note in
  `docs/IMPLEMENTATION_PLAN.md`, this means LLM-007's per-provider quirk handling is verified only
  at the deterministic schema-translation level, not against real API behavior -- flagged
  honestly rather than claimed as fully proven.

## What does not exist

- Any model fields, migrations, views, or templates for `job_intake`, `candidate_matching`,
  `resume_builder`, `reviews`, or `job_applications` (Milestones M4-M7).
- Any pipeline stage other than Candidate Memory build (`MEMORY_BUILD`) actually calling
  `get_adapter_for_stage()` -- `AJ_ANALYZE`/`AC_MATCH`/`AB_BUILD` remain unused until M4/M5/M6.
- Any live-provider verification of the OpenAI/NVIDIA NIM/Gemini adapters (opt-in, operator-run,
  not performed in this environment -- no credentials configured), including for the M3 Candidate
  Memory extraction path specifically -- see the M3 verification section above.
- A bootstrapped real Candidate Memory built from the operator's actual four source documents
  (only synthetic test fixtures have been processed in this environment).
- Any remote/CI configuration (not required; local quality commands remain the standard).

## Decisions (see `docs/DECISIONS.md` for full detail)

D-001 through D-015 are all APPROVED (several "with modification"); D-013 is superseded by D-010.
No decision remains blocking for any milestone through M7.

## Next action

Milestone M4 (Agent Jobber) or M5 (Agent Candidate) may proceed next per
`docs/IMPLEMENTATION_PLAN.md`'s dependency graph. Before either can produce a real result end to
end, a live LLM provider credential needs to be configured (M3's Candidate Memory bootstrap is
implemented but has only been run against the `FakeAdapter`); this is an operational prerequisite,
not a code blocker.

## Maintenance rule for this file

Update this file after any milestone completes or any meaningful implementation step lands.
Describe the repository as it truly is -- never mark something present, tested, or working that
has not actually been built and verified.
