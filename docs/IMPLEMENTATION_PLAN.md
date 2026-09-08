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
M1 — Foundation + reuse audit — COMPLETE
 |
M2 — LLM control plane + operator call console — COMPLETE
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

### Acceptance — MET (see docs/CURRENT_STATE.md for evidence)
- application runs — verified via `make start` + live HTTP requests (home `200`, admin `302` unauthenticated);
- Postgres works — migrations applied against a real local PostgreSQL 16 instance (Docker), verified table-by-table;
- admin works — `JobApplication`/`StageRun`/`JobApplicationStageState` registered and reachable, verified by test and live request;
- tests/check/lint pass — 42/42 tests, `manage.py check` clean, `ruff check .` clean;
- reuse decisions documented (`docs/V2_REUSE_AUDIT.md`) — infra files (`docker-compose.yml`, `.env.example`, `.gitignore`, `Makefile`, `pyproject.toml`, `requirements.txt`, `templates/base.html`, `manage.py`, `config/asgi.py`/`wsgi.py`) cherry-picked from `main` per the audit's REUSE_AS_IS classification; `config/settings.py`/`urls.py` adapted (REUSE_WITH_ADAPTATION) to M1's actual app set;
- `JobApplication`/`StageRun`/`JobApplicationStageState` schema matches the closed M0.2 design, not a growing `current_*` FK list — `StageRun`'s provider/model fields are deferred to M2 per V2-D036 (implementation-sequencing, not an architecture change);
- no accidental import of old V1 pipeline semantics (in particular, no `AC_NORMALIZE`/`AC_RANK`/`AC_MATCH` stage identifiers) — enforced by an automated test that scans the actual source tree.

M1 implementation note: only `job_applications` was implemented in M1 (V2-D036); the other app boundaries listed above are created when their own milestone begins. The local PostgreSQL dev database was found to contain a stale V1 schema from unrelated prior work and was reset before verification — see V2-D037.

## M2 — LLM Control Plane and Operator Call Console

### Objective
Build or selectively reuse the provider/model execution foundation.

### Scope
- OpenAI
- NVIDIA NIM
- Gemini
- OpenRouter
- provider registry
- model registry, with `supported_reasoning_levels` as the canonical per-model reasoning capability (V2-D034)
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
- `StageRun`'s deferred provider/model/reasoning_level/max_output_tokens/temperature fields (V2-D036), now that `llm_provider.LLMProvider`/`LLMModel` exist

### Acceptance — MET (see docs/CURRENT_STATE.md for evidence)
- fake-adapter deterministic tests pass — met, `llm_provider/tests/test_fake_adapter.py`;
- each provider adapter's schema translation is tested without live credentials — met, `llm_provider/tests/test_real_adapter_config_guards.py` and `test_schema_translation.py` mock `requests.post`/exercise translation directly; zero live calls;
- stage default routing works — met, `StageModelAssignment` + `get_adapter_for_stage` (`llm_provider/tests/test_adapters_routing.py`);
- runtime override works — met, `get_adapter_for_model`/`run_with_model_override` (`llm_provider/tests/test_console.py`);
- input/output token metadata is captured — met, `LLMCallLog` token fields, written by `BaseLLMAdapter._write_call_log`;
- operator can run a stage manually — met, `llm_provider.services.console.run_stage_manually` (V2-D040: Django admin + this service layer is M2's UI surface; interactive per-call UI deferred to M4, see V2-D040);
- `.env` credentials are not stored in DB — met, `LLMProvider.credential_env_variable` stores only the variable name (`llm_provider/tests/test_models.py::LLMProviderTests::test_credential_value_never_stored`);
- OpenRouter behaves as a normal provider — met, `OpenRouterAdapter` is one more `ADAPTER_CLASSES` entry, routed/validated identically to every other provider;
- configured providers can be manually smoke-tested — met, `manage.py llm_smoke_test <provider>` (opt-in only, never run by `make test`/`make verify`);
- `reasoning_level` is rejected, both at `StageModelAssignment` save time and on a per-call override, if it is not a member of the selected model's `supported_reasoning_levels` (V2-D034) — met, `StageModelAssignment.clean()` and `llm_provider.validation.validate_reasoning_level`, both tested.

M2 implementation note: adapters/schema translation/retry/error-taxonomy were reused, file-by-file, from `main`'s original M2 commit (`8627a93`) per `docs/V2_REUSE_AUDIT.md`'s REUSE_AS_IS classification, then adapted for V2's exact field names, the shared `job_applications` stage vocabulary, `supported_reasoning_levels` (V2-D034), and a fourth provider (OpenRouter, reused from `main`'s early `a541a0a` commit with its later data-collection-policy/`top_p` hardening deliberately dropped as out of V2's documented M2 scope). `main`'s much later, heavily-hardened `llm_provider` state (GPT-5.4 defaults, OpenRouter free-router, per-stage read timeouts, rate-limit diagnostics) was deliberately **not** reused — none of that is in `docs/ARCHITECTURE.md` §12/§13's frozen field set, and pulling it in would have been exactly the "copy the legacy V1 pipeline wholesale" this milestone's task explicitly disallowed. `docs/DECISIONS.md` V2-D039/V2-D040/V2-D041 record the specific, deliberate deviations from the literal `docs/ARCHITECTURE.md` §13 field list and the M2 UI scope boundary.

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
