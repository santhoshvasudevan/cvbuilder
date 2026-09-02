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
- **M3 (`candidate_memory`)**: `confirmation_status` state transitions (`unconfirmed` →
  `confirmed`/`retired`, and that retrieval excludes non-confirmed claims); versioning invariants
  (a new `CandidateMemory` revision leaves prior versions' claims untouched); the claim-
  traceability validator (a claim whose text isn't supported by its stored source quote is
  flagged).
- **M4 (`job_intake`, `job_applications`)**: fetch-success vs. fetch-failure branching (a fixture
  returning an unparseable/too-short body triggers the fallback-to-paste UI path, not a silent
  low-quality result); `source_type` + raw input always persisted regardless of path taken; every
  material requirement is assigned a stable, unique `JR-xxx` ID and category within one
  `JobRequirementAnalysis` version; a new `JobApplication` is created/attached on intake with
  `pipeline_phase` starting at `NEW`.
- **M5 (`candidate_matching`, `reviews`)**: retrieval precision (irrelevant/unconfirmed claims
  excluded from a fixture memory); the disposition-coverage validator (every relevant
  `JobRequirement` in a fixture gets exactly one `RequirementAssessment`; a fixture with a known
  `GAP` always surfaces it, never silently upgraded to `MATCH`; a `MATCH`/`PARTIAL` lacking
  confirmed-claim evidence is rejected); review-gate state transitions (approve → proceed-ready,
  advancing `pipeline_phase` to `PREPARATION`; feedback → `needs_rework` plus a new version per
  D-010, verified to survive a process restart since it's plain DB state, not in-memory/paused
  execution — this is the direct test of HITL-002/HITL-004).
- **M6 (`resume_builder`)**: the no-fabrication validator running against the **structured**
  representation before any markdown is rendered (a fixture `ResumeElement` with no evidence, or
  with an ID that doesn't resolve to an existing/confirmed/eligible `MemoryClaim`, is rejected
  before rendering); the markdown renderer produces exactly the structure in
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
