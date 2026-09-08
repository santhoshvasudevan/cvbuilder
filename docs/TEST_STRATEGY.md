# CVBuilder V2 Test Strategy

**Status:** Proposed  
**Date:** 2026-09-08

V2 testing has three distinct layers:

1. deterministic correctness tests,
2. live/manual provider qualification,
3. human content-quality evaluation.

Do not confuse these layers.

## 1. Deterministic Tests

### Foundation
- Django loads.
- PostgreSQL is the configured application database.
- `.env` is not committed.
- no Redis/Celery/SPA dependency appears without approval.

**M1 status: VERIFIED.** `job_applications/tests/` (42 tests) covers Django startup, settings/environment validation, PostgreSQL configuration (including a live query against a real connection), the home view and URL routing, admin registration/reachability, and M1's architectural invariants (no legacy V1 stage identifiers or app names, `.env` not tracked by git, `.env.example` placeholder-only). Run via `make test`; `make check`, `make migrations-check`, and `make lint` are also part of `make verify`. Canonical local commands live in the root `Makefile` — see `AGENTS.md`.

### LLM provider
- adapter routing;
- provider/model registry;
- stage defaults;
- runtime overrides;
- structured-output translation;
- retry classification;
- error sanitization;
- token logging;
- identical input snapshot used for model A/B runs;
- `reasoning_level` rejected (at `StageModelAssignment` save time and on a per-call override) when it is not a member of the selected model's `supported_reasoning_levels` (V2-D034).

Automated tests never require live credentials.

### Candidate context
Fixtures verify that context includes:
- direct match;
- differentiators;
- career narrative;
- foundations;
- gaps/constraints.

A regression test should prevent over-aggressive retrieval from dropping all earlier-career context.

These tests gate M3B's acceptance criteria (V2-D025) — AC (M5) must not depend on `CandidateContextSnapshot` until they pass on real candidate data.

### Static resume profile
- `ExperienceSlot` is a related, sequenced collection, not fixed columns — a HARD_INTEGRITY check enforces exactly three `ExperienceSlot` rows with `is_primary=True, is_active=True` for the configured candidate (V2-D024);
- generated draft references slots by ID;
- model output cannot overwrite static company/date/title/location metadata (HARD_INTEGRITY, V2-D026);
- renderer uses static metadata;
- `ExperienceSlot` creation/activation only occurs through the operator-controlled selection UI (select engagement → create/activate → order → validate), never automatically (V2-D031).

### AJ
Schema tests for stable requirement IDs, the three orthogonal requirement dimensions (`requirement_priority`/`requirement_domain`/`origin` — V2-D033), role identity, ranked hiring signals, screen-out risks, recruiter questions, and RecruiterDecisionModel fields.

### AC
Schema tests for disposition values, evidence-strength values, experience-level values, gaps retained, and differentiators supported by supplied context.

### APS
Schema tests for required positioning fields.

### AB
- exactly three title options;
- exactly three experience-section references;
- no generated static metadata;
- duplicate detection;
- suspicious metric warning;
- section completeness;
- critic schema;
- refinement preserves unchanged content where instructed.

Of the above, "no generated static metadata" and correct experience-slot cardinality are `HARD_INTEGRITY` checks (fail closed); duplicate detection and suspicious-metric warnings are `SOFT_REVIEW_WARNING` checks (surfaced, never block) — see V2-D026.

### UI/workflow
- StageRun status persists after restart;
- operator edit does not delete original model output;
- approval/rerun works;
- current pipeline stage derives from DB state;
- application outcome is independent from generation phase;
- `ReviewFeedback` correctly references its `StageRun` (or is nullable for aggregate gate-level feedback), with no separate `Target` enum (V2-D032).

## 2. Live Provider Qualification

Manual opt-in only.

For each configured provider/model:
- execute minimal structured output;
- verify response parsing;
- verify usage metadata;
- record safe call metadata;
- do not persist secrets/raw sensitive provider errors.

Provider status:
- `NOT_TESTED`
- `VERIFIED`
- `FAILED`
- `NOT_CONFIGURED`

## 3. Content-Quality Evaluation

This is essential in V2.

Use representative real jobs and score outputs on:
- Job Alignment
- Candidate Differentiation
- Recruiter Impact
- Specificity
- Career Coherence
- Seniority Positioning
- Technical Credibility
- Domain Positioning
- ATS Coverage
- Conciseness
- Completeness
- Correction Burden

Use a 1–10 scale.

Compare:
- expert-assisted reference;
- one-shot baseline;
- V2 multi-pass output;
- alternative models.

See `docs/QUALITY_BENCHMARK.md` for the full benchmark methodology, including the initial Amazon GenAI Solutions Architect representative benchmark (V2-D028).

## 4. Stage-Specific Quality Checks

### AJ
- Did AJ understand what the job really is?
- Are hiring signals ranked correctly?
- Are screen-out risks realistic?
- Are recruiter questions useful?

### AC
- Did AC use enough candidate breadth?
- Did it miss transferable strengths?
- Did it retain career progression?
- Are experience levels realistic?
- Are gaps preserved?

### APS
- Is there a clear candidate thesis?
- Is lead/support/de-emphasize strategy convincing?
- Does narrative explain why this candidate fits?
- Does it answer recruiter questions?

These APS quality checks must pass on at least one representative real job application before AB (M6) implementation begins (V2-D027).

### AB
- Is the opening strong?
- Is the draft generic?
- Does it use strongest evidence prominently?
- Is candidate differentiated?
- Is career progression coherent?
- Does critique identify real weaknesses?
- Does refinement materially improve the draft?

## 5. Token Evaluation

For every real benchmark capture:
- input tokens;
- cached input tokens;
- output tokens;
- total tokens;
- latency;
- retries;
- stage;
- provider;
- model;
- reasoning level;
- configured output budget.

Calculate quality-per-token comparisons in M8.

## 6. Acceptance Target

Representative final resume content should reach approximately **90% or better of expert-assisted reference quality**, measured per the methodology in `docs/QUALITY_BENCHMARK.md`.

A green deterministic test suite alone does not satisfy this criterion.
