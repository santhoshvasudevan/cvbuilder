# Test Strategy

Status: pre-implementation — no tests exist yet. This document follows requirements.md §12's
phased strategy exactly; it does not introduce a stricter or looser regime than what was
specified.

## Phase 1 (now): deterministic tests for non-LLM logic

**Explicit decision (TEST-001, requirements.md §12)**: no recorded/golden/cassette LLM-response
testing yet. Prompts and pipeline logic are still being iterated on; locking down "golden"
responses this early would produce tests that need constant rewriting rather than tests that catch
real regressions. This is a deliberate deferral, not an oversight — do not build cassette
infrastructure in Phase 1 no matter how tempting it is once a real provider call is working.

What IS tested from day one, concretely, per milestone (see `docs/IMPLEMENTATION_PLAN.md` for full
milestone scope):

- **M2 (`llm_provider`)**: retry classification (transient vs. non-transient error matrix,
  including the "never retry after partial stream" rule); error normalization/sanitization (typed
  taxonomy, no raw provider content leaks into a log or DB field); provider/model registry CRUD
  and `StageModelAssignment` routing (swapping an assignment changes routing with zero pipeline
  code change); per-provider schema-translation logic (e.g. Gemini's enum-stripping and
  `$ref`/`$defs`-flattening produces a schema in the expected reduced dialect); `LLMCallLog` token
  fields (input/cached-input/output/total) recorded correctly per call — all of this against a
  fake adapter, no live API calls, per this repo's standing rule that the **automated** M2 suite
  must remain deterministic and must never require live provider credentials.

  **(v1.1 addition) Manual opt-in provider smoke verification** — separate from the automated
  suite above, not a Phase 1/Phase 2 test-suite item: for each configured provider (OpenAI, NVIDIA
  NIM, Gemini, OpenRouter), a minimal structured-output request through the actual adapter, run
  only when the operator explicitly initiates it, using credentials from `.env`. This never runs
  automatically in CI or as part of `manage.py test`, never persists raw sensitive provider
  request/response bodies (nor, for OpenRouter, any reasoning/chain-of-thought content), and
  records safe `LLMCallLog` metadata where appropriate. A provider with no configured credential is
  reported as **not live-verified**, never as a failure of the deterministic suite — passing mocked
  tests must never be reported as "this provider is operationally verified."

  **(2026-09-04 addition, D-025) OpenRouter provider transport tests**: `llm_provider/tests/
  test_openrouter_adapter.py` covers OpenRouter-specific transport/configuration correctness the
  same way the three existing adapters are covered — endpoint/auth, missing-credential
  short-circuit, optional attribution headers, exact free-model-slug/no-fallback, request-body
  construction (including the `provider` routing object and its fail-closed invalid-policy case),
  reasoning enable/omit/reject and its never-substitutes-for-content guarantee, no raw reasoning in
  `LLMCallLog`, response normalization, status-code classification (including `402`/`408`/`524`/
  `529`), and bounded retry — all against a mocked `requests.post`, no live credential or network
  required. Per this file's standing rule (and CLAUDE.md), none of these tests assert resume
  quality or recruiter-judgment semantics — that stays out of scope for a provider-transport
  test file, same as for the other three adapters.

  **(2026-09-04 addition, D-026) Null/invalid final-content handling + smoke-budget tests**: a
  live OpenRouter smoke test (run after D-025 was merged to `main` and real registry rows were
  created) surfaced a genuine uncaught-exception bug in the shared `parse_openai_style_chat_
  completion` parser when a reasoning-enabled model returned `message.content=None`. Fixed and
  covered by `llm_provider/tests/test_null_content_handling.py` (the exact real response shape
  plus its content-type/emptiness/finish_reason variants, exercised directly against the shared
  parser and end-to-end through representative NVIDIA NIM/OpenAI/OpenRouter adapter calls —
  proving exactly one sanitized `LLMCallLog` row is written per logical call, zero retries, usage/
  finish-reason preserved, and that no prompt/reasoning/credential content ever reaches a logged
  field) and `llm_provider/tests/test_smoke_output_budget.py` (the separated reasoning/
  non-reasoning smoke output-token budgets and their validation, including that an invalid value
  never reaches `requests.post` and that no `LLMModel`/`StageModelAssignment` row is ever mutated
  by a smoke run). This is still a Phase 1, fully deterministic, mocked-`requests.post` addition —
  no live credential or network involved in the automated suite; the manual opt-in smoke
  verification item above remains the only place a real OpenRouter call is ever made, and only by
  explicit operator action.

  **Same-day amendment**: the first pass at this fix had one incorrect rule — a `finish_reason=
  "length"` response with content that happened to still parse as valid JSON was accepted as a
  success rather than always classified `CONFIGURATION`. `test_null_content_handling.py` was
  corrected accordingly (the wrong-category test was replaced, not silently deleted — see the
  file's own docstring amendment and D-026 in `docs/DECISIONS.md`), and gained explicit coverage
  for exactly that case (valid JSON + `finish_reason=length` → `CONFIGURATION`, never success) at
  both the shared-parser and representative-adapter levels.
- **M3 (`candidate_memory`, D-015)**: `confirmation_status` state transitions (`unconfirmed` →
  `confirmed`/`retired`/`BLOCKED_CONFLICT`, and that retrieval excludes anything not
  `confirmed`+`resume_eligible`); versioning invariants (a new `CandidateMemory` revision leaves
  prior revisions' claims untouched); the claim-traceability validator (a claim whose
  `MemoryClaimSupport` quotation isn't an exact substring at its stated line range is flagged) —
  plus the following D-015-specific items:
  - exact source quotation and line-range resolution for `MemoryClaimSupport` (quotation matches
    `MemorySourceDocument.raw_content` at `start_line`/`end_line`, and `quotation_hash` matches).
  - source `content_sha256` validation (a mutated fixture document's hash no longer matches).
  - multiple support passages for one claim (a fixture claim with one `PRIMARY` English support
    and one `GERMAN_EXPRESSION` support persists both, not just one).
  - English/German duplicate grouping (two fixture expressions of the same underlying fact share
    one `duplicate_group_key`).
  - canonical English storage (a claim built from a German-only source still has an English
    `canonical_text`).
  - evidence vs. constraint vs. positioning classification (a fixture evidence sentence becomes a
    `MemoryClaim`; a fixture constraint sentence — e.g. "currently learning Go" — becomes a
    `CandidateRule` with `rule_type = LEARNING_STATUS`, not a claim; a fixture positioning sentence
    — e.g. a suggested target title — becomes a `CandidateRule` with `rule_type = POSITIONING`,
    never a claim).
  - a recommended/suggested target title is never treated as a historical employment title (a
    fixture with both present in source material keeps them as distinct `claim_type`/`rule_type`
    records).
  - awareness/learning `experience_level` never becomes `PROFESSIONAL_DELIVERY` or higher (a
    fixture claim tagged `AWARENESS` is rejected if an extraction attempts to store it as
    `PROFESSIONAL_DELIVERY`).
  - unresolved `MemoryConflict` blocks claim eligibility (a fixture conflict with `status = OPEN`
    makes both involved claims read `BLOCKED_CONFLICT` and excludes them from retrieval; resolving
    the conflict restores eligibility for the resolved claim only).
  - a non-conflicting factual claim from an `OPERATOR_APPROVED` bootstrap source becomes
    `confirmed` automatically, per D-015's auto-confirm rule.
  - an unsupported or malformed extraction remains `unconfirmed`/rejected **despite** its source
    being `OPERATOR_APPROVED` — source approval is not extraction approval.
  - the snapshot file (`docs/CANDIDATE_MEMORY_SNAPSHOT.md`) is excluded from import — running the
    bootstrap command must not treat the snapshot as a fourth source document.
  - unchanged-source reuse (a fixture revision with one unchanged and one changed document only
    reprocesses the changed one, per D-002).
  - a new `OPERATOR_UPDATE` through the M3 UI creates a new `CandidateMemory` revision rather than
    mutating the active one.
  - bounded AC retrieval (M5) excludes the full source documents and reference-only content — a
    retrieval-service unit test asserts only claim/rule records are returned, never raw source
    document content or the snapshot.

  **(M0.1 audit fix) Revision lifecycle and activation tests** — planned for M3, not yet
  implemented, closing the blocking gap found in the M0.1 pre-implementation audit
  (`docs/ARCHITECTURE.md` §4 `CandidateMemory`, `docs/IMPLEMENTATION_PLAN.md` M3):
  - an `ACTIVE` (or `SUPERSEDED`) revision's `MemoryClaim`, `MemoryClaimSupport`, `CandidateRule`,
    and `MemoryConflict` rows cannot be edited — confirming/correcting/retiring/restoring a claim,
    or resolving a conflict, against such a revision is rejected by the service layer.
  - using the "add/update profile" workflow against the current `ACTIVE` revision always creates a
    new revision (`base_revision` pointing at it) rather than editing it in place.
  - activating a revision atomically supersedes the previously-`ACTIVE` one — a fixture check
    confirms both transitions happen together, never leaving two revisions `ACTIVE`.
  - only one revision can be `ACTIVE` at a time — enforced as a service/DB-level invariant, not
    just a convention.
  - a revision with an unresolved `MemoryConflict` may activate only if every claim it affects
    remains `BLOCKED_CONFLICT`/ineligible; the activation view surfaces an explicit warning
    listing what's being excluded.
  - resolving a conflict that was left open at activation, once the revision is `ACTIVE`, requires
    creating a new revision — there is no path that mutates `MemoryConflict.status` on an `ACTIVE`
    revision directly.
  - a revision with a failing provenance validation (quotation/hash mismatch) or failing
    classification/eligibility validation cannot be activated until the failure is fixed.

  All of the above are ordinary deterministic Django/Python tests against fixture markdown and a
  fake LLM adapter, consistent with Phase 1. This M0.1 addition does **not** introduce cassette
  tests, live-provider tests, or vector-search tests — those remain out of scope for the reasons
  already stated above (cassette: TEST-002, deliberately deferred; live-provider: the M2 opt-in
  manual smoke path only, never the automated suite; vector-search: no vector database is
  required for v1, per D-015).
  - **The deterministic static-profile boundary (D-019, 2026-09-03)**: `CareerEngagement`'s own
    derived behavior (`duration_months` — including the disclosed missing-month convention and the
    `end_status=UNKNOWN` → `None` fail-closed case, `is_current`, `displayed_organization` across
    all three `presentation_mode` values, `title_for_language` with and without a stored localized
    entry) and `services.career_engagement.total_non_overlapping_experience_months` (additive for
    non-overlapping engagements, merged — never double-counted — for overlapping ones, with
    `UNKNOWN`-end engagements excluded and reported, not guessed). `services.engagement_mapping.
    propose_claim_engagement_mappings`: exact-match proposal, ambiguous (2+ candidate matches) and
    unresolved (0 matches) left untouched rather than guessed, idempotent re-run, and — the key
    regression guard — a mapping can be proposed and approved for a claim belonging to an
    already-`ACTIVE` `CandidateMemory` revision without violating that revision's frozen-content
    invariant. `services.static_profile_boundary`: `EngagementNarrativeOutput`/`EngagementBullet`
    (M6's real output schema, `resume_builder/schemas.py`, mirrors this shape) reject any employer/title/location/date field via Pydantic's
    `extra="forbid"`; `render_engagement_header` refuses an unknown or non-`APPROVED` `engagement_id`
    outright; a rendered header's title/organisation/dates exactly match the stored `CareerEngagement`
    fields and are never machine-translated regardless of the requested `language`; `assess_tenure_
    requirement_locally`/`assess_location_requirement_locally` (used directly by M5's real `candidate_matching/services/static_requirements.py`) and `RequirementEvidenceReference` (accepting `supporting_memory_claim_ids` and
    `supporting_engagement_ids` together, rejecting unknown fields). All ordinary deterministic
    tests, no LLM adapter involved at all in this group.
- **M4 (`job_intake`, `job_applications`)**: fetch-success vs. fetch-failure branching (a fixture
  returning an unparseable/too-short body triggers the fallback-to-paste UI path, not a silent
  low-quality result); `source_type` + raw input always persisted regardless of path taken; every
  material requirement is assigned a stable, unique `JR-xxx` ID and category within one
  `JobRequirementAnalysis` version; a new `JobApplication` is created/attached on intake with
  `pipeline_phase` starting at `NEW`. **(D-022, 2026-09-04, committed; course-corrected same day,
  D-023)** a real controlled Gate-1 preparation run surfaced a schema-valid `AgentJobberAnalysis`
  with zero `JobRequirement`s and posting responsibilities recast as candidate-gap screening risks
  (JobApplication id=9). The first fix (D-022) over-reached into judging *meaning* (a candidate-gap
  phrase blocklist, a posting-length "substantive" threshold, a misclassification-inference
  heuristic); D-023 corrected the boundary per the product owner's explicit rule -- "LLMs decide
  meaning and wording. Deterministic code protects truth, boundaries and lifecycle." --
  removing all three. `job_intake/validators/integrity.py::find_integrity_violations` (renamed from
  `sanity.py`) is the corrected, deterministic *integrity-only* gate between schema validation and
  persistence, testing exactly three objective properties, no more: at least one requirement exists
  (unconditional, no length threshold -- a short posting with one explicit requirement rejects a
  zero-requirement response exactly as readily as a long one); no exact duplicate requirement (same
  category + normalized text); and verifiable provenance (a MANDATORY/PREFERRED/RESPONSIBILITY
  requirement's or any screening risk's `source_context` is an exact substring of the posting
  actually analyzed -- `ATS_SIGNAL`/`IMPLIED_EXPECTATION` remain exempt, since neither is expected
  to carry a literal quote by design). `test_integrity_validator.py` tests all three directly,
  including a dedicated proof that legitimate quoted posting wording (e.g. "No experience
  necessary") is never rejected merely for its words -- and that a response with numerous
  gap-phrased screening risks is *still* rejected when requirements is empty, but only for that one
  objective reason, never for the risks' wording or count. `test_integrity_validation_intake.py`
  covers the same ground at the `run_intake` integration level -- atomicity (nothing persisted, no
  pipeline-phase advancement), the underlying `LLMCallLog` row still written without raw posting
  content, and a valid analysis (including one containing legitimate "no experience necessary"-
  style wording) still persisting normally. `candidate_matching/tests/test_fit_assessment.py` adds
  the M5-side guard: `build_fit_assessment` refuses a current JRA with zero `JobRequirement`s,
  exercised against a fixture built the same way the real legacy JRA id=9 exists (JobRequirement
  rows never created), proving the check is a fresh runtime property, not something that has to be
  baked in at creation time. No test in this codebase may assert that deterministic code correctly
  judged a semantic classification (D-023) -- that would itself be evidence of the D-022 overreach
  recurring.
- **M5 (`candidate_matching`, `reviews`), implemented 2026-09-03**: retrieval precision (irrelevant/unconfirmed claims
  excluded from a fixture memory); the disposition-coverage validator (every relevant
  `JobRequirement` in a fixture gets exactly one `RequirementAssessment`; a fixture with a known
  `GAP` always surfaces it, never silently upgraded to `MATCH`; a `MATCH`/`PARTIAL` lacking
  confirmed-claim evidence is rejected); review-gate state transitions (approve → proceed-ready,
  advancing `pipeline_phase` to `PREPARATION`; feedback → `needs_rework` plus a new version per
  D-010, verified to survive a process restart since it's plain DB state, not in-memory/paused
  execution — this is the direct test of HITL-002/HITL-004). **(D-019)** a fixture static
  requirement (tenure/dates/location/employment relationship) is assessed via
  `candidate_memory.services.static_profile_boundary`'s local assessors and cited by
  `supporting_engagement_ids` alone, with zero LLM calls made for that disposition.
  **(D-021, 2026-09-04, committed)** the deterministic BM25/phrase/acronym scoring layer
  (`lexical_relevance.py`/`candidate_generation.py`) is tested purely as a lexical-scoring
  mechanism against small synthetic fixtures (no live corpus, no cassette) — rare-vs-common-term
  direct score comparison, exact-phrase/acronym matching, determinism across repeated calls,
  empty/punctuation-only input; the new `AC_NORMALIZE` requirement-normalization stage
  (`normalize.py`) is tested the same way every other stage in this codebase is (`test_rank.py`'s
  pattern): a `FakeAdapter`/`StageModelAssignment(AC_NORMALIZE)` scripted with a fixed response,
  proving requirement-id preservation, schema/count/length-bound enforcement (an over-large or
  malformed response fails closed, never silently truncates), and that only
  requirement_id/text/language ever reach the stage's prompt — never a MemoryClaim,
  CareerEngagement, or candidate/employment field. `test_normalize.py` also has synthetic
  paraphrase/German-to-English/acronym-matching fixtures proving the *mechanism* bridges a genuine
  vocabulary gap. Retrieval quality itself is verified against **requirement-level evidence
  coverage**, not exact-claim-ID recall: a live, real-corpus five-profile gold-set measurement (not
  a committed automated test, since it depends on the real ACTIVE CandidateMemory's actual content)
  found 16/19 predeclared claim IDs reaching the pool, and an acceptance review — comparing actual
  capability/scope/engagement/evidence strength, not wording — confirmed the three unreached claims
  are each redundant with claims that did reach the pool for the same requirement (see D-021's
  Finding 3). No unit test asserts a specific real claim ID must appear in the pool, since that
  would overfit the scorer to one gold set rather than testing the scoring mechanism itself.
  **(D-027, 2026-09-05)** `test_normalize.py` additionally asserts the *provider-facing* contract,
  not just local Python validation: the generated Pydantic `model_json_schema()`, the
  `to_openai_strict_schema()` conversion, and the final OpenAI-compatible request body (built
  locally with invented, non-personal requirement text, no network call) all expose
  `maxLength: MAX_TERM_CHARS` on every bounded term-list item, and the `SYSTEM_PROMPT` states the
  same limit in natural language — closing the gap a real M5 run found, where a limit was enforced
  locally but invisible to the model. **(D-032, 2026-09-05)** `test_normalize.py` was updated for
  the provider-specific schema dialect split this decision introduced:
  `to_openai_strict_schema()` (OpenAI's own outbound schema) no longer carries `maxLength` (not in
  OpenAI's documented supported subset) while `to_openai_compatible_strict_schema()` (NVIDIA/
  OpenRouter) still does — both dialects are asserted separately, and a new adapter-level test
  proves an over-length term in a *returned* OpenAI response still fails canonical Pydantic
  validation even with `maxLength` absent from what was sent. A new
  `llm_provider/tests/test_strict_schema_required_completion.py` recursively asserts
  `set(required) == set(properties.keys())` and `additionalProperties: false` for every object
  node (root, `$defs`, nested, array-item, `$ref`-reached) of every registry-used schema, in both
  dialects — this is the general form of the same gap D-027 found for term length: a constraint
  invisible to the provider must still be enforced canonically after the fact, and here the
  invisible thing was entire required fields, not just a length bound.
- **M6 (`resume_builder`), implemented 2026-09-03**: the no-fabrication validator running against the **structured**
  representation before any markdown is rendered (a fixture `ResumeElement` with no evidence, or
  with an ID that doesn't resolve to an existing/confirmed/eligible `MemoryClaim`, is rejected
  before rendering); **(D-019)** a fixture `ExperienceSection` referencing an unknown or
  non-`APPROVED` `engagement_id` is rejected the same way, before rendering; a rendered header
  exactly matches the referenced `CareerEngagement`'s stored fields; the markdown renderer produces
  exactly the structure in
  `docs/RESUME_OUTPUT_STRUCTURE.md` §4 and omits internal-planning-only sections; draft versioning
  on regeneration; freshness/staleness detection keyed on `JobApplication.current_fit_
  assessment_id` (D-006) rather than a timestamp (mutating the underlying `FitAssessment` version
  pointer after a draft exists is caught at the next read).
- **M7 (integration, `job_applications` dashboard), implemented 2026-09-06** (isolated worktree/
  branch, not yet merged to `main`): cross-app status-transition wiring for a full job-application
  run; chain-wide freshness enforcement at every step boundary, including the *transitive* case (a
  `ResumeDraft` in sync with its own `FitAssessment` but that `FitAssessment` now stale relative to
  a newer JRA) — not LLM output quality, which stays a manual review item, per below;
  `pipeline_phase` NEW→ANALYSIS→PREPARATION→READY transitions occur automatically at the right
  points and never in an impossible combination; `application_outcome` is independent of
  `pipeline_phase` (marking `APPLIED` then regenerating a resume does not revert
  `application_outcome`, verified by a dedicated regression test); the dashboard list view
  surfaces an accurate derived status for a fixture set of job applications in every pipeline
  phase, including one fixture reconstructing `JobApplication` 9's real accepted `READY` shape
  without touching the operational database. 45 new deterministic tests (`job_applications/tests/
  test_dashboard_services.py`, `job_applications/tests/test_views_dashboard.py`, `resume_builder/
  tests/test_delivery.py`); zero live provider calls, verified directly by asserting `LLMCallLog`'s
  row count is unchanged across dashboard/detail/preview/download requests.
- **M8**: token-consumption report correctness against known `LLMCallLog` fixture rows (per
  job application, per stage, per provider, per model); any remaining gaps found across M1–M7.
- **M6 follow-up (2026-09-06, D-035/D-036)**: the hybrid baseline-chronology correction is tested
  the same way M5/M6 already are — deterministic, `FakeAdapter`-only, zero network. Synthetic
  Ford/Continental/Maruti/German-language/global-evidence/no-evidence-engagement fixtures
  (`resume_builder/tests/test_hybrid_chronology.py`) prove the architecture-level invariants (every
  approved engagement reaches the baseline regardless of AC_RANK selection, anchor selection is
  bounded and deterministic with a documented tie-break, language evidence is unconditional, global
  claims are never misattributed, no-evidence engagements are flagged rather than fabricated
  around, and one end-to-end `build_resume_draft` run proves the corrected final markdown actually
  contains the previously-omitted content) without needing any LLM-quality judgment call — this
  correction is a deterministic data-flow/rendering fix, not a prompt-quality change, so it needed
  no exception to the "no cassette testing until outputs stabilize" rule above.
- **M6 corrective follow-up (2026-09-07, D-037)**: an independent audit found the 2026-09-06 fix
  above still re-derived evidence from live mutable state at M6 time instead of a pinned
  `FitAssessment` identity. Testing this correction needed one new pattern beyond "deterministic,
  `FakeAdapter`-only": proving *drift-immunity* -- that changing live state (activating a second
  `CandidateMemory` revision, rejecting a `CareerEngagement`, revoking a `ClaimEngagementMapping`)
  *after* a `FitAssessment`'s manifest was already persisted has **zero** effect on that
  `FitAssessment`'s own reconstructed M6 context (`candidate_matching/tests/
  test_fit_assessment.py::PinnedEvidenceIdentityTests`, `resume_builder/tests/
  test_context.py::test_engagement_rejected_after_manifest_creation_still_appears_pinned`,
  `::test_mapping_status_change_after_manifest_creation_does_not_alter_reconstruction`) --
  asserting equality of the manifest/context *before and after* the mutation, not just asserting a
  single post-mutation snapshot. Manifest validation itself (`candidate_matching/tests/
  test_baseline_chronology_manifest.py`, 15 tests) is tested as a pure unit -- malformed/
  inconsistent/cross-revision dicts constructed by hand, no database fixture beyond what's needed
  to build one valid manifest to mutate. Completeness enforcement
  (`resume_builder/tests/test_completeness.py`) and the full-request budget check
  (`resume_builder/tests/test_request_budget.py`) both drive the real `build_resume_draft`/
  `generate_resume_content` orchestration end to end, `FakeAdapter` only, and the budget test
  additionally asserts the mocked adapter's `generate()` is never even called once the check fails
  -- proving the fail-closed check truly runs before any would-be HTTP call, not just that an
  exception is eventually raised somewhere in the call stack. 46 new tests total; still no
  LLM-quality judgment call anywhere (this remains a deterministic data-flow/schema-validation
  correction), so it needed no exception to the "no cassette testing until outputs stabilize" rule
  above.



**OpenRouter Free Router migration (2026-09-07, D-038)**: 28 new deterministic tests --
`llm_provider/tests/test_openrouter_free_router_config.py` (the idempotent
`configure_openrouter_free_router` service/command: fresh-database creation, the realistic
pre-migration Z.ai-assignment scenario, repeated-invocation idempotency, historical `LLMCallLog`
preservation across deactivation, `ProtectedError` on an attempted delete of a still-referenced
model, the `InactiveModelAssignedError` routing guard and its reactivation rollback path, and
`--dry-run` leaving the database byte-for-byte unchanged) and
`llm_provider/tests/test_openrouter_free_router_request.py` (the exact `openrouter/free` model id
reaching the serialized `requests.post` body, never double-prefixed/reconstructed/split -- including
a synthetic multi-slash id as a general regression guard -- `tools`/`tool_choice` absence,
`response_format`/`provider.require_parameters` presence, reasoning-field absence for the
non-reasoning free-router model, and the new requested-vs-resolved-model/finish_reason/
correlation_id audit fields, including the never-guessed-on-error case). All mock at the
`requests.post` boundary and assert the actual serialized body, per this file's standing
convention. Deliberately does **not** re-test the general OpenRouter transport/structured-output/
privacy/retry/error-classification matrix already covered model-id-agnostically by
`test_openrouter_adapter.py` -- that coverage applies unchanged to any model id, including this
one, and a read-only audit confirmed the adapter never parses, splits, or reconstructs
`LLMModel.model_id` anywhere, so no behavior there needed to change for this migration. Manual opt-in
live qualification (`manage.py smoke_test_openrouter --model openrouter/free`) was **not attempted**
in the session that implemented this -- `OPENROUTER_API_KEY` was not configured in that environment.

**Per-run model selection (2026-09-07, same day, D-038 broadened scope)**: 42 further deterministic
tests. `llm_provider/tests/test_model_eligibility.py` (11) -- inactive provider/model excluded,
incompatible (non-structured-output) model excluded, missing credential reference excluded,
eligible OpenRouter Free/NVIDIA included, the retired Z.ai model excluded via `is_active` (plus a
dedicated case proving eligibility itself is purely `is_active`-driven per row, with the actual
"deactivate every row" safety property proven where it's enforced -- the config-command tests
below), FAKE always excluded, a newly-added registry model appearing with zero calling-code change,
and display-label formatting. `llm_provider/tests/test_model_selection.py` (11) --
`resolve_stage_model`'s three-step precedence (override → default → typed
`NoStageDefaultConfiguredError`), an override never mutating the global `StageModelAssignment`, an
ineligible/nonexistent override rejected, and `get_adapter_for_stage`'s `requested_model_id`
wiring (override uses its own model's capability rather than the default stage's tuned budget,
`selection_source` recorded correctly, an unavailable selection failing before any `requests.post`
call, and independent selection across two different stages in one call). Expanded
`test_openrouter_free_router_config.py`: every stage (not only `AC_NORMALIZE`) defaults to
`openrouter/free` on both a fresh and a realistic pre-populated database, idempotency re-proven for
the full 6-stage matrix, `AB_BUILD`'s prior OpenAI assignment removed while the `gpt-5` row itself
stays active/untouched, NVIDIA-assigned stages move to the free router while the NVIDIA model row
stays active/untouched, no paid/fallback model is ever introduced as a side effect, and (the
real-database finding this update made) a second, orphaned OpenRouter-type provider row's own copy
of the retired Z.ai model id is also deactivated, not only the canonical row's. One updated case in
`test_routing.py`: an unassigned stage now raises the typed `NoStageDefaultConfiguredError` instead
of a bare Django `DoesNotExist`. 14 new UI/execution tests --
`job_intake/tests/test_model_selection_ui.py` and `reviews/tests/test_model_selection_ui.py` --
render the selectors from the real registry (default marked, NVIDIA alternative present, inactive
model absent, accessible `<label for>`/`id` pairing), and drive a full request through mocked
`requests.post` to prove an override actually reaches the provider call and is recorded on
`LLMCallLog` with `selection_source=OVERRIDE` while the global default stays unchanged, an
invalid/ineligible selection is rejected before any provider call (via Django `ChoiceField`
validation for `job_intake`'s form, via the shared `resolve_stage_model` typed error for `reviews`'
raw-HTML forms), and a valid selection survives an unrelated downstream validation error rather
than silently resetting to the default. Full suite after this update: 1229/1229 passing (70 new
tests total for D-038, 28 + 42); `manage.py check`/`makemigrations --check --dry-run` clean;
`ruff check .` clean. Live qualification remains deliberately deferred to the later, separately
authorized M5/M6 run.

**Paid GPT-5.4 model defaults, reasoning-effort selection, and the M5/M6 stage console (2026-09-07,
D-039)**: 71 net new deterministic tests (1229 -> 1300), all against mocked `requests.post`
asserting the real serialized JSON body, per this project's standing convention.
`llm_provider/tests/test_gpt54_wire_payload.py` (14; renamed in scope by the same-day correction
below to cover only the OpenRouter-hosted *alternative* records, `openai/gpt-5.4-mini`/
`openai/gpt-5.4` -- see `test_gpt54_openai_direct_wire_payload.py` for the direct-OpenAI defaults'
own wire coverage) -- the exact OpenRouter model id on the wire unchanged (never split/
double-prefixed), every configured reasoning level (`medium`/`high` from the default matrix, plus
the structurally-supported `low`/`xhigh`) serialized as `{"reasoning": {"effort": <value>}}`, the
explicit-`NONE`-vs-omitted-key distinction, that `reasoning.effort` and `reasoning.enabled` are
never sent together, and that no `tools`/NVIDIA/Gemini-specific parameter ever leaks onto an
OpenRouter body. `llm_provider/tests/test_gpt54_defaults_config.py` (25) -- exact model ids, the
complete 6-stage default matrix, idempotency (including a real cross-command interaction bug this
work found: running `configure_openrouter_free_router` after `configure_gpt54_defaults` raised a
`ValidationError` over a stale `default_reasoning_effort`, fixed in
`openrouter_free_router.py` and proven not to regress under either invocation order), no stale
Z.ai/legacy-GPT-5 default, no automatic fallback, historical `LLMCallLog` preservation, and the
management command's `--dry-run`/real-run output. `llm_provider/tests/test_reasoning_selection.py`
(15) -- the same three-step precedence `test_model_selection.py` proves for the model, now for
reasoning effort: stage default used absent an override, explicit override (including explicit
`NONE`) takes precedence and never mutates the stored default, an explicit reasoning request
incompatible with the resolved model is rejected (both when the *model* was explicitly overridden
and when only the stage's own configured default reasoning was left in place against an overridden
model), `adapter.effective_reasoning_effort` exposure, and persistence to
`LLMCallLog.reasoning_effort`. `llm_provider/tests/test_console.py` (11) -- the M5/M6 stage-console
service (`llm_provider.services.console.build_stage_card`): truthfully-known paid/free detection
(`openrouter/free`/`:free`-suffixed ids are free, both GPT-5.4 models are never described as free,
an unknown model reports `None` rather than guessing), `correlation_id`-scoped attempt history
(one application's card never shows another application's attempt, and shows none at all when no
`correlation_id` is given rather than falling back to "the global latest call for this stage"),
attempt numbering, and sanitized error category + actionable guidance on a failed attempt. Plus
required-review-note-on-rejection coverage added to `reviews/tests/test_gate1_services.py`,
`test_gate2_services.py`, `test_views.py`, `test_gate2_views.py` (blank/whitespace-only feedback
`comments` rejected before any re-run, at both the service and view layer). Every pre-existing
test-only stub that replaces `get_adapter_for_stage`/`expand_requirements_for_search`/
`rank_relevance` across `candidate_matching`/`job_intake`/`resume_builder`'s test factories was
updated to accept the new `requested_reasoning_effort`/`correlation_id` keyword arguments the real
call sites now pass, with zero change to what any of them scripts. Full suite after this decision:
1300/1300 passing; `manage.py check`/`makemigrations --check --dry-run` clean; `ruff check .`
clean. Applied to the real local development database, idempotency proven directly against it.
Live qualification remains deliberately deferred to the later, separately authorized M5/M6 run.

**Correction, same day (D-039 update): direct OpenAI, not OpenRouter, is the default**. New
`llm_provider/tests/test_gpt54_openai_direct_wire_payload.py` (20) -- exact unprefixed direct model
ids (`gpt-5.4-mini`/`gpt-5.4`) on the wire, the direct endpoint (`https://api.openai.com/v1`) and
`OPENAI_API_KEY`-referenced Bearer credential, `medium`/`high` reasoning effort as OpenAI's own
flat top-level `reasoning_effort` field (never OpenRouter's nested `reasoning.effort` object), the
pre-existing `max_completion_tokens`/no-`temperature` reasoning-model shape, and the absence of
every OpenRouter-only wire artifact (`provider` routing object, attribution headers) and
NVIDIA/Gemini-specific parameter. `llm_provider/tests/test_gpt54_defaults_config.py` was
substantially rewritten (25 -> 32) to prove the corrected direct-OpenAI default matrix, that this
registry's own pre-existing direct-OpenAI provider row (id 11, already serving `gpt-5`) is reused
rather than duplicated, that an operator's own custom `base_url`/`credential_env_var` on that row
is never overwritten, and that the OpenRouter-hosted records are preserved as active, non-default
alternatives (both on a fresh database and simulating the real pre-correction state). New
`llm_provider/tests/test_provider_grouped_selection.py` (9) proves the `<optgroup>` provider
grouping (`llm_provider.services.eligibility.provider_group_label`/`grouped_model_choices`) and
that a provider/model mismatch cannot be constructed (there is no separate provider input; the
submitted value is always one unambiguous `LLMModel` primary key). `llm_provider/tests/
test_console.py` gained `ProviderEndpointCredentialDisplayTests` proving "System default" resolves
to `"OpenAI — Direct API"` for the real GPT-5.4 stages, `"Routed (OpenRouter)"` for the OpenRouter
alternative, and that credential presence is reported without ever exposing the value. Full suite
after this correction: 1339/1339 passing (39 net new/rewritten on top of the original pass's 1300);
`manage.py check`/`makemigrations --check --dry-run`/`ruff check .` all clean. No live call was
made for this correction either.

**Product Owner output-token budget correction (2026-09-07, D-040)**: 24 new deterministic tests
(`llm_provider/tests/test_gpt54_budget_correction.py`) -- both `gpt-5.4-mini`/`gpt-5.4` accept and
persist the corrected 16384-token capability via `full_clean()`; `AC_NORMALIZE`/`AC_RANK`/
`AC_MATCH`/`AB_BUILD` each resolve to an effective request budget of exactly 16384 through the real
`get_adapter_for_stage` resolution path (never merely a registry field read out of context);
`MEMORY_BUILD`/`AJ_ANALYZE` confirmed unchanged at 4096/8192; a stage budget above the new 16384
ceiling is still rejected by both `full_clean()` and, defense-in-depth, `get_adapter_for_stage`'s
own `InvalidStageBudgetError`; `selection_source` stays `DEFAULT` with no override submitted; every
provider/model mapping and reasoning level is unchanged; no fallback/substitution mechanism exists;
the configuration operation is idempotent, including when re-run against a simulated stale
pre-correction database still carrying the old 8192 budgets (proving the correction genuinely
*writes* the new value rather than treating an already-matching model/reasoning pair as "nothing to
do"); and the four affected stages' real request bodies are constructed locally via the adapter's
own pure `build_chat_completion_body` function -- never through `adapter.generate()`, and `requests`
is never imported or patched anywhere in the new file -- confirming `max_completion_tokens=16384`
reaches the OpenAI reasoning-model request contract for all four. 24 existing tests in
`test_gpt54_defaults_config.py` were updated for the new three-element `STAGE_DEFAULT_MATRIX` shape
(model, reasoning, per-stage budget) and the 16384 capability value, with zero behavior change to
what they otherwise assert. Full suite after this correction: 1363/1363 passing (24 net new on top
of 1339); `manage.py check`/`makemigrations --check --dry-run` (confirmed no schema migration is
generated -- a pure data/configuration correction)/`ruff check .` all clean. No provider call was
made for this correction.

**GPT-5.4 32K capacity correction and operator-controlled M5/M6 staged review workflow (2026-09-08,
D-041, branch `m5-staged-workflow`)**: the capacity correction (split `gpt-5.4-mini`/`gpt-5.4`
ceilings, `AC_RANK`/`AC_MATCH` reasoning lowered to medium) reuses the exact same test shape as
D-040 (`llm_provider/tests/test_gpt54_budget_correction.py`, rewritten for the new per-model
ceiling) -- deterministic only, `requests` never imported or patched. The staged-workflow
architecture adds 67 new deterministic tests across two layers, all `FakeAdapter`-only, zero live
provider calls:
- **Service layer** (`candidate_matching/tests/test_staged_run.py`, 39 tests;
  `resume_builder/tests/test_staged_build.py`, 8 tests): the full happy path (start -> execute ->
  approve for each of AC_NORMALIZE/AC_RANK/AC_MATCH, then `finalize_run`) proving exactly one
  `LLMCallLog` row per executed stage and zero on every other action; input-edit run-locality and
  canonical-record non-mutation; fabricated/cross-pool claim-id rejection on both input and output
  edits; provider-output immutability (including a real bug found and fixed during this work --
  `execute_stage` originally stored `operator_output` as the *same object* as `provider_output`,
  so an in-place mutation of one silently corrupted the other in memory before either was next
  saved/reloaded; fixed with an explicit `copy.deepcopy`, and a dedicated test now asserts the two
  diverge correctly after an edit); downstream invalidation on a post-approval edit, with revision
  history proven to survive it; the D-035/D-037 baseline-chronology independence-from-AC_RANK-
  selection property (a Continental-organisation-style engagement's anchor claim, and a German-
  language-style claim, both confirmed present in the persisted manifest even when AC_RANK's own
  ranking response selects nothing); atomic, single-`FitAssessment`/`ResumeDraft` finalization,
  including a forced mid-transaction failure proving no partial row survives; and optimistic-
  concurrency (`lock_version`) rejection of a stale/duplicate action.
- **UI/security layer** (`reviews/tests/test_m5_staged_views.py`, 13 tests;
  `reviews/tests/test_m6_review_views.py`, 7 tests): CSRF actually enforced (via
  `Client(enforce_csrf_checks=True)`, not merely a template tag's presence); GET/save-input/save-
  output/approve actions proven to never invoke the adapter, only the "run" action does; a
  duplicate submission (same, now-stale `lock_version`) rejected with a re-rendered error page, not
  a second call; a page refresh after a successful run proven not to repeat it; failure diagnostics
  proven not to leak a raw header/credential string; the historical `FitAssessment`/its rendered
  Gate 1 heading proven absent from a fresh, in-progress M5 run's own page; and full finalize-and-
  redirect-to-Gate-1 / approve-and-redirect-to-Gate-2 integration tests. Two pre-existing gate1/
  gate2 view tests were updated for the new empty-state copy (the single-shot "Run/Re-run" buttons
  are removed from both templates; the assertion now checks for their *absence* alongside the new
  "Start M5 run"/"Start M6 review" entry point). Full suite after this work: all tests passing (no
  regression in any of the 1,431 pre-existing tests); `manage.py check`/`makemigrations --check
  --dry-run`/`ruff check .` all clean. No live M5/M6 run was performed, and no
  `JobApplication`/`FitAssessment`/`ResumeDraft`/`CandidateMemory`/Gate row was touched, at any
  point while writing or running this test suite.

**What Phase 1 does NOT attempt to test automatically**: the *quality* of any LLM-generated
content (e.g. "is this a good resume," "did AJ correctly identify implied seniority signals").
That is a manual review activity at each milestone's acceptance walkthrough, not a unit test —
requirements.md is explicit that this is an exploratory build where prompts are still being
iterated on. This includes whether generated wording *fairly represents* the evidence it cites:
the D-014 structural validators (M5's disposition-coverage check, M6's no-fabrication check) only
verify evidence attachment and eligibility — that a cited `MemoryClaim` ID exists, is confirmed,
and is in scope — never that the surrounding prose is an honest rendering of it. That residual
judgment is exactly what Human Review Gates 1 and 2 are for, and no unit test replaces them.

## Phase 2 (future, once outputs stabilize): recorded-response ("cassette") testing

**Explicitly deferred (TEST-002, requirements.md §12, NG-003)** — not built in v1. When it is
eventually built: record real provider responses once, replay them in CI thereafter (hash the
request, store/replay a JSON fixture, forbid live "record" mode in CI), modeled on the pattern
`career-intelligence`'s `evals/cassette_store.py` established — cited here as prior art/rationale
only, not as code to inspect or copy for this app per the standing instruction to keep this
codebase independent.

**Why this is safe to defer without cost later**: the adapter interface designed in M2
(`NormalizedLLMRequest`/`NormalizedLLMResult`, one call path per provider) is deliberately the one
seam every LLM call passes through (LLM-002). Recording/replaying at that single seam later is a
small, localized addition — it does not require touching any pipeline call site in
`candidate_memory`, `job_intake`, `candidate_matching`, or `resume_builder`. Preserving this seam is
itself a testable architectural property: a code-review check at any later milestone that no
pipeline app imports a provider SDK directly is what keeps this promise true.

## What stays manual regardless of phase

Two review-gate walkthroughs (HITL-001/HITL-003) and the overall resume-quality judgment (GOAL-001)
are operator activities, not test-suite responsibilities, for the entirety of v1 — they are
supposed to involve a human, by design (requirements.md §1/§10), not to be automated away.

## Test tooling

No tooling decision beyond what STACK-001/STACK-005 already implies (Django's own test runner or
`pytest-django`, and Pydantic model validation used directly in assertions) is needed for Phase 1.
No cassette/VCR-style library, no LLM-judge/eval framework, and no browser-automation testing tool
is introduced in this phase.
