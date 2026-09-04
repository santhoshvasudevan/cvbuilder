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
  NIM, Gemini), a minimal structured-output request through the actual adapter, run only when the
  operator explicitly initiates it, using credentials from `.env`. This never runs automatically in
  CI or as part of `manage.py test`, never persists raw sensitive provider request/response
  bodies, and records safe `LLMCallLog` metadata where appropriate. A provider with no configured
  credential is reported as **not live-verified**, never as a failure of the deterministic suite —
  passing mocked tests must never be reported as "this provider is operationally verified."
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
  `pipeline_phase` starting at `NEW`. **(D-022, 2026-09-04, committed)** a real controlled Gate-1
  preparation run surfaced a schema-valid `AgentJobberAnalysis` with zero `JobRequirement`s and
  posting responsibilities recast as candidate-gap screening risks (JobApplication id=9) --
  `job_intake/validators/sanity.py::find_sanity_violations` is a deterministic, lexical-only
  (never fuzzy) semantic gate between schema validation and persistence, tested directly
  (`test_sanity_validator.py`) against: the exact observed failure shape (zero requirements +
  gap-phrased screening risks, reproduced verbatim); valid MANDATORY/PREFERRED/RESPONSIBILITY
  extraction with real provenance (no violations); a genuine, quoted screening constraint (no
  violations); `IMPLIED_EXPECTATION` correctly exempt from the provenance requirement; a duplicate
  requirement (same category + normalized text); a requirement/risk whose `source_context` is not
  an exact substring of the posting actually analyzed; and zero requirements on a short,
  genuinely-non-substantive posting correctly *not* flagged. `test_semantic_validation_intake.py`
  covers the same ground at the `run_intake` integration level -- atomicity (nothing persisted, no
  pipeline-phase advancement), the underlying `LLMCallLog` row still written without raw posting
  content, and a valid analysis still persisting normally. `candidate_matching/tests/
  test_fit_assessment.py` adds the M5-side guard: `build_fit_assessment` refuses a current JRA with
  zero `JobRequirement`s, exercised against a fixture built the same way the real legacy JRA id=9
  exists (JobRequirement rows never created), proving the check is a fresh runtime property, not
  something that has to be baked in at creation time.
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
- **M7 (integration, `job_applications` dashboard)**: cross-app status-transition wiring for a
  full job-application run; chain-wide freshness enforcement at every step boundary (not LLM
  output quality — that stays a manual review item, per below); `pipeline_phase`
  NEW→ANALYSIS→PREPARATION→READY transitions occur automatically at the right points and never in
  an impossible combination; `application_outcome` is independent of `pipeline_phase` (marking
  `APPLIED` then regenerating a resume does not revert `application_outcome`); the dashboard list
  view surfaces an accurate derived status for a fixture set of job applications in different
  states.
- **M8**: token-consumption report correctness against known `LLMCallLog` fixture rows (per
  job application, per stage, per provider, per model); any remaining gaps found across M1–M7.

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
