# Current State

Last updated: 2026-09-05 (OpenAI Structured Outputs strict-schema `required`-completion fix -- see
"OpenAI strict-schema `required`-completion fix (2026-09-05, D-032)" below. No M5/M6/Gate action,
no live provider call, and no `StageModelAssignment`/registry change were made in this session --
committed on branch `worktree-openai-strict-schema-fix`, not merged to `main`.). Previously, also
2026-09-05: runtime agent/stage architecture documentation, configurable per-stage
LLM read timeout, OpenRouter key-status service + sanitized 429 diagnostics + operator UI, and a
read-only `gpt-5` Chat Completions compatibility fix -- see "Runtime timeout/OpenRouter-diagnostics/
GPT-5-readiness work (2026-09-05, D-029/D-030/D-031)" below. No M5/M6/Gate action, no live provider
call, no `StageModelAssignment` change, and no operational timeout value change were made in that
session -- everything above is additive mechanism/UI/documentation, committed on branch
`worktree-timeout-openrouter-diagnostics`, not merged to `main`.). Previously, as of 2026-09-04:
stage-specific LLM output-token budgets, committed -- see "Stage-specific
LLM output-token budgets (2026-09-04, D-024)" below, motivated by a real AJ_ANALYZE rerun of
JobApplication 9 truncating at the model's shared 4,096-token capability. Not applied live: the
intended 16,384/8,192 configuration is a separate follow-up action.). Previously, also 2026-09-04:
recall-repair work on top of M5/M6, committed -- see "Bounded retrieval recall repair (2026-09-04,
D-021)" below. An initial verification pass measured exact-claim-ID recall against a five-profile
gold set (16/19); the product owner then replaced that acceptance bar with requirement-level
evidence coverage, and an acceptance review confirmed the three unreached claims are each
genuinely redundant with claims that did reach the pool -- see D-021 for the full, claim-by-claim
comparison. Previously, as of 2026-09-03: M5 and M6 implemented, tested, committed,
independently audited, and hardened based on that audit -- see "M5/M6 audit hardening (2026-09-03,
D-020)" below for the two release-blocking corrections. M5 -- Agent Candidate, matching, and Human
Review Gate 1 -- and M6 -- Agent Builder and Human Review Gate 2 -- are both complete: bounded,
relevance-ranked retrieval and static-requirement assessment against the real ACTIVE
CandidateMemory and real APPROVED CareerEngagements; LLM-backed narrative assessment/generation
routed only through the FakeAdapter (no live provider calls were authorized or made this round);
disposition-coverage and no-fabrication (including engagement-placement-correct) validators;
deterministic markdown rendering; both human review gates wired end to end, hardened against
concurrent approval and provider failure. A full end-to-end manual walkthrough (intake -> Agent
Candidate -> Gate 1 feedback+approval -> Agent Builder -> Gate 2 feedback+approval) was run live
against the real ACTIVE CandidateMemory (v2/id=7) and a real APPROVED CareerEngagement (CE-0003),
inside a transaction rolled back at the end -- zero residue in the persistent development database
(`JobApplication`/`FitAssessment`/`ResumeDraft` counts confirmed unchanged before and after).)

## Summary

This repository has completed **Milestones M1 through M6**. M1 established the Django/PostgreSQL
application foundation. M2 fully implements the `llm_provider` app. M3 implements the
`candidate_memory` app end to end, including a real, live-qualified four-source bootstrap whose
recovered revision 2 (id=7) is the sole **ACTIVE** CandidateMemory. M4 implements the `job_intake`
app (Agent Jobber) and the `job_applications.JobApplication` aggregate. Ahead of M5/M6, D-019
implemented the deterministic static-profile boundary (`CareerEngagement`/`ClaimEngagementMapping`)
and, in this session, three real engagements (CE-0001 Ford, CE-0002 Continental, CE-0003 Maruti
Suzuki) were approved with operator-approved organization aliases/programme scopes, and 178
narrative claim-engagement mappings are `APPROVED` (43 direct-match + 135 alias/programme-scope
matches; 9 static-type mappings `REJECTED`; 944 global claims intentionally unmapped). M5
(`candidate_matching` + `reviews` Gate 1) and M6 (`resume_builder` + `reviews` Gate 2) are now
**implemented, tested, and committed** -- see their own sections below. Milestone M7 (integrated
per-job workflow, freshness enforcement across the whole chain, and the dashboard) remains **not
implemented** -- confirmed: no `job_applications` dashboard list/detail view exists, and no
cross-app integration beyond the pointer-based freshness checks M5/M6 already enforce individually.

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
- **Schema translation** (`llm_provider/schema_translation.py`, D-032 as of 2026-09-05):
  `to_openai_strict_schema()` (OpenAI's own dialect) and `to_openai_compatible_strict_schema()`
  (NVIDIA NIM/OpenRouter's dialect) both keep `$ref`/`$defs`/`enum` and recursively complete every
  object node's contract -- `additionalProperties: false` plus `required = list(properties.keys())`,
  in `properties`' own order, for the root object, `$defs`, nested objects, array item objects,
  and anything reached through `$ref` -- raising `OpenAIStrictSchemaContractError` (caught by each
  adapter as a pre-HTTP `CONFIGURATION` failure) if the result is ever incomplete. Only
  `to_openai_strict_schema()` additionally strips `minLength`/`maxLength` (confirmed unsupported
  by OpenAI's own Structured Outputs documentation); `to_openai_compatible_strict_schema()` keeps
  them, since NVIDIA/OpenRouter are not confirmed to reject either keyword. `to_gemini_schema()`
  (inlines `$ref`/`$defs`, strips `enum`, converts type names to Gemini's uppercase OpenAPI-subset
  dialect) is unchanged.
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

## What exists (M5 -- new, COMMITTED, commit `aed6b32`)

`candidate_matching` (Agent Candidate) and the Gate-1 half of `reviews` are implemented and
committed per `docs/IMPLEMENTATION_PLAN.md` M5.

- **`candidate_matching/models.py`**: `FitAssessment` (append-only/versioned per D-010, FK to the
  exact `JobRequirementAnalysis` version it was built from, `retrieved_claim_ids`/
  `retrieved_engagement_ids` recording exactly which bounded context was provided) and child
  `RequirementAssessment` (`requirement_id`, `disposition` MATCH/PARTIAL/GAP/UNKNOWN,
  `supporting_memory_claim_ids`/`supporting_engagement_ids`, `explanation`, `gap_or_limitation`) --
  both append-only, same honest application-layer-only immutability pattern as `job_intake`'s JRA.
- **`services/retrieve.py`** (superseded by the bounded pipeline below, 2026-09-03 audit
  hardening -- kept only as the eligibility layer `bounded_retrieval.py` builds on):
  `retrieve_eligible_pool()` (formerly `retrieve_context()`) returns a bounded subset of the ACTIVE
  CandidateMemory -- every `CONFIRMED`+`resume_eligible` narrative (non-static-type) `MemoryClaim`
  that either has an `APPROVED` `ClaimEngagementMapping` or has no mapping at all (a global claim),
  every `APPROVED` `CareerEngagement`, and every `CandidateRule` -- and exposes exactly which
  claim/engagement IDs were included. Never the full CandidateMemory, a source document, or the
  reference snapshot.
- **`services/static_requirements.py`**: a heuristic, regex-based (D-004-style, not a completeness
  guarantee) classifier recognizing five static/structural requirement kinds -- total
  non-overlapping experience, single-engagement tenure, current/past employment status, location,
  and direct-employment-vs-consulting relationship -- each assessed purely from `CareerEngagement`
  records via `candidate_memory.services.career_engagement`/`static_profile_boundary`, with zero
  LLM involvement (D-019). A requirement this classifier does not recognize simply falls through to
  the LLM-backed narrative path unchanged.
- **`services/assess.py`**: the AC LLM call for genuinely narrative requirements only, routed
  exclusively through `llm_provider.adapters.get_adapter_for_stage(AC_MATCH)` -- no provider SDK
  import (confirmed by grep). Prompt gives narrative claims, engagement summaries (read-only, cited
  by ID only), and candidate rules as context; instructs no-concealment and cites-real-IDs-only.
- **`validators/disposition_coverage.py`**: `sanitize_items()` drops any cited claim/engagement ID
  that was not part of the actual retrieved context (never trusting a fabricated ID) and downgrades
  a MATCH/PARTIAL left with no surviving evidence to a disclosed UNKNOWN (the downgrade reason is
  appended to the row's own `explanation`, visible at Gate 1 -- never a silent drop);
  `ensure_full_coverage()` guarantees exactly one row per relevant `JobRequirement`, synthesizing an
  explicit UNKNOWN row for anything the model never addressed. A known GAP is never softened or
  removed by either function.
- **`services/fit_assessment.py`**: orchestrates retrieval + local static assessment + LLM
  narrative assessment + validation into one versioned `FitAssessment`, mirroring
  `job_intake.services.intake.run_intake`'s shape (LLM call outside any transaction so its
  `LLMCallLog` always commits; persistence as one all-or-nothing `transaction.atomic()` block).
- **`job_applications/models.py`**: `current_fit_assessment` FK; `record_jra`/
  `record_fit_assessment` (plain pointer updates, no phase check) and `approve_gate1()`
  (`ANALYSIS -> PREPARATION`, requiring a `current_fit_assessment` whose `based_on_jra_id` matches
  `current_jra_id` -- D-006 freshness, refuses a stale assessment outright).
- **`job_intake/services/intake.py`**: `rerun_analysis()` (new) -- the Agent-Jobber side of a
  Gate-1 feedback re-run; creates a new append-only JRA version and repoints `current_jra` via
  `record_jra`, reusing the same original input by default or a fresh URL/pasted text if supplied.
- **`reviews`**: `ReviewFeedback` model (gate, target AJ/AC, comments); `services.py`
  (`run_agent_candidate`, `submit_gate1_feedback` -- records feedback and immediately performs the
  targeted re-run as one synchronous action, `approve_gate1`); `views.py::gate1_view` + `templates/
  reviews/gate1.html` -- combined AJ+AC display with per-requirement dispositions/evidence, a
  staleness banner, run/re-run/approve/feedback actions, and feedback history. CSRF-protected
  (verified with `enforce_csrf_checks=True`); GET/POST via `require_http_methods`.
- **Automated test suite**: 147 new deterministic tests -- full project suite 664/664 (after M6,
  see below), `ruff check .`/`manage.py check`/`makemigrations --check`/`git diff --check` all
  clean. Covers retrieval boundary conditions, all five static-requirement kinds, the coverage
  validator's sanitize/downgrade/fill-gap behavior, the full orchestrator (including a fabricated-
  claim-id downgrade and a static requirement never reaching the LLM), Gate-1 view/service behavior
  (run/approve/feedback/staleness/CSRF), and `JobApplication`'s new transition methods.
- **Not done, reported honestly**: no live LLM call was made for `AC_MATCH` -- every assessment in
  this session used the `FakeAdapter` with a scripted response, per the explicit "no live provider
  calls" constraint for this work package. No `StageModelAssignment` row for `AC_MATCH` was created
  or changed in the persistent development database (only in the rolled-back manual walkthrough,
  see the top of this file).

## What exists (M6 -- new, COMMITTED)

`resume_builder` (Agent Builder) and the Gate-2 half of `reviews` are implemented and committed per
`docs/IMPLEMENTATION_PLAN.md` M6, completing `JobApplication`'s three current-version pointers.

- **`resume_builder/models.py`**: `ResumeDraft` (append-only/versioned per D-010, FK to the exact
  `FitAssessment` version it was built from, `retrieved_claim_ids`/`retrieved_engagement_ids`,
  `recommended_title`/`title_options`/`skill_categories`/`positioning_guidance` as planning-only
  JSON, `rendered_markdown`) with exactly one further permitted mutation -- `confirm()`, setting
  `confirmed_at` from `None` to a timestamp once, the same "one specific allowed transition, else
  frozen" pattern `CandidateMemory` already uses for `ACTIVE -> SUPERSEDED`. `ResumeElement` (the
  queryable, evidence-bearing structured content Gate 2 inspects: `section`, `engagement_id` for
  experience bullets, `order`, `text`, `supporting_memory_claim_ids`, `matched_job_requirement_ids`)
  -- also append-only.
- **`resume_builder/schemas.py`**: `AgentBuilderOutput` and its nested models, every one
  `extra="forbid"`, with no field anywhere for employer/client/role title/dates/location/
  presentation mode (D-019) -- `BaseLLMAdapter.generate()`'s existing re-validation against this
  schema means a provider that tried to smuggle one of those fields through fails schema
  validation outright, not merely "gets ignored by convention" (proven by
  `test_no_fabrication.py::test_forbidden_static_field_fails_schema_validation_before_reaching_this_validator`).
- **`services/generate.py`**: the AB LLM call, routed exclusively through
  `llm_provider.adapters.get_adapter_for_stage(AB_BUILD)` -- no provider SDK import. Prompt gives
  the target job, Agent Candidate's own per-requirement dispositions/explanations, narrative
  claims, and read-only engagement summaries; instructs selecting the strongest truthful
  positioning and citing only real IDs from the given context.
- **`validators/no_fabrication.py`**: `validate_and_flatten()` -- unlike M5's per-item downgrade
  approach, this **fails the entire build closed** (raises `NoFabricationError` naming every
  failure) on any element with no evidence, a fabricated claim ID, or an unknown/unapproved/
  not-in-context `engagement_id` (via `resolve_approved_engagement`) -- nothing is ever persisted
  for a build that fails validation, matching `docs/RESUME_OUTPUT_STRUCTURE.md` Sec 3/6's own
  "rejected... before any markdown is ever rendered" contract. **Corrected 2026-09-03 (D-020)**:
  every experience bullet's cited claims are now also checked against that claim's *complete* set
  of approved engagement mappings (`RetrievedClaim.approved_engagement_ids`) -- a claim approved
  only for a different engagement than its bullet fails the build closed, and a bullet whose
  evidence is entirely global (unmapped) claims is rejected too (global evidence may supplement a
  bullet that already has at least one genuinely matching claim, never support it alone). The
  original version of this validator only checked that a cited claim existed somewhere in context,
  never that it belonged to the specific engagement it was placed under -- see D-020.
- **`rendering/markdown.py`**: deterministic v1 rendering per `docs/RESUME_OUTPUT_STRUCTURE.md`
  Sec 4, run only after validation passes and entirely from already-validated in-memory data (no
  DB round-trip needed before persistence). Every experience header comes exclusively from
  `render_engagement_header`, resolved fresh by `engagement_id` -- never anything Agent Builder
  produced. Experience sections are ordered deterministically by each engagement's own recorded
  dates (most recent/current first), not by whatever order the LLM happened to list them in.
  Achievement placement (explicitly left open as a future iteration in
  `docs/RESUME_OUTPUT_STRUCTURE.md`) uses a documented v1 policy: an achievement already covered by
  an existing bullet's evidence is skipped; an uncovered achievement resolving to exactly one
  engagement (via its own claims' engagement mappings) is placed there; one resolving to zero or
  multiple engagements is placed under Professional Summary instead of being silently dropped.
- **`services/build.py`**: orchestrates the freshness/gate precondition (requires
  `pipeline_phase` in PREPARATION/READY and a non-stale `current_fit_assessment`), retrieval,
  generation, validation, rendering, and versioned persistence, mirroring `build_fit_assessment`'s
  transaction shape. **Corrected 2026-09-03 (D-020)**: retrieval is no longer M5's raw eligible
  pool -- `resume_builder/services/context.py::build_builder_context()` builds a separate, smaller
  `BuilderContext` strictly from what the current `FitAssessment` already selected
  (`retrieved_claim_ids`/`retrieved_engagement_ids`), re-verifying each claim/engagement is still
  eligible/`APPROVED` rather than reloading or re-querying the full CandidateMemory.
- **`job_applications/models.py`**: `current_resume_draft` FK (completing D-012's three pointers);
  `record_resume_draft` (plain pointer update) and `approve_gate2()` (`PREPARATION -> READY`,
  requiring a non-stale `current_resume_draft` -- D-006 -- and confirming the draft itself in the
  same action).
- **`reviews`**: `services.py` gains `run_agent_builder`, `submit_gate2_feedback` (feedback always
  targets Agent Builder -- a wrong disposition belongs at Gate 1 instead, not re-litigated here),
  `approve_gate2`; `views.py::gate2_view` + `templates/reviews/gate2.html` -- rendered markdown,
  full per-element evidence inspection table, staleness banners (both draft-vs-assessment and the
  transitive assessment-vs-JRA case), run/re-run/approve/feedback actions, feedback history.
  CSRF-protected; a Gate-1-to-Gate-2 navigation link appears once past NEW/ANALYSIS.
- **Automated test suite**: 47 new deterministic tests -- full project suite **664/664 passing**,
  `ruff check .`/`manage.py check`/`makemigrations --check`/`git diff --check` all clean.
- **Manual end-to-end walkthrough performed live** against the real ACTIVE CandidateMemory
  (v2/id=7) and a real `APPROVED` `CareerEngagement` (CE-0003, Maruti Suzuki), using the
  `FakeAdapter` for every LLM stage (no live provider calls): pasted-text intake -> Agent Candidate
  (MATCH) -> Gate-1 feedback-triggered AC re-run (new `FitAssessment` v2) -> Gate 1 approved
  (`PREPARATION`) -> Agent Builder produced a real rendered markdown resume citing the real
  claim's text and the real engagement's title/organisation/dates/location -> Gate-2 feedback-
  triggered AB re-run (new `ResumeDraft` v2) -> Gate 2 approved (`READY`, draft confirmed). The
  whole walkthrough ran inside one transaction that was rolled back at the end --
  `JobApplication`/`FitAssessment`/`ResumeDraft` counts confirmed at 0 both before and after, and
  `LLMCallLog`'s count was unchanged (263), since the walkthrough's own calls rolled back with it.
  One pre-existing, unrelated data-quality observation surfaced during this walkthrough: the real
  claim `MC-7-0250`'s `canonical_text_en` itself begins with a literal `"- "` (an M3-era extraction
  artifact, not an M5/M6 defect), which the deterministic renderer faithfully reproduces (rendering
  exactly what is stored, never editing claim text) -- visible in the walkthrough's sample output as
  a doubled leading dash. Flagged here for future correction via the normal Candidate Memory
  correction workflow, not fixed silently as part of this work package.
- **Not done, reported honestly**: no live LLM call was made for `AB_BUILD` -- every generation in
  this session used the `FakeAdapter` with a scripted response. No `StageModelAssignment` row for
  `AB_BUILD` was created or changed in the persistent development database (only in the rolled-back
  manual walkthrough). M7 (the dashboard, cross-app integration beyond the pointer-based freshness
  checks, and the final "download the resume" flow) was not started.

## M5/M6 audit hardening (2026-09-03, D-020)

An independent adversarial audit of the M5/M6 implementation above found two release-blocking
gaps, confirmed live against the real ACTIVE CandidateMemory before being fixed in three separate
commits. See D-020 in `docs/DECISIONS.md` for full detail; summarized here:

1. **Unbounded retrieval (blocking, fixed)**: `retrieve_context()` sent the *entire* eligible pool
   to both `AC_MATCH` and `AB_BUILD` -- 1,122 claims/359 rules, ~203,000 characters (~50,785
   estimated tokens) against the real corpus, with no relevance filtering, no count/token bound,
   and no deduplication. Replaced with a real bounded pipeline: conservative dedup -> deterministic
   per-requirement lexical candidate generation -> a new bounded LLM relevance-ranking stage
   (`AC_RANK`) that never falls back to the full corpus on failure -> capped final selection
   against documented hard limits, with an inspectable `retrieval_manifest` on every
   `FitAssessment`. M6 now builds its own smaller `BuilderContext` from what the current
   `FitAssessment` already selected, never the full CandidateMemory.
2. **Engagement-placement gap (blocking, fixed)**: the no-fabrication validator only checked that a
   cited claim existed *somewhere* in context, never that it belonged to the specific engagement
   its bullet was placed under -- a claim mapped only to Engagement A was accepted under
   Engagement B with no rejection. Fixed: every experience bullet is now checked against the
   claim's complete set of approved engagement mappings; a wrong-engagement citation, or a bullet
   supported only by global/unmapped evidence, fails the whole build closed.
3. **Non-blocking hardening also applied**: a `FAKE`-typed provider can no longer serve a real
   stage outside an automated test run; Gate 1/Gate 2 approval is now `select_for_update()`-locked
   and idempotent (a repeated approval of an already-approved, still-current artifact is a safe
   no-op); the same version-allocation race is closed the same way for `FitAssessment`/
   `ResumeDraft`/JRA-rerun version numbering; Gate 1 shows screening risks and the retrieval
   manifest inline; Gate 2 links every supporting claim ID to its CandidateMemory detail/quotation
   page and marks global/supplementary evidence distinctly.

Verification: 746/746 tests passing (26 new dedicated failure-atomicity tests covering timeout/
429/retryable-5xx/non-retryable/malformed/schema-invalid/truncated-output/DB-failure-during-child-
persistence/retry-after-failure for both `AC_MATCH` and `AB_BUILD`; 31 new engagement-placement
tests including the audit's exact adversarial scenarios), `ruff`/`check`/`makemigrations`/
`git diff --check` all clean, plus a fresh genuinely-isolated PostgreSQL database (not Django's own
test-runner database) had every migration applied cleanly from zero. No live provider call was
made or authorized. `docs/REQUIREMENT_TRACEABILITY.md`'s MEM-015/MEM-016 rows were corrected to
describe the real bounded pipeline rather than the unbounded one they originally cited.

## Bounded retrieval recall repair (2026-09-04, D-021) -- COMMITTED

Following the M5/M6 audit hardening above, a separately-scheduled independent re-audit of the
corrected pipeline found a real recall gap in `candidate_generation.py`'s deterministic lexical
scoring: raw shared-token-count scoring let corpus-common words ("vehicle", "connected", "cloud")
outweigh rare, diagnostic terms ("Kubernetes") that should have dominated, and a German-language
requirement retrieved less evidence than its English equivalent. The product owner directed a
two-part fix, implemented and measured live against the real ACTIVE CandidateMemory in this same
session:

1. **Rarity-aware scoring** (`lexical_relevance.py` rewritten): BM25 (IDF + term-frequency
   saturation) replaces raw overlap counting, with a corpus-relative common-term dampening rule, a
   rarity-weighted exact-phrase bonus (weighted by its own words' average IDF, not the phrase's own
   document frequency), a rarity-weighted acronym/technical-proper-noun bonus, and a corpus-driven
   singular/plural token merge. Two tokenization correctness bugs were found and fixed in the same
   pass (a sentence-final period gluing onto a word's token; a stopword being misread as
   "maximally rare" when it was actually just filtered from measurement) -- see D-021 for detail.
2. **A new bounded requirement-normalization stage (`AC_NORMALIZE`)**, inserted before candidate
   generation: `JobRequirement -> canonical English search representation -> BM25 candidate
   generation -> AC_RANK -> AC_MATCH`. Receives only a requirement's id/text and the job posting's
   language -- never a MemoryClaim, CareerEngagement, or candidate/employment data -- and returns a
   small, schema-bounded (`extra="forbid"`, hard count/length limits) canonical English
   restatement plus diagnostic terms/equivalents/preserved technical terms, scored *alongside*
   (never in place of) the requirement's own original text. Fails closed on any provider error,
   malformed/oversized response, or a requirement-id mismatch (`NormalizationFailedError`), exactly
   like D-020's `AC_RANK` ranking step.

**Verified, live, against the real ACTIVE CandidateMemory (1,122 eligible claims)**: the same five
gold profiles the independent re-audit used (Automotive Cloud/Solutions Architect;
Connected-Vehicle Product Owner; Data/Cloud Data Engineer; ADAS/Validation Engineer; a
German-language profile), with hand-authored deterministic normalization content standing in for a
real provider call. An initial pass measured exact-claim-ID recall: **16 of 19 predeclared critical
claims** reached the pre-`AC_RANK` candidate pool (up from an 11/19 raw-overlap baseline measured
the same way) -- the specific Kubernetes-vs-common-word regression the audit named is fixed and
reproduced (Profile 1 and the German profile both reach a full 4/4, matching each other exactly).
The product owner then replaced exact-claim-ID recall with a **requirement-level evidence-coverage**
acceptance standard: bounded retrieval must ensure every requirement's important concepts have
truthful, source-supported evidence *somewhere* in the pool, not that every claim ID a prior audit
happened to point at survives the cap. An acceptance review inspected the three unreached claims
against what *did* reach the pool for the same requirement (comparing actual capability, scope,
engagement, and evidence strength, not wording) and classified all three
`REDUNDANT — COVERAGE PRESERVED`:
- MC-7-0146 (solution-architecture/integration leadership for connected-vehicle services) is
  covered by MC-7-1002, MC-7-1052 (same engagement), MC-7-0980 and MC-7-0556 (same engagement).
- MC-7-0175 (Ford-VW alliance connectivity integration, unmapped) is covered by MC-7-0556 and
  MC-7-0980 -- both engagement-mapped, i.e. *stronger*, more source-attributable evidence than the
  excluded unmapped claim.
- MC-7-0033 (dbt-based data-quality achievement, unmapped, not itself naming SQL/Airflow/BigQuery)
  is covered by MC-7-0376, MC-7-0121 (data-quality/validation) and MC-7-1026/MC-7-1027/MC-7-0564
  (monitoring).
No requirement's evidence coverage is weakened and none would incorrectly surface as a `GAP`
because of these exclusions. All candidate/token caps stay within their existing D-020 limits
throughout (40 candidates/requirement, ~1,300-1,400 estimated tokens per requirement's candidate
set) -- no limit was raised to reach this result. This work is committed: `lexical_relevance.py`,
`candidate_generation.py`, `normalize.py`, `normalization_limits.py`, the new `AC_NORMALIZE` stage
value/migration, and `bounded_retrieval.py`'s wiring, fully tested (769/769 project tests passing,
including a dedicated `test_normalize.py`), `ruff`/`check`/`makemigrations --check` all clean.

## Agent Jobber semantic sanity gate (2026-09-04, D-022) -- COMMITTED, then course-corrected (D-023, below)

**This section is historical** — kept for the record of what the failure was and how the first fix
overreached; the validator it describes (`job_intake/validators/sanity.py`,
`SemanticValidationError`) was renamed and narrowed by D-023 immediately afterward. See the
"Agent Jobber course correction" section below for the corrected, current state — read that one
first if you're orienting to what's actually running today.

A controlled live Gate-1 preparation run (primary checkout, real NVIDIA NIM credential, one real
posting) surfaced a live M4 failure: JobApplication id=9's `AgentJobberAnalysis` was schema-valid
(posting language and role title correctly detected) but had **zero** `JobRequirement` rows, while
its 14-item `screening_risks` list restated the posting's own responsibilities/qualifications as
candidate-gap judgments ("Lack of hands-on experience with...", "No proven ability to...") --
`run_intake` persisted it as the application's current, "successful" analysis anyway, because
nothing between schema validation and persistence ever asked whether the result was substantively
usable. Root cause and fix are recorded in full in D-022; summary:

- **`services/analyze.py`'s `SYSTEM_PROMPT` rewritten**: states explicitly that Agent Jobber has no
  candidate/history/Candidate-Memory context and must never write a sentence judging whether "the
  candidate" has, lacks, or falls short of a capability; requires every explicit responsibility/
  qualification/skill/experience expectation to become an atomic `requirements` item (never
  summarized into `screening_risks`); tightens MANDATORY (only when the posting states or clearly
  requires it) vs. PREFERRED (preferred/desirable/advantageous/nice-to-have) usage; narrows
  `screening_risks` to only explicit hiring constraints/conditions the posting itself states, each
  requiring a verbatim quotation.
- **`schemas.py`'s `screening_risks` changed from `list[str]` to `list[ScreeningRisk]`**, a new
  model requiring non-blank `text` and `source_context` (mirroring `ExtractedRequirement`'s
  existing, optional one) -- an unstated risk is not an explicit constraint at all. No model
  migration: `JobRequirementAnalysis.screening_risks` is unchanged as a `JSONField`, now storing
  `{"text", "source_context"}` objects; a pre-existing legacy JRA's plain-string risks still
  display correctly via a template fallback.
- **New deterministic semantic sanity validator** (`job_intake/validators/sanity.py::
  find_sanity_violations`, lexical/deterministic throughout, never fuzzy matching): rejects zero
  requirements for a substantive (>=300 char) posting; a non-empty `screening_risks` list produced
  alongside zero requirements (the exact observed failure shape); candidate-gap marker phrases
  ("lack of", "no experience", "no proven", "insufficient", and similar) in any requirement or
  risk text; duplicate requirements (same category + normalized text); and any MANDATORY/PREFERRED/
  RESPONSIBILITY requirement or screening risk whose `source_context` is not a real, exact
  substring of the posting text actually analyzed. Wired into `run_intake`/`rerun_analysis`
  strictly before the persistence transaction opens, via a new `SemanticValidationError` (subclass
  of the existing `AnalysisFailedError` -- no view-layer change needed to handle it): on any
  violation, nothing is created (no `JobApplication`, no `JobRequirementAnalysis`, no
  `JobRequirement`, no pipeline-phase advancement) while the underlying successful provider call's
  `LLMCallLog` row is still written, as always storing only token/latency/error metadata, never raw
  content.
- **New M5 precondition**: `candidate_matching.services.fit_assessment.build_fit_assessment` now
  refuses (`AgentCandidateError`) to run against a current JRA with zero `JobRequirement` rows --
  a fresh runtime check, so it correctly covers JobApplication id=9's real, legacy, pre-D-022 JRA
  (version 1) without editing that row at all (append-only per `JobRequirementAnalysis.save()`,
  left completely untouched throughout this work).
- **UI**: `job_intake`'s analysis-detail page computes (never stores) whether the current JRA has
  zero requirements and shows a clear "INCOMPLETE ANALYSIS ... not eligible for Gate 1" banner when
  true -- covering both the now-impossible-to-create case and JobApplication id=9's real legacy
  state, read-only.

No live provider call was made or authorized by this work (all tests use `FakeAdapter`/scripted
responses); JobApplication id=9's real JRA was inspected read-only only and never rerun or edited.
Fully tested (809/809 project tests passing, including new `test_sanity_validator.py`,
`test_semantic_validation_intake.py`, and an M5-precondition test in
`candidate_matching/tests/test_fit_assessment.py`), `ruff`/`check`/`makemigrations --check` and a
genuinely fresh migration on an isolated database all clean.

## Agent Jobber course correction (2026-09-04, D-023) -- COMMITTED, current state

Immediately after reviewing D-022's implementation above, the product owner stated a durable
boundary: *"LLMs decide meaning and wording. Deterministic code protects truth, boundaries and
lifecycle. The operator approves semantic quality."* D-022's diagnosis was correct and its
lifecycle/provenance/atomicity machinery was sound, but its validator had overreached into judging
*meaning* — a ~26-phrase candidate-gap word blocklist that would reject a posting's own legitimate
"No experience necessary" wording, a 300-character posting-length threshold standing in for "is
this posting real," and a heuristic inferring *why* zero requirements happened (misclassification)
rather than just that it happened. D-023 removed all three and kept everything else. The corrected,
now-current state:

- `job_intake/validators/sanity.py` → **renamed** `job_intake/validators/integrity.py`
  (`find_sanity_violations` → `find_integrity_violations`); `SemanticValidationError` →
  `AnalysisIntegrityError` (still an `AnalysisFailedError` subclass) — the rename makes the
  narrower, corrected scope legible in the module's own name.
- The validator now checks exactly three objective properties, none requiring reading for meaning:
  (1) at least one requirement exists, unconditionally, no length threshold; (2) no exact duplicate
  requirement (same category + normalized text); (3) a MANDATORY/PREFERRED/RESPONSIBILITY
  requirement's or any screening risk's `source_context` is a real, exact substring of the posting
  (`ATS_SIGNAL`/`IMPLIED_EXPECTATION` remain exempt, unchanged from D-022, since neither is
  expected to carry a literal quote by design). `GAP_LANGUAGE_MARKERS`,
  `SUBSTANTIVE_TEXT_MIN_CHARS`, and the risks-without-requirements heuristic are gone entirely —
  not replaced with a larger or more elaborate version of any of them.
- Retained exactly as D-022 built them (all objective, none a semantic judgment): the AJ prompt's
  no-candidate-context statement (guidance *to the LLM*, correctly placed there rather than
  enforced in code); strict Pydantic schema validation; `ScreeningRisk`'s mandatory provenance
  field; exact-substring provenance verification; stable/unique `JobRequirement` IDs; atomic
  failure before any artifact persists; append-only JRA versions; the sanitized `LLMCallLog`
  retained on every failure; the M5 precondition rejecting a zero-requirement current JRA
  (including JobApplication id=9's real, legacy, still-untouched JRA); and the read-only
  "incomplete, not eligible for Gate 1" banner with its server-side enforcement.
- `services/analyze.py::SYSTEM_PROMPT`: lightly reworded (the zero-requirements framing is now
  unconditional, matching the code exactly) plus a new header note stating this prompt is the only
  place AJ's semantic judgment is guided — the example candidate-gap phrasing to avoid remains *in
  the prompt* (instructions to the LLM), just no longer *also* enforced as a code-level rejection
  rule.
- No new human-review gate or source-unit coverage subsystem was added — out of scope per the
  product owner. The existing `job_intake` detail page (every requirement + category + source
  excerpt, every screening risk + excerpt, implied expectations marked inferred) and Gate 1's
  existing "Agent Jobber (re-analyze the posting)" feedback action remain the intended inspect/
  rerun path; Gate 1 remains the one formal combined AJ/AC approval point.
- Testing posture: `test_integrity_validator.py`/`test_integrity_validation_intake.py` assert only
  the three objective properties, including a dedicated case proving legitimate quoted posting
  wording (e.g. "No experience necessary") is never rejected merely for its words. Recorded/golden
  ("cassette") LLM-output testing remains deferred (TEST-002) until several real outputs stabilize
  — unchanged by this decision. This decision also does not assume the `MEMORY_BUILD` model is
  necessarily right for `AJ_ANALYZE`/`AC_NORMALIZE`/`AC_RANK`/`AC_MATCH` — it was reused there only
  as an operational convenience (already registered/credentialed/structured-output-capable);
  per-stage model suitability is something to evaluate from real run outcomes and Gate-1 review,
  not assumed from Candidate Memory bootstrap.

No live provider call was made or authorized by this work; JobApplication id=9's real, legacy JRA
remains inspected read-only only, never rerun or edited. Fully tested (812/812 project tests
passing), `ruff`/`check`/`makemigrations --check` and a genuinely fresh migration on an isolated
database all clean.

## Stage-specific LLM output-token budgets (2026-09-04, D-024) -- COMMITTED

A separately-authorized, real AJ_ANALYZE rerun of JobApplication 9 (against the merged D-022/D-023
code) truncated at exactly 4,096 output tokens (`finish_reason=length`) and produced no valid
`JobRequirementAnalysis` -- `LLMModel.max_output_tokens` was being read as both the model's own
provider capability ceiling *and* every stage's per-request budget, with no way to give AJ_ANALYZE
(a larger prompt/posting than the other four stages) a bigger budget without also raising or
lowering every other stage sharing the same model. See D-024 in `docs/DECISIONS.md` for full
detail; summarized here:

- New, optional `StageModelAssignment.max_output_tokens` field (nullable, `MinValueValidator(1)`,
  cross-validated in `clean()` against the assigned model's own capability whenever that capability
  is set -- enforced both by the admin form and, as defense in depth, by `get_adapter_for_stage`
  itself before any provider call, raising `InvalidStageBudgetError` for a row that reached the
  database without validation).
- `BaseLLMAdapter.effective_max_output_tokens` is the one value every pipeline service now reads
  (`adapter.effective_max_output_tokens`) instead of separately recomputing `adapter.llm_model.
  max_output_tokens or <a locally hard-coded 4096>` -- computed from the model's own capability (or
  one canonical shared default, `llm_provider.adapters.base.DEFAULT_MAX_OUTPUT_TOKENS`) by
  `BaseLLMAdapter.__init__`, then overridden by `get_adapter_for_stage` only when the stage's own
  budget is configured. `job_intake/services/analyze.py` (AJ_ANALYZE) no longer has any
  hard-coded token literal driving its own request budget.
- Retry classification is unchanged: `finish_reason=length` truncation is still `CONFIGURATION`
  (never retried identically) -- this fix changes what budget is *requested*, never how a
  truncation response is classified.
- **Not applied live**: the real development database's `LLMModel`/`StageModelAssignment` rows are
  unchanged (`max_output_tokens=4096` on the shared model, no stage overrides) -- only the
  additive schema migration (`llm_provider.0004_stagemodelassignment_max_output_tokens_and_more`)
  was applied. Raising the model capability to 16,384 and setting `AJ_ANALYZE`'s stage budget to
  8,192 is a separate, explicit follow-up configuration action, not performed by this change.

Fully tested (829/829 project tests passing, 17 new dedicated tests), `ruff`/`check`/
`makemigrations --check` all clean, plus a genuinely fresh, isolated PostgreSQL database (created
and dropped via `docker exec` against the running `cvbuilder-db-1` container, not Django's own
test-runner database) had every migration -- including the new one -- applied cleanly from zero.
No live provider call was made under this decision; JobApplication 9's JRA remains untouched.

## OpenRouter provider integration (2026-09-04, D-025) -- MERGED TO MAIN, registry rows created, smoke bug found and fixed (D-026)

Added `OPENROUTER` as a fourth real `llm_provider` adapter (alongside OpenAI/NVIDIA NIM/Gemini),
with initial support for `z-ai/glm-5.2:free`, structured JSON-schema output, and OpenRouter's
unified reasoning parameter. This work was done in an isolated worktree/branch
(`openrouter-provider` / `worktree-openrouter-provider`) while the primary checkout continued a
separate, controlled M5 task, and was implementation-only at commit time: no `StageModelAssignment`
was pointed at OpenRouter, no existing NVIDIA assignment changed, and no live inference call was
made. See D-025 in `docs/DECISIONS.md` for full detail; summarized here:

- **Schema** (`llm_provider.0005_llmprovider_data_collection_policy_and_more`, additive):
  `LLMProvider.ProviderType.OPENROUTER` and a new `LLMProvider.data_collection_policy` field
  (constrained `DENY`/`ALLOW` choices, default `DENY`) that only `OpenRouterAdapter` reads.
- **Adapter** (`llm_provider/adapters/openrouter.py`): reuses `openai.py`'s
  `build_chat_completion_body`/`parse_openai_style_chat_completion` for the OpenAI-compatible
  parts (messages, temperature, `max_tokens`, `response_format`, response envelope, usage, status
  classification), adding only what's OpenRouter-specific: `stream: false`, optional `top_p`, the
  `reasoning: {"enabled": true}` parameter (sent only when explicitly enabled, never a default,
  rejected before any HTTP call if the target `LLMModel.supports_reasoning` is `False`), the
  `provider: {"require_parameters": true, "data_collection": "deny"|"allow"}` routing object
  (fails closed with `CONFIGURATION` -- no HTTP call -- if `data_collection_policy` is ever an
  invalid stored value), and the two optional attribution headers
  (`OPENROUTER_HTTP_REFERER`/`OPENROUTER_APP_TITLE`, included only when set). A model not marked
  `supports_structured_output` is rejected the same way NIM already does. The final answer is
  parsed only from `choices[0].message.content` -- `reasoning`/`reasoning_content`/
  `reasoning_details` are never read as a substitute and never persisted to `LLMCallLog` (which
  has no content field for any provider). The model id sent is always exactly
  `LLMModel.model_id` -- no fallback to a paid model or a different slug exists in the code.
  `parse_openai_style_chat_completion` (shared with OpenAI/NIM) gained `402`->`CONFIGURATION` and
  `408`->`TIMEOUT` branches; every other status code OpenRouter needs (`404`/`410`/`429`/`5xx`
  incl. `524`/`529`) already classified correctly via the existing generic branches.
- **Smoke test**: `manage.py smoke_test_openrouter` (`--model`, `--reasoning`) added following the
  existing opt-in, never-automatic pattern; `smoke/common.py`'s `CREDENTIAL_ENV_VARS` gained
  `OPENROUTER: "OPENROUTER_API_KEY"` and `run_smoke_test` gained an optional `reasoning_enabled`
  parameter (default `None` -- no behavior change for the three existing smoke commands). **Not
  run live** -- no credential configured in this environment.
- **Admin**: `LLMProviderAdmin.list_display` gained `data_collection_policy`; `LLMModel`/
  `StageModelAssignment`'s existing fields and `full_clean()`-validated admin forms already cover
  OpenRouter with zero additional code.
- **Tests**: 46 new deterministic tests in `llm_provider/tests/test_openrouter_adapter.py` (all
  `requests.post` mocked -- see D-025 for the full list of what's covered). Full suite: 875/875
  passing (up from 829), `manage.py check`/`makemigrations --check --dry-run` (no changes
  detected)/`ruff check .` all clean. The new migration was additionally verified applying
  cleanly from zero on a genuinely fresh, isolated, throwaway `postgres:16-alpine` Docker
  container (a different port, separate from the real `cvbuilder-db-1` dev database), removed
  immediately after verification.
- **Not done in the original D-025 session (separate, later, explicitly-authorized operator
  action)**: verifying `z-ai/glm-5.2:free`'s real context-length/max-completion-tokens/capability
  metadata against OpenRouter's own model listing; creating the real `LLMProvider`/`LLMModel`
  rows; populating `OPENROUTER_API_KEY` in `.env`; running `smoke_test_openrouter`; and, only after
  that smoke test is reviewed and approved, assigning OpenRouter to any `StageModelAssignment`.

**Subsequent continuation (2026-09-04, same day)**: `worktree-openrouter-provider` was fast-forward
merged into `main` (`main` HEAD is now `a541a0a`), `z-ai/glm-5.2:free`'s capability metadata was
independently verified against OpenRouter's own model listing (256,000 context length, 230,400 max
completion tokens, structured output and reasoning both supported, free pricing), and the real
registry rows were created in the development database: `LLMProvider` id **9** (`OpenRouter`,
`data_collection_policy=DENY`) and `LLMModel` id **10** (`z-ai/glm-5.2:free`,
`max_output_tokens=230400`, `supports_structured_output=True`, `supports_reasoning=True`) -- both
still **unassigned** to any `StageModelAssignment`. The one authorized live smoke test
(`smoke_test_openrouter --reasoning`, deliberately run *without* `--model` so it resolved to these
real rows via `select_default_model` rather than creating a separate "OPENROUTER (smoke test)"
duplicate) reached OpenRouter, got a real response, and **crashed with an uncaught `TypeError`**
in the shared response parser -- see "Null-content response parsing fix" below (D-026) for the
root cause, the fix, and what remains before a qualifying smoke run.

## Null-content response parsing fix (2026-09-04, D-026) -- IMPLEMENTED, no live call

The real OpenRouter reasoning smoke test above surfaced a genuine bug: `parse_openai_style_chat_
completion` (`llm_provider/adapters/openai.py`, shared by OpenAI/NVIDIA NIM/OpenRouter) raised an
uncaught `TypeError` when `message.content` came back `None` (the model spent its whole 64-token
smoke budget on reasoning and returned no final answer, `finish_reason=length`), because
`json.loads(None)` isn't a `KeyError`/`IndexError`/`json.JSONDecodeError`. The exception bypassed
`_write_call_log()` entirely -- a real, token-spending call left no audit trail. Full detail,
including the precise root cause and every classification rule, is in D-026 in
`docs/DECISIONS.md`; summarized here:

- **Fix (current, corrected rule)**: the parser now checks `finish_reason` first, before any
  content extraction or JSON parsing is attempted. `finish_reason == "length"` classifies as
  `CONFIGURATION` (a token-budget problem, never retried) **unconditionally** -- regardless of
  whether `message.content` is missing, `None`, non-string, empty, whitespace-only, malformed
  JSON, or even valid JSON, since truncated output may be an incomplete answer and must never be
  accepted as genuine merely because it happens to parse. Every other finish reason: absent/`None`/
  non-string/empty/whitespace-only content classifies `SCHEMA_VALIDATION`; a present-but-unparseable
  JSON string also stays `SCHEMA_VALIDATION`; valid JSON succeeds normally. Reasoning fields
  (`reasoning`/`reasoning_content`/`reasoning_details`) are never read as a content substitute.
  Every path now returns a normal `NormalizedLLMResult`, so `_write_call_log()` always runs -- the
  missing-audit-row gap is closed. Applies uniformly to OpenAI, NVIDIA NIM, and OpenRouter (the
  three adapters sharing this parser); Gemini/Fake were not touched.
- **Amendment (same day)**: the first implementation pass got one case wrong -- it accepted a
  `finish_reason="length"` response as a *success* whenever its content was present and happened to
  parse as valid JSON, instead of always classifying `length` as `CONFIGURATION`. This was corrected
  in a follow-up commit (never by rewriting the first one) after an explicit invariant check caught
  it; see D-026 in `docs/DECISIONS.md` for the full before/after and the specific test that asserted
  the wrong category and was replaced.
- **Smoke budget**: `llm_provider/smoke/common.py` gained `resolve_smoke_max_output_tokens` (64
  default, 4,096 when `--reasoning` is set, an 8,192 smoke-only safety ceiling, never exceeding the
  selected model's own capability, validated before any provider call) and
  `smoke_test_openrouter` gained `--max-output-tokens`. Does not touch `LLMModel.max_output_
  tokens`, any `StageModelAssignment`, or any application-stage budget. Unchanged by the amendment.
- **No migration**: nothing schema-shaped changed; `makemigrations --check --dry-run` confirmed it,
  both before and after the amendment.
- **Tests**: two new modules (`test_null_content_handling.py`, `test_smoke_output_budget.py`) plus
  one addition to `test_network_guard.py` (OpenRouter was missing from its adapter coverage).
  Full suite: 927/927 passing after the initial pass (up from 875); **935/935 passing** after the
  amendment (added valid-JSON-and-`length` coverage at both the parser and adapter levels; replaced
  the one test that had asserted the wrong category). `check`/`makemigrations --check --dry-run`/
  `ruff check .`/`git diff --check` all clean at every step; all migrations re-verified from zero on
  a fresh, isolated, throwaway PostgreSQL container both before and after the amendment.
- **Not done in this session**: no live provider call; no `StageModelAssignment` created or
  changed; JobApplication 9 / JRA id 10 (v2) untouched; no Gate approved. This was implemented,
  corrected, and verified in isolated worktree `openrouter-null-content-fix` / branch
  `worktree-openrouter-null-content-fix`, as two separate commits (the initial fix, then the
  amendment) on top of `main` HEAD `a541a0a` -- neither merged into `main`.
- **Next action**: a separately authorized re-run of `python manage.py smoke_test_openrouter
  --reasoning --max-output-tokens 4096` against the real `LLMProvider` id 9 / `LLMModel` id 10 rows
  to confirm the fix against a genuine reasoning-enabled response, before any `StageModelAssignment`
  is ever pointed at OpenRouter.

## AC_NORMALIZE provider-facing contract alignment (2026-09-05, D-027) -- IMPLEMENTED, no live call

A controlled, real M5 run against `JobApplication` 9 / `JobRequirementAnalysis` 10 (v2, 30
requirements) reached AC_RANK on its first attempt (after a separately authorized AC_RANK budget
increase to 16384) but failed on a second attempt at the earlier AC_NORMALIZE stage: 15 schema-
validation errors, all `equivalents` entries longer than `MAX_TERM_CHARS` (60 characters). A
read-only audit found the limit was real and correctly fail-closed, but reached the model through
no channel at all -- enforced only by a Python-only `@field_validator`
(`schemas.RequirementNormalizationItem._bound_each_term_length`), invisible to
`model_json_schema()` and therefore to the provider-facing request body, and never stated in the
`SYSTEM_PROMPT` in any form. Full detail (root cause, why 60 itself was judged not to be
overreach, and the fix) is in D-027 in `docs/DECISIONS.md`; summarized here:

- **Fix (structural, not semantic)**: `candidate_matching/schemas.py` replaces the custom
  validator with `BoundedTerm = Annotated[str, StringConstraints(max_length=MAX_TERM_CHARS)]`,
  used as the item type for `diagnostic_terms`/`equivalents`/`preserved_technical_terms` --
  `MAX_TERM_CHARS` stays the single source of truth, never duplicated as a literal. This produces
  `maxLength: 60` in `model_json_schema()`, `to_openai_strict_schema()`'s output, and the final
  `response_format.json_schema.schema` every OpenAI-compatible adapter sends -- verified locally
  (invented, non-personal requirement text; no network call) through the actual production request
  builder and adapter body-construction path. `normalize.py`'s `SYSTEM_PROMPT` gained one sentence,
  built from `MAX_TERM_CHARS`, stating each bounded-list entry must be a short term/phrase of at
  most that many characters, never a complete sentence, action clause, or requirement restatement.
- **Boundary preserved, not weakened**: `MAX_TERM_CHARS` itself is unchanged (60); no term is
  truncated/dropped/rewritten after generation; no repair/regeneration call was added;
  `SCHEMA_VALIDATION` is still never retried; `extra="forbid"`, canonical-text length enforcement,
  requirement-ID set-integrity checking, every request/token/retrieval bound, and non-citability of
  normalization output are all unchanged.
- **No migration**: `normalization_limits.py`'s constants are unchanged in value;
  `makemigrations --check --dry-run` confirmed no schema change.
- **Tests**: `candidate_matching/tests/test_normalize.py` gained exact-boundary coverage (60 passes/
  61 fails, list-count boundaries) for all three term-list fields, plus new assertions on the
  generated Pydantic schema, the strict-schema conversion, the final provider-facing request body,
  and the prompt's stated rule. Full suite: 944/944 passing; `check`/`makemigrations --check
  --dry-run`/`ruff check .`/`git diff --check` all clean.
- **Not done in this session**: no live provider call; no `StageModelAssignment`/budget/provider
  row changed (AC_RANK remains at its separately authorized 16384; AC_NORMALIZE/AC_MATCH remain at
  8192); no M5/M6 process ran; no Gate approved; `JobApplication` 9, `JobRequirementAnalysis` 10,
  and `CandidateMemory` 7 untouched. Implemented in isolated worktree
  `ac-normalize-contract-alignment` / branch `worktree-ac-normalize-contract-alignment`, on top of
  `main` HEAD `1530f0b` -- not merged into `main`.
- **Next action**: a separately authorized M5 re-run against `JobApplication` 9 to confirm
  AC_NORMALIZE now succeeds against a real NVIDIA response with the aligned contract, followed by
  the independent Gate-1 FitAssessment review that was deferred pending a successful run.

## Runtime timeout/OpenRouter-diagnostics/GPT-5-readiness work (2026-09-05, D-029/D-030/D-031)

An implementation-plus-independent-verification work package, committed on its own branch
(`worktree-timeout-openrouter-diagnostics`, not merged to `main` in this session). Covers, in full
detail in `docs/DECISIONS.md` D-029/D-030/D-031:

- **`docs/ARCHITECTURE.md` §9a** (new): a consolidated "Runtime agents and LLM stages" section --
  the four agentic components, the six LLM stages and their deterministic support, both human
  review gates, the data-driven `StageModelAssignment` routing invariant, and a Mermaid diagram --
  cross-referencing rather than duplicating the existing per-app (§2) and per-provider (§3) detail.
- **Configurable per-stage read timeout** (D-029): `StageModelAssignment.read_timeout_seconds`
  (migration `llm_provider.0006_stagemodelassignment_read_timeout_seconds`), resolved through the
  same `get_adapter_for_stage` path as D-024's output-token budget. Every adapter now passes
  `requests`' own `(connect, read)` tuple (`BaseLLMAdapter.request_timeout`) instead of a bare
  scalar `timeout=60` -- `DEFAULT_READ_TIMEOUT_SECONDS=60` preserves the exact prior behavior when
  unconfigured; `DEFAULT_CONNECT_TIMEOUT_SECONDS=10` is new, disclosed, and not per-stage
  configurable. Bounds `[1, 300]` seconds are enforced by `full_clean()` and, defensively, at
  `get_adapter_for_stage` resolution time (`InvalidStageTimeoutError`). No stage's
  `read_timeout_seconds` was set on any real row -- the built-in 60s default still governs every
  real stage exactly as before.
- **OpenRouter key-status service and 429 diagnostics** (D-030): `llm_provider/
  openrouter_key_status.py`'s `fetch_openrouter_key_status()` performs an explicit, operator-
  triggered `GET /api/v1/key` (never `/api/v1/credits`, never automatic, never during a retry loop),
  reporting only documented non-secret quota/limit fields into the new `OpenRouterKeyStatus` model.
  `llm_provider/adapters/openrouter.py`'s new `parse_openrouter_rate_limit` retains sanitized,
  bounded 429 diagnostics (`retry_after_seconds`, `limit`, `remaining`, `reset`, upstream-vs-unknown
  `source`/`upstream_provider`) onto the new `LLMCallLog.rate_limit_diagnostics` `JSONField`
  (migration `llm_provider.0007_llmcalllog_rate_limit_diagnostics_and_more`) -- scoped to
  `OpenRouterAdapter` only, the shared OpenAI-compatible 429 branch used by OpenAI/NVIDIA NIM is
  untouched. An operator-facing admin view (`admin:llm_provider_openrouter_diagnostics`, linked from
  the `LLMProvider` changelist) shows the last key-status snapshot, a POST-only/CSRF-protected
  "Refresh OpenRouter status" action, a locally-observed-vs-authoritative-data distinction, and the
  most recent 25 sanitized OpenRouter `RATE_LIMIT` log rows -- no Candidate Memory/job/resume/
  employer/application content appears anywhere in this view (it queries only `llm_provider`'s own
  models).
- **`gpt-5` Chat Completions compatibility fix, no live call** (D-031): confirmed against current
  official OpenAI documentation that `gpt-5` (exactly that slug, never `gpt-5-chat-latest` or any
  other variant) supports Chat Completions, structured outputs, and `reasoning.effort` in
  {minimal, low, medium, high}, with a 128,000-token output ceiling -- but rejects the pre-existing
  shared request body on two counts (`max_tokens` unsupported, must be `max_completion_tokens`;
  `temperature` unsupported at any non-default value, and this codebase's own
  `NormalizedLLMRequest.temperature` defaults to `0.0`). Fixed in `llm_provider/adapters/openai.py`:
  `build_chat_completion_body` gained two keyword-only, default-preserving overrides, and
  `OpenAIAdapter._call_once` now branches on the existing `LLMModel.supports_reasoning` flag to use
  them (plus, only when explicitly requested, OpenAI's own top-level `reasoning_effort` field) --
  zero behavior change for NVIDIA NIM, OpenRouter, or any non-reasoning OpenAI model. No
  `LLMProvider`/`LLMModel`/`StageModelAssignment` row for OpenAI/`gpt-5` was created; `AB_BUILD`
  remains unassigned.
- **Tests**: 100 new deterministic tests across
  `test_stage_read_timeouts.py` (19), `test_openrouter_key_status.py` (11),
  `test_openrouter_rate_limit_diagnostics.py` (13), `test_openrouter_diagnostics_view.py` (15), and
  `test_gpt5_compatibility.py` (11), plus incidental fixture additions -- full `llm_provider` suite
  278/278 passing, `ruff check .` clean, `manage.py check`/`makemigrations --check --dry-run` clean.
- **Confirmed unchanged**: `AJ_ANALYZE`/`MEMORY_BUILD`/`AC_MATCH`/`AC_RANK` remain on NVIDIA
  Nemotron, `AC_NORMALIZE` remains on OpenRouter `z-ai/glm-5.2:free`, `AB_BUILD` remains unassigned
  -- exactly the "Known state" this work package was given at the start. `JobApplication` 9 (JRA
  id 10/version 2, 30 requirements), Gate 1's unapproved status, and the absence of any
  `FitAssessment`/`ResumeDraft` are all unchanged. No M5, M6, Gate action, provider smoke test, or
  live LLM inference of any kind occurred in this session.

## OpenAI strict-schema `required`-completion fix (2026-09-05, D-032)

A schema-correction-plus-test work package, committed on its own branch
(`worktree-openai-strict-schema-fix`, not merged to `main` in this session). Full detail in
`docs/DECISIONS.md` D-032; summarized here:

- **Confirmed incident**: a synthetic OpenAI `gpt-5` diagnostic request 400'd --
  `type=invalid_request_error`, `param=response_format`, "'required' is required to be supplied
  and to be an array including every key in properties. Missing 'diagnostic_terms'." Root cause:
  Pydantic omits a defaulted field (`diagnostic_terms`/`equivalents`/`preserved_technical_terms`,
  all `default_factory=list`) from JSON Schema `required`, but OpenAI's own documented rule is
  "all fields must be required".
- **Fix**: `llm_provider/schema_translation.py`'s `_complete_object_contract()` now recursively
  completes `required`/`additionalProperties` for every object node (root, `$defs`, nested,
  array-item, `$ref`-reached) in both `to_openai_strict_schema()` (OpenAI) and a new
  `to_openai_compatible_strict_schema()` (NVIDIA NIM/OpenRouter); `_assert_object_contract_
  complete()` re-checks the result and raises `OpenAIStrictSchemaContractError` if it is ever
  incomplete -- caught by all three real adapters (`_call_once`) as a pre-HTTP `CONFIGURATION`
  failure, never sent, never retried.
- **Provider-specific keyword projection**: only `to_openai_strict_schema()` strips
  `minLength`/`maxLength` (confirmed absent from OpenAI's documented Structured Outputs supported-
  keyword subset, `developers.openai.com/api/docs/guides/structured-outputs`) --
  `to_openai_compatible_strict_schema()` keeps them for NVIDIA/OpenRouter, which are not confirmed
  to reject either keyword and which this project already relies on to enforce `MAX_TERM_CHARS=60`
  server-side.
- **Canonical validation unchanged**: `BaseLLMAdapter.generate()`'s post-response
  `model_validate()` (D-005) was not touched -- verified directly that a mocked OpenAI response
  exceeding the (now OpenAI-schema-absent) 60-char term bound, 500-char text bound, or 100-item
  array bound still fails `SCHEMA_VALIDATION`.
- **Prompt**: `candidate_matching/services/normalize.py`'s `SYSTEM_PROMPT` gained one sentence
  stating the three bounded term lists are always present and must be `[]`, never omitted or
  fabricated, when empty.
- **HTTP-400 classification boundary**: deferred, not fixed in this decision (see D-032's own
  entry for the full gating rationale) -- this fix eliminates the schema-shape cause of the
  confirmed incident before any HTTP call, so no residual classification gap remains for this
  specific failure mode.
- **Tests**: 26 new deterministic tests (`llm_provider/tests/test_strict_schema_required_
  completion.py`) plus 5 new/updated tests in `candidate_matching/tests/test_normalize.py`. Full
  suite (`llm_provider`, `candidate_matching`, `candidate_memory`, `job_intake`, `resume_builder`,
  `job_applications`, `reviews`): 1042/1042 passing; `manage.py check`/`makemigrations --check
  --dry-run`/`ruff check .`/`git diff --check` all clean; zero live provider calls.
- **Not done in this session**: no live provider call; no `LLMProvider`/`LLMModel`/
  `StageModelAssignment` row changed; no M5/M6 process ran; no Gate approved; `JobApplication` 9,
  `JobRequirementAnalysis` 10, and `CandidateMemory` 7 untouched.

## What does not exist

- The M7 dashboard (list/detail views, `application_outcome` operator action) and any integration
  wiring beyond the pointer-based freshness checks M5/M6 already enforce individually.
- Any pipeline stage other than `MEMORY_BUILD`, `AJ_ANALYZE`, `AC_RANK`, `AC_MATCH`, and
  `AB_BUILD` -- all five now exist and are exercised by the automated suite via the `FakeAdapter`;
  none has been exercised against a live provider (no credentials configured in this environment
  for any stage).
- Any real-world URL fetch verification (only mocked HTTP responses have been exercised).
- Any live-provider verification of the OpenAI/NVIDIA NIM/Gemini adapters (opt-in, operator-run,
  not performed in this environment -- no credentials configured).
- Any remote/CI configuration (not required; local quality commands remain the standard).
- Any `StageModelAssignment` pointing at OpenRouter, or any successful (non-crashing)
  live-provider verification of the OpenRouter adapter -- the real `LLMProvider` (id 9)/`LLMModel`
  (id 10) rows and a populated `OPENROUTER_API_KEY` do now exist (see "OpenRouter provider
  integration" and "Null-content response parsing fix" above), but the one smoke test run against
  them crashed before D-026's fix; a fresh, separately authorized smoke re-run is still needed.

## Decisions (see `docs/DECISIONS.md` for full detail)

D-001 through D-015 are all APPROVED (several "with modification"); D-013 is superseded by D-010.
D-016 (`FAILED` lifecycle state) remains PROPOSED, not yet product-owner-approved. D-017 (line-wrap
provenance risk), D-018 (Candidate Memory recovery), D-019 (the deterministic static-profile
boundary — `CareerEngagement`/`ClaimEngagementMapping`, see below), and D-020 (M5/M6 audit
hardening -- bounded relevance retrieval, engagement-correct evidence, gate/failure hardening, see
"M5/M6 audit hardening" above) are all **APPROVED AND IMPLEMENTED**. M5 and M6 were originally
implemented within the boundaries of the already-approved decisions in force at the time
(principally D-006, D-010, D-014, D-019); D-020 was added when an independent audit found that
implementation had not actually satisfied D-015's bounded-retrieval clause and D-019's engagement-
placement guarantee, and records the correction. D-021 (rarity-aware BM25 scoring + `AC_NORMALIZE`
requirement normalization, see "Bounded retrieval recall repair" above) is **APPROVED AND
IMPLEMENTED** -- verified against a requirement-level evidence-coverage standard (the product
owner's explicit replacement for exact-claim-ID recall) rather than exact-claim-ID recall against
the five gold profiles; an acceptance review confirmed the three claims short of exact-ID recall
are all genuinely redundant with claims that did reach the pool. D-022/D-023 (Agent Jobber
integrity hardening and its course correction, see the sections above) and D-024 (stage-specific
LLM output-token budgets, see "Stage-specific LLM output-token budgets" above) are all **APPROVED
AND IMPLEMENTED**. D-025 (OpenRouter provider integration, see "OpenRouter provider integration"
above) is **APPROVED AND IMPLEMENTED**, now merged to `main` with real (unassigned) registry rows.
D-026 (null-content response parsing fix + separated smoke budgets, see "Null-content response
parsing fix" above) is **APPROVED AND IMPLEMENTED** -- a provider-boundary correction found by
D-025's own smoke test, still no stage assignment and no live call performed under D-026 itself.
D-027 (AC_NORMALIZE provider-facing contract alignment, see "AC_NORMALIZE provider-facing contract
alignment" above) is **APPROVED AND IMPLEMENTED** -- a contract-alignment correction found by a
real M5 run's AC_NORMALIZE failure against `JobApplication` 9. Since first written, D-027 has been
independently re-audited, fast-forward merged to `main` (HEAD `011a628`), and qualified live against
both NVIDIA (`LLMCallLog` id 310) and OpenRouter `z-ai/glm-5.2:free` (`LLMCallLog` id 311) -- both
succeeded, 30/30 requirement IDs, no length violations. D-028 (provider/model fallback is an
operator-authorized rerun, never an automatic or invisible switch -- see `docs/DECISIONS.md`) is
**APPROVED** as a policy record only, ahead of the next authorized M5 run; no fallback mechanism or
UI exists yet, and none is implemented by D-028 itself. D-029 (configurable per-stage read timeout),
D-030 (OpenRouter key-status service + sanitized 429 diagnostics + operator UI), and D-031 (`gpt-5`
Chat Completions compatibility fix, no live call) -- see "Runtime timeout/OpenRouter-diagnostics/
GPT-5-readiness work" above -- are all **APPROVED AND IMPLEMENTED**. D-032 (OpenAI Structured
Outputs strict-schema `required`-completion fix, no live call -- see "OpenAI strict-schema
`required`-completion fix" above) is **APPROVED AND IMPLEMENTED**; its own HTTP-400 classification
sub-question is explicitly deferred as a distinct follow-up, not itself blocking. Neither D-016 nor
anything else is blocking for M7 as currently scoped.

## Deterministic static-profile boundary (D-019, 2026-09-03)

Implemented ahead of M5/M6, before either milestone starts: `CareerEngagement` (an admin-editable
registry, independent of any `CandidateMemory` revision's own lifecycle, gated by its own
`approval_status`) and `ClaimEngagementMapping` (a reviewable, deterministic mapping between a
`MemoryClaim` and a `CareerEngagement` — a separate table, never a `MemoryClaim` field, so it never
conflicts with the ACTIVE-revision-content-freeze invariant). `services/engagement_mapping.py`
proposes a mapping only on an exact normalized identity match, leaving anything ambiguous or
unmatched for the operator. `services/career_engagement.py` computes total non-overlapping
experience across engagements (merging overlapping ranges so they are never double-counted).
`services/static_profile_boundary.py` defines the forward-compatible M5/M6 contracts themselves --
`RequirementEvidenceReference` (`supporting_memory_claim_ids` + `supporting_engagement_ids`),
`EngagementNarrativeOutput`/`EngagementBullet` (no employer/title/location/date field, `extra=
"forbid"`), `render_engagement_header` (resolves those fields exclusively from an `APPROVED`
`CareerEngagement`, fails closed on an unknown/unapproved `engagement_id`), and two local
static-requirement assessors (tenure, location) that make an LLM call unnecessary for that kind of
disposition. Migration `candidate_memory.0006_careerengagement_claimengagementmapping`; tests in
`test_career_engagement.py`, `test_engagement_mapping.py`, `test_static_profile_boundary.py`
(53 new tests, all deterministic, no LLM adapter involved). Three real `CareerEngagement` rows
(CE-0001 Ford, CE-0002 Continental, CE-0003 Maruti Suzuki) were subsequently created and approved
by the operator, with operator-approved organization aliases/programme scopes, and 178 narrative
`ClaimEngagementMapping` rows are `APPROVED` against them (see the aliases/mapping work recorded in
this file's git history and `docs/DECISIONS.md` D-019's refinement) -- this real registry is what
M5/M6's real end-to-end manual walkthrough (see the M6 section above) retrieved and rendered
against.

## Next action

M1 through M6 are implemented, committed, and verified (M1-M4 against the `FakeAdapter`/mocked
HTTP as already documented above; M5-M6 additionally verified in a real, rolled-back end-to-end
walkthrough against the real ACTIVE CandidateMemory and a real APPROVED CareerEngagement). Per
`docs/IMPLEMENTATION_PLAN.md`'s dependency graph, Milestone M7 (integrated per-job workflow,
chain-wide freshness enforcement, and the dashboard) depends on M3, M4, M5, and M6, all now
complete -- M7 may proceed. Per this work package's explicit instruction, M7 was **not started**
in this session. Before any live M5->Gate1->M6->Gate2 run against a real provider, the operator
must: configure a real credential in `.env` for whichever provider/model will serve `AC_MATCH` and
`AB_BUILD`, create the corresponding `LLMProvider`/`LLMModel`/`StageModelAssignment` rows (none
exist yet for either stage), and confirm CE-0001/CE-0002/CE-0003's mapped narrative claims are the
intended evidence set for a real job application before approving either gate for real.

**OpenRouter addendum (2026-09-04, D-025/D-026)**: OpenRouter is now available as an additional
provider option, merged to `main`, with real (unassigned) `LLMProvider`/`LLMModel` registry rows
(see "OpenRouter provider integration" and "Null-content response parsing fix" above) -- this does
not change which provider currently serves `AC_MATCH`/`AB_BUILD` and does not alter the operator
steps above in any way. A known parser bug the first live smoke test surfaced (D-026) has been
fixed; a fresh, separately authorized smoke re-run against the real registry rows is the remaining
step before OpenRouter could be considered for any stage assignment.

## Maintenance rule for this file

Update this file after any milestone completes or any meaningful implementation step lands.
Describe the repository as it truly is -- never mark something present, tested, or working that
has not actually been built and verified.
