# Current State

Last updated: 2026-09-03 (M3 Candidate Memory: the real four-source bootstrap has now been run
live against NVIDIA Nemotron and fully activated. It produced revision 1 (v1/id=6, preserved as
audit evidence, never activated -- see below) and, after targeted chunk/sentence-level recovery of
every truncated chunk (never raising `max_output_tokens` beyond model-supported bounds, never
re-enabling reasoning), revision 2 (v2/id=7), which passed activation validation with zero
blockers and was **activated via `services/lifecycle.py::activate_revision`** as the sole `ACTIVE`
CandidateMemory. M4 Agent Jobber/job intake is **implemented and committed** (audit corrections
applied: D-004 follow-up note, cascade-delete/bulk-ORM bypass disclosure, admin `pipeline_phase`
read-only).

## Summary

This repository has completed **Milestones M1, M2, and M3**, and has **implemented but not yet
committed Milestone M4**. M1 established the Django/PostgreSQL application foundation (see git
history for detail). M2 fully implements the `llm_provider` app. M3 implements the
`candidate_memory` app end to end: relational models with an enforced `BUILDING -> NEEDS_REVIEW ->
ACTIVE -> SUPERSEDED` lifecycle, an explicit bootstrap management command, deterministic conflict
detection, an operator-facing server-rendered UI, and a deterministic snapshot export -- all routed
through M2's `llm_provider` adapter interface, never a provider SDK directly; M3 has since been
live-qualified against NVIDIA Nemotron (see the M3 extraction-quality repair section), the real
four-source bootstrap has been run, and its recovered revision 2 (id=7) is now **ACTIVE** (see "The
real four-source bootstrap" section below) -- M3 is complete end to end, including a real
activation. M4 implements the `job_intake` app (Agent Jobber) and
the `job_applications.JobApplication` aggregate per `docs/IMPLEMENTATION_PLAN.md` -- implemented
and tested against the `FakeAdapter` only, **not yet committed** (staged for operator review with a
proposed commit message). Milestones M5 through M8 remain **not implemented** (confirmed untouched:
`candidate_matching/`, `resume_builder/`, `reviews/` show no changes in `git status`).

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

## M3 extraction-quality repair (2026-09-02, second pass -- NVIDIA qualification hardening)

Following the post-audit repair above, a live-provider qualification pass against NVIDIA Nemotron
found and fixed a semantic-completeness defect: extracted EVIDENCE items frequently left
`subject_scope`/`legal_employer`/`client_organization` null even when the source text clearly
supported them, which would have silently lost or misfiled real claims during the real bootstrap.
Fixed:

- **`candidate_memory/services/subject_scope.py`** (new): a deterministic, conservative
  `normalize_subject_scope()` that fills a genuinely missing/malformed `subject_scope` from the
  item's own `client_organization`/`legal_employer` (employment claims) or `language_proficiency`
  fields, using a documented canonical convention (`organization:<name>`, `language:<name>`,
  `skill:<name>`, `career`). It never overwrites an already-valid model-provided value (even a
  legacy plain-string one predating this convention) and never invents an employer/client/language
  from world knowledge -- only reshapes what the item's own extracted fields already state. Wired
  into `storage.store_extracted_item()` before classification validation.
- **`services/classification.py`**: two new fail-closed rules -- a CONSTRAINT/POSITIONING item
  with `resume_eligible=True` is now rejected (planes that become a `CandidateRule` can never be
  resume content); `legal_employer`/`client_organization` must be set together or not at all
  (exactly one set is treated as an inconsistent/invented split, never silently completed).
- **`schemas.py`**: `EmploymentLocationValue.city`/`LanguageProficiencyValue.language` now reject a
  blank string (previously any non-`None` string, including `""`, passed Pydantic's bare `str`
  typing).
- **`services/extraction.py`**'s `SYSTEM_PROMPT`: added explicit `subject_scope` convention
  documentation, `experience_level`/`resume_eligible` guidance, and one concise fictional worked
  example (Alex Doe / Fictional Consulting Group / Globex Corporation) covering the legal-employer/
  client split, language attained-vs-in-progress levels, and CONSTRAINT/POSITIONING planes
  together.
- 40 new deterministic tests (`candidate_memory/tests/test_subject_scope.py`,
  `test_comparable_values.py`, plus additions to `test_classification.py`, `test_storage.py`,
  `test_conflict_pipeline.py`, `test_extraction.py`) -- full suite 284/284 passing at the time,
  `ruff check .` clean, migrations clean.
- **One final authorized live NVIDIA qualification call** (identical fictional excerpt,
  `reasoning_enabled=false, temperature=1.0, top_p=0.95, max_output_tokens=4096`) confirmed the
  fix: every EVIDENCE item's `subject_scope` populated correctly (`organization:globex
  corporation`, `career`, `language:french`), `legal_employer`/`client_organization` correctly
  separated, CONSTRAINT/POSITIONING both correctly `resume_eligible=false`. Latency 8286ms, tokens
  2028 in/931 out/2959 total, `retry_count=0`, isolated-transaction storage pass rolled back with
  zero residue (`CandidateMemory`/`MemorySourceDocument` counts both 0 afterward), `LLMCallLog`
  count 4->5 (exactly the one authorized call).
- **Finding, since fixed** (D-017, now APPROVED AND IMPLEMENTED): that same live call's storage
  pass rejected 3 of 5 items at the provenance-verification step (not the semantic-extraction
  step) -- every rejected item's supporting sentence happened to word-wrap across two physical
  lines in the test excerpt, and Nemotron reconstructed the quote by joining the wrapped halves
  with a space where the source has an actual newline, failing `_verify_quote_at_lines`'s
  exact-substring check. This was orthogonal to the subject_scope defect this round targeted
  (pre-existing chunking/provenance code) but was a real, structural risk for the actual bootstrap
  sources. A read-only audit of all four `docs/AC/*.md` files subsequently confirmed the risk is
  concentrated in `AC-OPERATOR_FACT_RESOLUTIONS.md` specifically (19 confirmed wrap boundaries in
  101 lines) and effectively absent from the three bulk corpus files (0 each; see D-017 for the
  full exposure analysis). **Fixed** in a follow-up session: `services/quote_recovery.py` (new)
  recovers the exact original source slice via whitespace-normalized, uniqueness-required location
  matching -- never fuzzy/semantic, never storing the normalized form itself, always re-verified by
  the original exact-match validator before anything is trusted. See D-017 for full detail.
- **Dry-run re-verified** against the real four `docs/AC/*.md` sources after the subject_scope
  repair: `--dry-run` reported 60 planned chunks/estimated provider calls across all four sources,
  zero provider calls, zero database writes (`CandidateMemory.objects.count()` confirmed 0
  afterward).
- **Verdict**: subject_scope/legal-employer semantic-completeness defect -- FIXED and verified
  live. Line-wrap provenance risk (D-017) -- FIXED (deterministic, no live call needed to verify
  the fix itself; 17 new deterministic tests cover it, including a synthetic fixture matching
  `AC-OPERATOR_FACT_RESOLUTIONS.md`'s confirmed wrapping style). The real four-source bootstrap has
  still **not** been run -- only dry-run/synthetic/rolled-back qualification calls.

## The real four-source bootstrap: revision 1 (v1/id=6, audit evidence) and revision 2 (v2/id=7, ACTIVE)

The real four-source bootstrap was subsequently authorized and run live against NVIDIA Nemotron,
producing `CandidateMemory` version 1 (id=6, status `NEEDS_REVIEW`). A full read/write review
(applying only the application's own `services/lifecycle.py` actions -- no raw SQL) resolved its
one genuine conflict (a German B1-attained/B2-in-progress false-positive split across two claims,
merged onto one) and corrected one misclassified claim. That review also surfaced two further
defects, neither fixable by claim-level review actions alone:

- **65% chunk truncation**: 39 of 60 chunks failed with `finish_reason=length` at the (then-fixed)
  `max_output_tokens=4096` ceiling, concentrated in the bulk corpus (primary/English/German), and
  the operator-approved Ford/Continental/Maruti employment-history facts are **entirely absent**
  from the extracted claims as a result (only a corrected, still-location-less Ambigai
  legal-employer-identity claim survives).
- **Coarse duplicate-grouping conflation** (pre-existing since M3, not introduced this session):
  `storage.duplicate_group_key()`'s `subject_scope::claim_type` fallback silently merged distinct
  facts sharing a scope+type -- one `responsibility` claim ended up with 39 supports representing
  ~30 different actual responsibilities; one `education` and one `certification` claim each
  silently merged two genuinely distinct degrees/certifications.

**Both defects are now fixed** (D-018): bounded recursive chunk-splitting on truncation (never
raising `max_output_tokens`, never re-enabling reasoning, only shrinking chunk size, bounded and
fail-closed), a durable `ChunkExtractionAttempt` audit trail, removal of the coarse
duplicate-grouping fallback for every claim type, and activation validation strengthened to block
on any unresolved chunk attempt, any open conflict, or zero employment coverage (overridable only
by an explicit, visible operator flag). **Revision 1 (v1/id=6) itself is preserved exactly as
built, never activated, and not further edited** -- it remains valuable audit evidence of both
defects.

Recovery did not use `--force-reextract` (which would re-process all four sources from scratch,
inheriting nothing from revision 1). Instead, revision 2 (v2/id=7) was recovered from its own
original truncated chunks via three successive rounds of targeted, bounded recovery, each adding
one deterministic tier only after the previous one proved insufficient, never raising
`max_output_tokens` past a model-verified ceiling and never re-enabling reasoning:

1. Recursive chunk-splitting on `finish_reason=length` down to a `MIN_SPLIT_CHUNK_LINES` floor,
   then a further one-physical-line-per-chunk fallback once that floor still truncated (some source
   lines are themselves entire alternate résumé-summary Markdown bullets, 600-1044 characters each).
2. For the one remaining line that still truncated even at single-line granularity (`docs/AC/
   AC-profile_english.md` line 37), a single manually-authorized retry at `max_output_tokens=8192`
   (confirmed within the configured NVIDIA Nemotron model's real 16,384-token completion ceiling
   before spending the call) -- this also still truncated.
3. A deterministic, LLM-free sentence-boundary split of that same line (`chunking.
   split_line_into_sentences`) into its six constituent sentences, each processed independently at
   the normal `max_output_tokens=4096`; all six succeeded, and the entire historical failure lineage
   for that line was marked `SUPERSEDED`.

After this recovery, revision 2 had zero unresolved `FAILED` chunk attempts, zero `OPEN`
conflicts, and confirmed employment coverage. Conflict detection and safe auto-confirmation were
re-run (both verified idempotent across repeated runs) and produced no duplicate conflicts. Four
`positioning_statement`-type claims (alternate résumé-summary framings, `resume_eligible=False` by
design) remain `UNCONFIRMED` -- correctly excluded from any confirmed+resume_eligible downstream
retrieval and correctly not an activation blocker. **Revision 2 (v2/id=7) passed activation
validation with zero blockers and was activated on 2026-09-03** via
`services/lifecycle.py::activate_revision` -- it is now the sole `ACTIVE` CandidateMemory; revision
1 remains `NEEDS_REVIEW`, untouched, with `activated_at` still `None`.

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

## What exists (M4 -- new, COMMITTED)

`job_intake` (Agent Jobber) and the `job_applications` scaffolding are implemented and committed
per `docs/IMPLEMENTATION_PLAN.md` M4, after a read-only pre-commit audit and its corrections were
applied (D-004 follow-up decision note, cascade-delete/bulk-ORM bypass disclosure in the JRA
immutability comments, `pipeline_phase` made admin-read-only while `application_outcome` stays
operator-editable). `candidate_matching`/`resume_builder`/`reviews` remain completely untouched
(M5-M6).

- **`job_applications/models.py`**: `JobApplication` (D-012) with only the M4-required shape --
  `current_jra` FK (to `job_intake.JobRequirementAnalysis`), `pipeline_phase`
  (`NEW`/`ANALYSIS`/`PREPARATION`/`READY`), `application_outcome`
  (`NOT_APPLIED`/`APPLIED`/`INTERVIEWING`/`REJECTED`), `created_at`/`updated_at`.
  `current_fit_assessment`/`current_resume_draft` are deliberately not added yet -- `FitAssessment`
  (M5)/`ResumeDraft` (M6) don't exist, mirroring the same defer-until-the-owning-milestone pattern
  M1 already used for this app itself. `advance_to_analysis()` is the one M4-owned transactional
  phase transition (`NEW`->`ANALYSIS`), rejecting any other starting phase; `application_outcome`
  is never touched by it.
- **`job_intake/models.py`**: `JobRequirementAnalysis` (FK to `JobApplication`, `version`,
  `source_type`, `source_url`, `original_input`, `extracted_text` + its sha256, AJ's structured
  output fields, `screening_risks` as a JSON list) and child `JobRequirement` (`requirement_id`
  e.g. `JR-001`, `order`, `category`, `text`, `source_context`) -- both append-only: `save()`/
  `delete()` raise on an already-persisted instance (the same honest, application-layer-only
  immutability pattern as `candidate_memory`'s frozen-revision guard -- a raw `QuerySet.update()`/
  bulk-update/raw SQL would bypass it identically). `(job_application, version)` and
  `(job_requirement_analysis, requirement_id)`/`(..., order)` are enforced as real DB unique
  constraints.
- **`job_intake/schemas.py`**: `AgentJobberAnalysis` Pydantic contract (employer, role_title,
  posting_language required-non-blank, location, work_arrangement, `requirements` list of
  `ExtractedRequirement` [category enum MANDATORY/PREFERRED/RESPONSIBILITY/ATS_SIGNAL/
  IMPLIED_EXPECTATION, text, optional source_context], `screening_risks`). Deliberately has no
  requirement-numbering field at all -- `JR-NNN` IDs are assigned purely from the `requirements`
  list's order by application code (`services/intake.py`), so a model can never control its own
  canonical ID even if it tried.
- **`job_intake/services/fetch.py`**: bounded, defensive URL fetching (D-004) --
  `readability-lxml` (D-004 follow-up, see below) for main-content extraction. HTTP(S)-only,
  rejects embedded credentials, resolves and rejects private/loopback/link-local/reserved/
  multicast destinations (checked for the original URL **and** every redirect hop, up to 5 hops),
  finite connect/read timeouts, a 2MB response-size cap enforced both via `Content-Length` and
  while streaming, an accepted-content-type allowlist, an explicit `User-Agent`, no credentials
  ever forwarded, and a deterministic usability check (minimum length + a challenge/access-denied
  keyword blocklist) before ever reaching an LLM call. Every failure raises `FetchError` with a
  sanitized `safe_message` -- the only text ever shown to the operator; raw fetched bodies never
  reach an exception message.
- **`job_intake/services/analyze.py`**: the AJ LLM call, routed only through
  `llm_provider.adapters.get_adapter_for_stage(AJ_ANALYZE)` -- no provider SDK import (confirmed by
  grep). Prompt delimits the posting as `<job_posting>...</job_posting>` data, explicitly
  instructing the model to ignore any instruction-like content inside it, mirroring
  `candidate_memory.services.extraction`'s hardening pattern.
- **`job_intake/services/intake.py`**: `resolve_posting_source()` (exactly-one-of-URL/pasted-text
  validation, a 20-50,000 char bound on pasted text, `FetchError` propagates uncaught so the view
  can offer the pasted-text fallback with zero LLM calls) and `run_intake()` (runs the AJ call
  *outside* any transaction so its `LLMCallLog` audit row always commits regardless of what happens
  next, then -- only once a valid structured result exists -- persists `JobApplication` +
  `JobRequirementAnalysis` + `JobRequirement` rows + the `NEW`->`ANALYSIS` transition as one
  all-or-nothing `transaction.atomic()` block; any failure after the call leaves nothing partial).
- **UI** (`job_intake/views.py`/`urls.py`/`templates/job_intake/*.html`, mounted at
  `/job-intake/`): an intake form (URL or pasted text) and a read-only analysis detail page
  (employer/role/source/language/version/pipeline phase, categorized requirements with stable JR
  IDs -- IMPLIED_EXPECTATION rows visibly labeled "inferred" -- and screening risks). POST/
  redirect/GET on success; a fetch failure re-renders the form with the URL preserved and the
  pasted-text fallback offered, making zero LLM calls; CSRF-protected via Django's standard
  middleware (verified with `enforce_csrf_checks=True`); auto-escaping confirmed against an
  injected `<script>` payload in a fixture AJ response. No Gate-1/candidate-matching/resume
  controls exist on this page (M5/M6 are out of scope).
- **Admin**: `JobRequirementAnalysisAdmin` (with an inline, read-only `JobRequirement` view) denies
  add/change/delete -- a completed JRA version is append-only, admin cannot bypass that;
  `JobApplicationAdmin` is a plain read-mostly registration (`current_jra`/timestamps read-only).
- **D-004 (extraction library) resolved**: `readability-lxml` (import name `readability`), chosen
  over heavier alternatives (e.g. `trafilatura`, which pulls in its own crawling/date-parsing
  dependency chain aimed at bulk corpus scraping) as the smallest maintained option that performs
  well at "strip chrome, keep the main posting body" for one URL at a time. Added to
  `requirements.txt` (pulls in `lxml`/`cssselect`/`chardet` transitively); recorded in
  `docs/DECISIONS.md` as a D-004 follow-up note, not a rewrite of D-004's original approved text.
- **Automated test suite**: 68 new deterministic tests (`job_intake/tests/`,
  `job_applications/tests/`) -- full project suite 352/352 passing, `ruff check .` clean,
  `manage.py makemigrations --check` clean, `manage.py check` clean, `git diff --check` clean. All
  network access is mocked (`requests.get`, `socket.getaddrinfo`) -- confirmed by code review, zero
  real network calls in the automated suite. Covers: JRA version uniqueness/append-only
  immutability, stable JR ID/ordering uniqueness constraints, phase-transition validity (including
  rejecting a second `advance_to_analysis()` call and confirming `application_outcome` stays
  independent), URL-fetch safety (scheme/credentials/private-loopback-link-local addresses
  including redirect-hop validation/timeout/connection-error/bad-status/excessive-redirects/
  unsupported-content-type/oversized-response-via-header-and-via-streaming/empty-or-short-
  extraction/challenge-page/extraction-library-failure, each making zero LLM calls where
  applicable), pasted-text intake (English and German fixtures, exact-text preservation, both-
  sources and neither-source and too-short and too-long validation errors), AJ schema validation
  (blank posting_language, blank requirement text, invalid category, model-supplied numbering
  cannot control canonical IDs), atomic persistence (successful run, failed-analysis rollback
  including confirming the LLMCallLog audit row survives the rollback, simulated storage-failure
  rollback), and view-level CSRF/escaping/redirect/fallback/no-later-milestone-controls checks.
- **Manual deterministic walkthrough performed** (Django test client, `manage.py shell`, zero real
  network/provider calls -- see the automated test suite above, which exercises exactly these
  paths): an English pasted posting and a German pasted posting each produced a `JobApplication`
  with stable `JR-001.. JR-005` IDs and `pipeline_phase=ANALYSIS`; a mocked broken URL produced zero
  LLM calls and rendered the pasted-text fallback with the URL preserved; `application.refresh_from_db()`
  after each run confirms state read back from PostgreSQL matches what was written (durability
  across a fresh query, not an in-memory assumption).
- **Not done, reported honestly**: no real public job-posting URL and no real LLM provider call
  were used for M4 -- every AJ analysis in this session used the `FakeAdapter` with a scripted
  response, per the explicit "no live LLM calls" constraint for this milestone. `AJ_ANALYZE` has no
  live-provider verification, matching M3's already-honest "not live-verified" pattern for `MEMORY_BUILD`
  before its own qualification round. No `StageModelAssignment` row for `AJ_ANALYZE` was created or
  changed in the persistent development database.

## What does not exist

- Any model fields, migrations, views, or templates for `candidate_matching`, `resume_builder`, or
  `reviews` (Milestones M5-M6).
- `JobApplication.current_fit_assessment`/`current_resume_draft` (M5/M6 scope, by design -- see
  the M4 section above).
- Any pipeline stage other than Candidate Memory build (`MEMORY_BUILD`) and Agent Jobber
  (`AJ_ANALYZE`) actually calling `get_adapter_for_stage()` -- `AC_MATCH`/`AB_BUILD` remain unused
  until M5/M6.
- Any real-world URL fetch verification (only mocked HTTP responses have been exercised) or any
  live NVIDIA/OpenAI/Gemini call for the `AJ_ANALYZE` stage.
- Any live-provider verification of the OpenAI/NVIDIA NIM/Gemini adapters (opt-in, operator-run,
  not performed in this environment -- no credentials configured), including for the M3 Candidate
  Memory extraction path specifically -- see the M3 verification section above.
- A bootstrapped real Candidate Memory built from the operator's actual four source documents
  (only synthetic test fixtures have been processed in this environment).
- Any remote/CI configuration (not required; local quality commands remain the standard).

## Decisions (see `docs/DECISIONS.md` for full detail)

D-001 through D-015 are all APPROVED (several "with modification"); D-013 is superseded by D-010.
D-016 (`FAILED` lifecycle state) remains PROPOSED, not yet product-owner-approved. D-017 (line-wrap
provenance risk) is now **APPROVED AND IMPLEMENTED**. Neither D-016 nor anything else is blocking
for any milestone through M7 as currently scoped.

## Next action

M4 is implemented, committed, and verified against the `FakeAdapter`/mocked HTTP. The real
four-source Candidate Memory bootstrap has been run live against NVIDIA Nemotron, recovered, and
activated (CandidateMemory v2/id=7 is the sole `ACTIVE` revision -- see "The real four-source
bootstrap" section above). Milestone M5 (Agent Candidate) may now proceed per
`docs/IMPLEMENTATION_PLAN.md`'s dependency graph -- it depends on both M3 and M4, both now complete
with a real, activated Candidate Memory available to retrieve against. M5 has not been started.

## Maintenance rule for this file

Update this file after any milestone completes or any meaningful implementation step lands.
Describe the repository as it truly is -- never mark something present, tested, or working that
has not actually been built and verified.
