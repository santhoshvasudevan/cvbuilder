# CVBuilder V2 Implementation Plan

**Status:** Proposed  
**Date:** 2026-09-08

Each milestone must be independently reviewable and committable. Do not begin a later milestone while an earlier dependency is incomplete.

## Dependency Graph

```text
M0 — V2 architecture baseline
 |
M0.2 — Architecture closure (reuse audit + design decisions + multi-agent handover protocol) — COMPLETE
 |
M1 — Foundation + reuse audit
 |
M2 — LLM control plane + operator call console
 |
M3A — Candidate knowledge + StaticResumeProfile
 |
M3B — CandidateContextSnapshot (five-bucket; own quality acceptance gate)
 |
M4 — AJ recruiter analysis
 |
M5 — AC assessment + APS + Gate 1 (APS quality acceptance required before M6)
 |
M6 — AB plan/draft/critique/refine + Gate 2
 |
M7 — Integrated workflow/dashboard
 |
M8 — Model qualification + token optimization
```

## M0 — V2 Architecture Baseline

### Objective
Freeze the V2 design before implementation.

### Scope
- `requirements.md`
- `docs/ARCHITECTURE.md`
- `docs/RESUME_OUTPUT_STRUCTURE.md`
- `docs/DECISIONS.md`
- `docs/TEST_STRATEGY.md`
- `docs/REQUIREMENT_TRACEABILITY.md`
- `docs/CURRENT_STATE.md`
- `CLAUDE.md`

### Acceptance
- documents agree;
- V2 positioning-first philosophy is explicit;
- APS exists;
- V1's AC chain (`AC_NORMALIZE`/`AC_RANK`/`AC_MATCH`) is not assumed as the AC_ASSESS design;
- three static experience slots are defined architecturally;
- AB multi-pass flow is explicit;
- no implementation changes.

## M1 — Foundation and Reuse Audit

### Objective
Establish the Django/PostgreSQL foundation on `cvbuild2` while selectively reusing stable infrastructure from `main`.

### First action: read-only reuse audit — COMPLETE
The read-only reuse audit (M0.2) is complete. See `docs/V2_REUSE_AUDIT.md` for the full `REUSE_AS_IS`/`REUSE_WITH_ADAPTATION`/`DO_NOT_REUSE` classification, exact files/commits, and recommended reuse order.

Do not bulk merge.

### Candidate reuse (confirmed by audit)
- Django settings/foundation
- Docker/Postgres
- `.env.example`
- Makefile/local quality tooling
- generic UI styles/templates
- provider adapter infrastructure (`llm_provider` — near-complete, see audit; requires the `StageModelAssignment.Stage` enum rewrite per V2-D022)

### Scope
- Django skeleton
- PostgreSQL local Docker
- app boundaries, including `candidate_context` as a distinct app from `candidate_memory` (`docs/ARCHITECTURE.md` §3, V2-D030) and `reviews` owning `ReviewFeedback` (§5, V2-D032)
- `JobApplication` (small stable aggregate), `StageRun`, `JobApplicationStageState`, and the canonical stage vocabulary (`docs/ARCHITECTURE.md` §5 — closed in M0.2, V2-D022)
- admin/auth infrastructure
- static template foundation
- environment handling
- root `Makefile` and `.env.example`, establishing the canonical local commands (start DB, migrate, test, lint/check, run server) that `AGENTS.md`/`docs/ENGINEERING_RULES.md` point to but do not themselves define — neither exists yet on `cvbuild2` (V2-D035)
- local test/lint/check commands

### Out of scope
- real AJ/AC/APS/AB behavior
- live provider calls

### Acceptance
- application runs;
- Postgres works;
- admin works;
- tests/check/lint pass;
- reuse decisions documented (`docs/V2_REUSE_AUDIT.md`);
- `JobApplication`/`StageRun`/`JobApplicationStageState` schema matches the closed M0.2 design, not a growing `current_*` FK list;
- no accidental import of old V1 pipeline semantics (in particular, no `AC_NORMALIZE`/`AC_RANK`/`AC_MATCH` stage identifiers).

## M2 — LLM Control Plane and Operator Call Console

### Objective
Build or selectively reuse the provider/model execution foundation.

### Scope
- OpenAI
- NVIDIA NIM
- Gemini
- OpenRouter
- provider registry
- model registry, with `supported_reasoning_levels` as the canonical per-model reasoning capability (V2-D034 — `StageRun` itself is not built here, see M1/V2-D022)
- stage defaults
- reasoning/token-budget defaults
- runtime overrides
- structured-output adapter abstraction
- `LLMCallLog`, referencing the initiating `StageRun` (V2-D022)
- safe errors/retry
- UI to inspect input/config/output
- run/approve/edit/rerun
- model comparison on identical stored input
- manual opt-in provider smoke testing

### Acceptance
- fake-adapter deterministic tests pass;
- each provider adapter's schema translation is tested without live credentials;
- stage default routing works;
- runtime override works;
- input/output token metadata is captured;
- operator can run a stage manually;
- `.env` credentials are not stored in DB;
- OpenRouter behaves as a normal provider;
- configured providers can be manually smoke-tested;
- `reasoning_level` is rejected, both at `StageModelAssignment` save time and on a per-call override, if it is not a member of the selected model's `supported_reasoning_levels` (V2-D034).

## M3A — Candidate Knowledge and StaticResumeProfile

**Closed in M0.2 (V2-D025):** M3 splits into M3A and M3B because the reuse audit found the static-profile half largely pre-built on `main` (`CareerEngagement`, `static_profile_boundary.py`), while M3B's five-bucket context snapshot has zero precedent — treating them as one milestone understated M3B's risk/effort.

### Objective
Establish candidate factual knowledge and the deterministic static resume profile that AB will render from.

### Scope
- Candidate Profile
- optional/reused MemoryClaims
- source ingestion
- operator corrections/preferences
- career direction
- previous positioning history
- `StaticResumeProfile`
- `ExperienceSlot` as a sequenced related collection, not fixed columns (`docs/ARCHITECTURE.md` §6, V2-D024)
- operator-controlled `ExperienceSlot` selection UI (V2-D031): select engagement → create/activate slot → order (1/2/3) → edit static metadata if authorized → validate exactly three — never automatic selection

### Acceptance
- candidate source documents ingest into structured claims;
- exactly three `ExperienceSlot` rows with `is_primary=True, is_active=True` exist and are correctly sequenced for the current candidate, created via the operator-controlled selection UI, not an automated choice;
- static company/date/title/location metadata is deterministic and never model-generated;
- operator corrections/preferences are captured and available to downstream stages.

## M3B — CandidateContextSnapshot

### Objective
Build the five-bucket `CandidateContextSnapshot` in `candidate_context`, a Django app distinct from `candidate_memory` (V2-D030). Confirmed by the reuse audit to have zero precedent on `main` — this is the highest-effort, highest-judgment part of M3 (V2-D025) and should be estimated/staffed accordingly, not as a peer-effort item to M3A.

### Scope
- `CandidateContextSnapshot`, owned by `candidate_context` and consuming `candidate_memory` + current job/AJ artifacts without becoming a source of candidate truth itself (V2-D030)
- five retrieval buckets: direct match, differentiators, career narrative, foundations, gaps/constraints
- minimum-sufficient-context selection strategy (CTX-001) — not minimum-matching-claims
- editable context UI
- context token reporting
- immutable per-run snapshot (CTX-006)

### Acceptance — required before AC (M5) may depend on M3B
For a real candidate profile:
- direct match evidence exists;
- differentiators are retained and are not required to map one-to-one to a JD requirement;
- career chronology is retained, not only the latest job;
- foundations are retained;
- constraints/gaps are retained;
- changing target job changes context selection without losing core narrative;
- context can be inspected/edited before AC;
- a regression test demonstrates that over-aggressive retrieval does not drop all earlier-career context.

## M4 — AJ Recruiter Analysis

### Objective
Produce job understanding at recruiter/hiring-manager quality.

### Scope
- URL/paste intake
- fetch/fallback
- stable JR IDs
- Job Requirement Model, with `requirement_priority`/`requirement_domain`/`origin` as separate orthogonal dimensions rather than one flat category enum (V2-D033)
- Recruiter Decision Model
- AJ UI
- model selection
- operator edit/approval

### Acceptance
AJ output contains:
- role identity;
- ranked hiring signals;
- differentiators;
- hard/soft gaps;
- screen-out risks;
- recruiter questions;
- seniority expectations;
- customer-facing expectations;
- resume must-demonstrate signals.

Manual comparison against at least two real job postings is required.

## M5 — AC + APS + Human Gate 1

### Objective
Turn broad candidate context into a coherent candidate strategy.

### AC scope
- deterministic context retrieval
- one `AC_ASSESS` call by default
- `RequirementFit`
- evidence strength
- experience level
- differentiator analysis
- career narrative
- gaps/transferability

### APS scope
- candidate thesis
- lead/support/de-emphasize
- transferable framing
- career narrative
- seniority/technical/domain/customer/AI positioning
- three title options
- resume opening strategy
- experience/achievement/skills strategy

### Gate 1
Show:
- AJ
- RecruiterDecisionModel
- CandidateContext
- AC
- APS

Allow edit/rerun/approve. Rerun feedback is recorded as `ReviewFeedback` referencing the relevant `StageRun` (nullable for aggregate gate-level comments) rather than a separate per-stage `Target` enum (V2-D032).

### Acceptance
For a representative senior role:
- APS clearly explains why candidate should be considered;
- narrative uses breadth beyond lexical job matches;
- gaps remain visible;
- title options are credible;
- operator can rerun APS without unnecessarily rerunning AJ/AC.

**Closed in M0.2 (V2-D027):** APS has no precedent anywhere on `main` and is, along with `CandidateContextSnapshot` (M3B), the highest-judgment new component in V2. The APS acceptance criteria above must be demonstrated on at least one representative real job application before M6 (AB) begins — this is a hard milestone dependency, not deferred to M8's general benchmarking pass.

## M6 — AB Multi-Pass + Human Gate 2

### Objective
Generate expert-quality structured resume content.

### AB-1
`ResumeContentPlan`.

### AB-2
Structured `ResumeDraft`:
- 3 title options
- recommended title
- summary
- 3 experience sections mapped to static slots
- key achievements
- skills

### Warning pass
Split into `HARD_INTEGRITY` (blocks: malformed output, dangling claim/experience-slot/object references, invalid schema, wrong experience-slot cardinality, static-metadata replacement attempts) and `SOFT_REVIEW_WARNING` (surfaced only, never blocks: duplicates, suspicious metrics, overly long bullets, unsupported wording, overstatement) — see `docs/ARCHITECTURE.md` §11, V2-D026.

### AB-3
Recruiter critique.

### AB-4
Controlled refinement.

### Optional evaluator
Quality scores and advisory feedback.

### Gate 2
Operator reviews:
- structured content
- markdown
- warnings
- critique
- quality score
- static metadata

Operator feedback on any AB sub-stage (Plan/Draft/Critique/Refine) is `ReviewFeedback` referencing that sub-stage's `StageRun` directly — no `AB_PLAN`/`AB_DRAFT`/`AB_CRITIQUE`/`AB_REFINE` feedback-target enum is introduced (V2-D032).

### Acceptance
- model never controls company/date/title/location metadata;
- strong positioning survives into final draft;
- draft → critique → one refinement works;
- final output is materially stronger than one-shot baseline;
- operator can edit/approve final wording;
- `HARD_INTEGRITY` failures block the draft; `SOFT_REVIEW_WARNING` items never do (V2-D026).

## M7 — Integrated Workflow and Dashboard

### Objective
Provide the complete operator workflow.

### Scope
- JobApplication dashboard
- stage visualization
- resume from current stage
- status tracking
- application outcome tracking
- stage input/output navigation
- alternate model output history
- final markdown export/copy

### Acceptance
Full real workflow:

`Job → AJ → Context → AC → APS → Gate1 → AB Plan → Draft → Critique → Refine → Gate2 → final markdown`

## M8 — Model Qualification and Token Optimization

### Objective
Tune models/reasoning/token budgets for quality per token.

### Representative benchmark set
- Cloud Architect
- Solutions Architect
- GenAI Architect
- Automotive Architect
- Technical Product Owner
- Data/Cloud role

### Measure
- human quality score
- AJ completeness
- AC fit quality
- APS differentiation
- AB recruiter impact
- correction burden
- input/output tokens
- latency
- failures

### Acceptance
Document recommended stage defaults based on evidence, not assumption.

Target overall content quality: **≥90% of expert-assisted reference quality** on representative benchmark applications.
