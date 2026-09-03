# Requirement Traceability

Status: Milestones M1 through M6 are implemented and verified (see `docs/CURRENT_STATE.md`
for detail); M7 onward remain not implemented. Every row below is truthful as of this writing — a row
is marked "Implemented" only when a real artifact and a real verification step back it up. This
table must be updated whenever implementation status genuinely changes; never mark a row
"Implemented" or "Passing" without that real evidence.

Source of truth for requirement text: `requirements.md` (never edited to match this table — this
table is edited to match it). Architecture components are defined in `docs/ARCHITECTURE.md`.
Milestones are defined in `docs/IMPLEMENTATION_PLAN.md`.

Columns: **Requirement** (ID + one-line summary) · **Source** (requirements.md section) ·
**Component** (proposed owning app/module, see ARCHITECTURE.md) · **Milestone** · **Artifact**
(expected file/path once built) · **Verification** (how it will be checked) · **Status**.

## Goals, non-goals, actors

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| GOAL-001 truthful, tailored resume from job req + candidate background | §1 | resume_builder + whole pipeline | M7 | end-to-end pipeline | manual run producing a resume for a real job posting | Not implemented |
| GOAL-002 provider independence (OpenAI/NVIDIA NIM/Gemini v1, extensible) | §1, §9 | llm_provider | M2 | adapter interface + 3 provider adapters | unit tests per adapter + registry-driven stage routing | **Implemented** — 41 tests passing, 2026-09-02 |
| GOAL-003 human review at every meaningful checkpoint | §1 | reviews | M3, M5, M6 | gate views/templates | manual walkthrough of both gates | **Implemented** — M3 Candidate Memory review checkpoint plus Gate 1 (`test_gate1_services.py`/`test_views.py`) and Gate 2 (`test_gate2_services.py`/`test_gate2_views.py`), both also exercised in a real end-to-end manual walkthrough |
| NG-001 no PDF/DOCX, markdown only | §1, §14 | resume_builder | M6 | ResumeDraft renderer | code review confirms no rendering deps added | **Implemented** — `rendering/markdown.py` only, no PDF/DOCX dependency added |
| NG-002 no *product-level* multi-tenant/auth (v1.1: Django admin auth is standard framework infra, not excluded) | §1 | project-wide | M1 | settings — `django.contrib.admin`/`auth`/`sessions`/`contenttypes` installed; no product-level account/tenant/role model | code review confirms no product-level user model, admin auth present and reachable | **Implemented** — admin verified reachable + login-capable 2026-09-02 |
| NG-003 no cassette test suite in v1 | §1, §12 | test suite | M1–M8 | test directory | code review confirms no cassette/fixture infra added | Not started |
| NG-004 no broker/worker infra | §1, §11 | project-wide | M1 | requirements/dependency file | code review confirms no Celery/Redis deps | **Implemented** — requirements.txt contains no Celery/Redis dependency |
| NG-005 no cloud deployment | §1, §11 | project-wide | M1 | absence of deploy config | code review | **Implemented** — no deploy config exists |
| ACTOR-001 single operator = candidate | §2 | project-wide | M1 | absence of product-level multi-user models (Django admin auth is present, per NG-002 v1.1 clarification) | code review | **Implemented** — no product-level user model added |

## Candidate Memory (prerequisite)

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| PIPE-001 memory build is one-time/revisited, not per-job | §3, §4 | candidate_memory | M3 | CandidateMemory model + build view | manual test: build once, reuse across two job runs | Not implemented |
| MEM-001 input = free-form markdown, no fixed format | §4 | candidate_memory | M3 | MemorySourceDocument upload | unit test: arbitrary markdown accepted | **Implemented** — `raw_content` is unstructured text; no format validation imposed (`test_bootstrap.py`, `test_storage.py`) |
| MEM-002 LLM organizes content into structured memory keyed by resume sections | §4 | candidate_memory | M3 | memory-build service | integration test against fake adapter | **Implemented** — `services/extraction.py` + `services/storage.py`; `test_bootstrap.py` end-to-end against `FakeAdapter` |
| MEM-003 MemoryClaim provenance (v1.1: hash + quote + start/end line; M0.1/D-015: multiple exact supports per claim via MemoryClaimSupport, not one FK) | §4 | candidate_memory | M3 | MemoryClaim + MemoryClaimSupport (source_document FK, quotation, quotation_hash, start/end line) | unit test: every claim has ≥1 resolvable support with hash and line range; multi-support fixture persists all supports | **Implemented** — `test_models_lifecycle.py`, `test_storage.py` |
| MEM-004 confirmation_status unconfirmed/confirmed/retired (M0.1/D-015: + BLOCKED_CONFLICT); only confirmed+resume_eligible usable downstream | §4 | candidate_memory | M3, M5 | MemoryClaim.confirmation_status + `candidate_matching/services/retrieve.py` | `test_confirmation.py`, `test_snapshot_export.py`, `test_retrieve.py::test_excludes_unconfirmed_and_retired_claims` | **Implemented** |
| MEM-005 no fabrication — organize/phrase only, never invent | §4 | candidate_memory | M3 | memory-build prompt + validator | unit test: claim text must be traceable to a MemoryClaimSupport excerpt | **Implemented** — `services/classification.py`/`services/storage.py` provenance validators (not prompting alone); `test_classification.py`, `test_storage.py` |
| MEM-006 memory versioned; new markdown → new revision; old revisions kept (v1.1: snapshot + incremental processing per D-002, not full reprocess; M0.1/D-015: explicit status BUILDING/NEEDS_REVIEW/ACTIVE/SUPERSEDED lifecycle, base_revision lineage; **M0.1 audit fix**: claims/supports/rules/conflicts are mutable only while BUILDING/NEEDS_REVIEW, frozen the instant a revision is ACTIVE, and any later correction — including resolving a conflict left open at activation — creates a new revision rather than mutating the active one) | §4 | candidate_memory | M3 | CandidateMemory.version/status/base_revision + content-hash-based revision service | unit test: unchanged documents are not reprocessed and may carry confirmed status forward; changed/new documents are reprocessed and start unconfirmed; old revisions untouched; exactly one ACTIVE revision at a time; editing a claim/support/rule/conflict on an ACTIVE or SUPERSEDED revision is rejected; see MEM-018..020 below for the full activation/lifecycle test list | **Implemented** — `services/revision.py`, `services/bootstrap.py`; `test_revision.py`, `test_bootstrap.py`, `test_models_lifecycle.py` |

## Agent Jobber

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| AJ-001 input via URL or pasted text, both required | §5 | job_intake | M4 | `forms.py`/`services/intake.py::resolve_posting_source` | `test_intake_service.py`, `test_views.py` (both paths, both-supplied/neither-supplied validation errors) | **Implemented** — FakeAdapter-verified only, not live-provider-verified |
| AJ-002 detect/work in posting's own language | §5 | job_intake | M4 | AJ prompt (`services/analyze.py`) + `posting_language` field | `test_intake_service.py::test_german_posting_is_analyzed_in_its_own_language` (fixture, not live) | **Implemented** — prompt instructs language detection; real non-English live-model behavior not verified (no live call made) |
| AJ-003 recruiter mindset, not keyword extraction | §5 | job_intake | M4 | AJ prompt design (`services/analyze.py::SYSTEM_PROMPT`) | manual prompt review only — real output quality requires a live model, not assessed this session | **Implemented (prompt), quality unverified** — no live call made |
| AJ-004 identify mandatory/preferred reqs, responsibilities, ATS keywords, screening risks, implied expectations | §5 | job_intake | M4 | `schemas.py::AgentJobberAnalysis`/`RequirementCategory` | `test_schemas.py`, `test_intake_service.py` (all five categories + screening_risks round-trip) | **Implemented** |
| AJ-005 output tagged source_type + original raw text/URL | §5 | job_intake | M4 | `JobRequirementAnalysis` model | `test_intake_service.py::test_pasted_text_preserves_exact_operator_input`, `test_models.py` | **Implemented** |
| AJ-007 (v1.1/D-014) stable JobRequirement IDs (JR-001, ...) with category, stable within a JRA version | §5, §16 | job_intake | M4 | `JobRequirement` model + `services/intake.py::run_intake` | `test_models.py::JobRequirementIdAndOrderingTests`, `test_intake_service.py::test_model_cannot_control_canonical_jr_ids` | **Implemented** |
| AJ-006 URL fetch best-effort; always allow pasted-text fallback; UI surfaces fetch failure | §5 | job_intake | M4 | `services/fetch.py` + `views.py` | `test_fetch.py` (14 tests, all mocked), `test_views.py::IntakeViewUrlFetchFailureTests` | **Implemented** — only mocked HTTP responses exercised; no real-world URL fetch verified |

## Agent Candidate + Gate 1

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| AC-001 retrieve only relevant memory claims for this job | §6 | candidate_matching | M5 | `services/retrieve.py::retrieve_context` | `test_retrieve.py` (excludes unconfirmed/retired/ineligible/static-type/unmapped-engagement claims) | **Implemented** |
| AC-002 no-concealment — must not hide/minimize genuine gaps | §6, §13 | candidate_matching | M5 | `validators/disposition_coverage.py` | `test_fit_assessment.py::test_a_known_gap_is_never_softened_by_sanitization`, `test_disposition_coverage.py::test_a_known_gap_is_never_dropped` | **Implemented** |
| AC-003 output FitAssessment: matched reqs w/ evidence, gaps, risk notes (v1.1: replaced/strengthened by explicit RequirementAssessment disposition per requirement, D-014) | §6, §16 | candidate_matching | M5 | `models.py::FitAssessment`/`RequirementAssessment` | `test_models.py`, `test_fit_assessment.py` | **Implemented** |
| AC-004 (D-014) every relevant JobRequirement gets exactly one disposition (MATCH/PARTIAL/GAP/UNKNOWN); MATCH/PARTIAL require confirmed-claim evidence; absence of evidence never becomes MATCH | §16 | candidate_matching | M5 | `validators/disposition_coverage.py::sanitize_items`/`ensure_full_coverage` | `test_disposition_coverage.py` (11 tests) | **Implemented** |
| HITL-001 Gate 1 shows AJ+AC together (incl. per-requirement dispositions); approve or send feedback to AJ/AC → re-run | §6 | reviews | M5 | `views.py::gate1_view`, `templates/reviews/gate1.html` | `reviews/tests/test_views.py`, `test_gate1_services.py`; live manual walkthrough via `manage.py shell` against the real ACTIVE CandidateMemory (rolled back, zero residue) | **Implemented** |
| HITL-002 gate is DB precondition, not paused graph/agent state | §6, §10 | reviews | M5 | `JobApplication.approve_gate1`/`pipeline_phase` | `test_gate1_services.py::test_approval_survives_a_fresh_query_not_an_in_memory_assumption` | **Implemented** |

## Agent Builder + Gate 2

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| AB-001 input = JRA + (edited) FitAssessment + referenced MemoryClaims | §7 | resume_builder | M6 | `services/generate.py::build_request`, `services/build.py::build_resume_draft` | `test_build.py` | **Implemented** |
| AB-002 strongest truthful positioning; no claim untraceable to a confirmed MemoryClaim (v1.1: enforced via structured ResumeElement evidence IDs, D-014) | §7, §13, §16 | resume_builder | M6 | `validators/no_fabrication.py::validate_and_flatten` | `test_no_fabrication.py` (8 tests) | **Implemented** |
| AB-003 output = structured resume representation, rendered to markdown after validation (v1.1: template question resolved via docs/RESUME_OUTPUT_STRUCTURE.md) | §7, §16 | resume_builder | M6 (unblocked — see DECISIONS.md D-007) | `models.py::ResumeDraft`/`ResumeElement`, `rendering/markdown.py::render_resume_markdown` | `test_markdown_rendering.py` (9 tests) + manual walkthrough producing real rendered markdown | **Implemented** |
| AB-004 markdown-only for v1 | §7, §14 | resume_builder | M6 | `ResumeDraft.rendered_markdown` | code review confirms no PDF/DOCX dependency added | **Implemented** |
| HITL-003 Gate 2 renders AB output as rendered markdown; feedback → regeneration; approval → final deliverable | §7 | reviews | M6 | `views.py::gate2_view`, `templates/reviews/gate2.html` | `reviews/tests/test_gate2_views.py`, `test_gate2_services.py`; live manual walkthrough (rolled back, zero residue) | **Implemented** |

## Data model (§8)

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| DATA-001 CandidateMemory | §8 | candidate_memory | M3 | model | migration + admin check | **Implemented** — `0001_initial.py`; `test_models_lifecycle.py` |
| DATA-002 MemorySourceDocument (M0.1/D-015: + logical_source_key, source_role, trust_status, precedence, unchanged_from) | §8 | candidate_memory | M3 | model | migration + admin check | **Implemented** — `test_models_lifecycle.py` (immutability), `test_revision.py` (unchanged_from) |
| DATA-003 MemoryClaim (M0.1/D-015: + claim_id, stable_key, claim_type, subject_scope, experience_level, resume_eligible, duplicate_group_key, valid_from/valid_to) | §8 | candidate_memory | M3 | model | migration + admin check | **Implemented** — `test_storage.py`, `test_confirmation.py` |
| DATA-004 JobRequirementAnalysis (+ child JobRequirement, v1.1/D-014) | §8, §16 | job_intake | M4 | model | migration applied against real PostgreSQL; `test_models.py` (version uniqueness, append-only, stable IDs) | **Implemented** |
| DATA-005 FitAssessment (+ child RequirementAssessment, v1.1/D-014) | §8, §16 | candidate_matching | M5 | `models.py`, `migrations/0001_initial.py` | `manage.py migrate` clean; `admin.py` registered read-only | **Implemented** |
| DATA-006 ReviewFeedback | §8 | reviews | M5 | `models.py::ReviewFeedback`, `migrations/0001_initial.py` | `manage.py migrate` clean; `admin.py` registered read-only | **Implemented** |
| DATA-007 ResumeDraft (+ child ResumeElement, v1.1/D-014; structured representation, not markdown-first) | §8, §16 | resume_builder | M6 | `models.py`, `migrations/0001_initial.py` | `manage.py migrate` clean; `admin.py` registered read-only | **Implemented** |
| DATA-008 provider registry tables | §8 | llm_provider | M2 | `LLMProvider`/`LLMModel`/`StageModelAssignment` models | migration + admin check | **Implemented** |
| DATA-009 LLMCallLog (v1.1: token-first fields — input/cached-input/output/total; no cost field at M2) | §8, §13 | llm_provider | M2 | `LLMCallLog` model | migration + admin check | **Implemented** |
| DATA-010 (v1.1/D-012) JobApplication aggregate | §8, §17 | job_applications | M4 (see M1 scope note in `docs/CURRENT_STATE.md`: the app is scaffolded at M1, empty; fields deferred to M4 since its FK targets don't exist before then) | model | migration applied against real PostgreSQL; `test_models.py::AdvanceToAnalysisTests` | **Partially implemented** — `current_jra`/`pipeline_phase`/`application_outcome` exist; `current_fit_assessment`/`current_resume_draft` deferred to M5/M6 by design |
| DATA-011 (M0.1/D-015) MemoryClaimSupport — one or more exact supporting passages per claim | §8, §16 (M0.1) | candidate_memory | M3 | model | migration + admin check; unit test: multi-support fixture | **Implemented** — `test_storage.py` (EN+DE multi-support, PRIMARY/CORROBORATING/GERMAN_EXPRESSION) |
| DATA-012 (M0.1/D-015) CandidateRule — constraint/positioning content, never resume evidence | §8, §16 (M0.1) | candidate_memory | M3 | model | migration + admin check | **Implemented** — `test_storage.py`, `test_classification.py` |
| DATA-013 (M0.1/D-015) MemoryConflict — explicit contradiction record, blocks affected claims while OPEN | §8, §16 (M0.1) | candidate_memory | M3 | model | migration + admin check; unit test: OPEN conflict blocks eligibility | **Implemented** — `test_conflicts.py` |

## LLM provider abstraction (§9)

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| LLM-001 every call independently configurable; new provider ⇒ zero pipeline changes | §9 | llm_provider | M2 | `adapters.get_adapter_for_stage()` + registry | unit test: swapping StageModelAssignment changes routing with no pipeline code change | **Implemented** — `test_routing.py` |
| LLM-002 single adapter interface; pipeline never touches provider SDK | §9.1 | llm_provider | M2 | `NormalizedLLMRequest`/`NormalizedLLMResult` in `types.py` | code review: no provider SDK import outside llm_provider | **Implemented** — REST via `requests` only, no SDK deps |
| LLM-003 registry is DB-backed, admin-editable | §9.2 | llm_provider | M2 | Django admin registration | manual admin check | **Implemented** — `test_registry.py` exercises admin CRUD |
| LLM-004 LLMProvider: name, base_url, credential reference only | §9.2 | llm_provider | M2 | `LLMProvider` model | unit test: no credential value field exists | **Implemented** |
| LLM-005 LLMModel: capability flags | §9.2 | llm_provider | M2 | `LLMModel` model | schema check | **Implemented** |
| LLM-006 StageModelAssignment: stage → model, admin editable | §9.2 | llm_provider | M2 | `StageModelAssignment` model | manual admin check | **Implemented** |
| LLM-007 per-provider structured-output handling (OpenAI/NVIDIA NIM/Gemini quirks) | §9.3 | llm_provider | M2 | `schema_translation.py` + 3 adapters | unit test per adapter against fixture schemas | **Implemented** (deterministic translation tests only — live-provider behavior not yet smoke-verified, see M2 risk note) |
| LLM-008 retry only transient failures; streaming retry only if nothing streamed; cap+backoff | §9.4 | llm_provider | M2 | `retry.py` | unit test: retry classification matrix | **Implemented** — `test_retry.py` |
| LLM-009 normalized typed error taxonomy; no raw provider content unsanitized | §9.5 | llm_provider | M2 | `errors.py` | unit test: sanitizer strips response bodies | **Implemented** — `test_errors.py` |
| LLM-010 every call writes an LLMCallLog row | §9.6 | llm_provider | M2 | `BaseLLMAdapter._write_call_log` | unit test: fake adapter call produces exactly one log row | **Implemented** — `test_fake_adapter.py` |
| LLM-011 adapter layer not extracted into shared package now | §9.7 | llm_provider | M2 | (non-goal) | code review confirms no shared-package dependency | **Implemented** (as a non-goal — no such dependency exists) |

## Human-in-the-loop mechanics (§10)

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| HITL-004 gates = plain app state, checked fresh, never paused graph state | §10 | reviews | M5, M6 | `JobApplication.approve_gate1`/`approve_gate2` | `test_gate1_services.py`, `test_gate2_services.py` | **Implemented** |
| HITL-005 approval = generic reusable action | §10 | reviews | M5, M6 | `reviews/services.py::approve_gate1`/`approve_gate2` (same shape, one per gate) | `test_gate1_services.py`, `test_gate2_services.py` | **Implemented** |
| HITL-006 feedback submission = normal DB write, re-checked fresh next run | §10 | reviews | M5, M6 | `reviews/services.py::submit_gate1_feedback`/`submit_gate2_feedback` | `test_gate1_services.py::test_ac_feedback_...`/`test_aj_feedback_...`, `test_gate2_services.py` | **Implemented** |
| HITL-007 freshness/staleness re-validated on upstream change | §10 | reviews | M7 | freshness check at each step start | unit test: mutate upstream after draft built, confirm downstream flagged stale | Not implemented |

## Stack (§11)

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| STACK-001 Django | §11 | project-wide | M1 | `config/` project (Django 5.1) | `manage.py check` | **Implemented** — passes clean, 2026-09-02 |
| STACK-002 PostgreSQL via Docker locally | §11 | project-wide | M1 | `docker-compose.yml` | `docker compose up` + migration succeeds | **Implemented** — `db` healthy, `manage.py migrate` applied cleanly |
| STACK-003 no queue/broker; synchronous in-request LLM calls | §11 | project-wide | M1 | absence of Celery/Redis deps | code review | **Implemented** — `requirements.txt` has no queue/broker dependency |
| STACK-004 server-rendered Django templates, no SPA | §11 | project-wide | M1 | `templates/base.html` | code review | **Implemented** — base template wired into `TEMPLATES[0]["DIRS"]` |
| STACK-005 Pydantic for LLM-output validation | §11 | llm_provider | M2 | `BaseLLMAdapter.generate()` re-validation | unit test | **Implemented** — `output_schema.model_validate()` in the shared adapter call path |
| STACK-006 orchestration: plain sequence, LangGraph not adopted for v1 (D-001 approved; post-M7 re-evaluation checkpoint scheduled) | §11 | project-wide | M1 | service-function sequence | code review confirms no langgraph/langchain/agents-sdk dependency | **Implemented** — no such dependency in `requirements.txt` |
| STACK-007 secrets via `.env`, never committed; DB stores references only | §11 | project-wide | M1 | `.env.example` + `.gitignore` | code review + git history check | **Implemented** — `.env` gitignored and never staged; settings read all secrets from environment |
| STACK-008 local-first; minimal CI (lint+test) worth adding early (v1.1: local repeatable quality commands are the hard M1 requirement; remote CI is not mandatory) | §11 | project-wide | M1 | `Makefile` (`check`/`test`/`lint`) | local quality commands run and pass | **Implemented** — `make check`/`make test`/`make lint` all pass repeatably; no remote CI added, none required |

## Testing strategy (§12)

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| TEST-001 Phase 1: ordinary unit tests for non-LLM logic from day one | §12 | test suite | M1–M8 | per-milestone test modules | `pytest`/`manage.py test` passing | Partially implemented — `llm_provider` (M2) has 41 and `candidate_memory` (M3, post-audit-repair) has 164 passing deterministic tests (205 total); M4–M8 apps not yet started |
| TEST-002 Phase 2: cassette/recorded-response layer once outputs stabilize | §12 | test suite | Deferred (post-v1) | n/a | n/a | Not started (deliberately deferred) |

## Non-functional / hard invariants (§13)

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| NFR-001 no-fabrication: every resume claim traces to a confirmed MemoryClaim | §13 | resume_builder | M6 | `validators/no_fabrication.py` | `test_no_fabrication.py`, `test_build.py::test_fabricated_evidence_aborts_the_whole_build_no_draft_persisted` | **Implemented** |
| NFR-002 no-concealment: AC gap analysis never softened | §13 | candidate_matching | M5 | `validators/disposition_coverage.py` | `test_fit_assessment.py::test_a_known_gap_is_never_softened_by_sanitization` | **Implemented** |
| NFR-003 secrets never in DB/VCS | §13 | project-wide | M1 | `.env` pattern + registry credential-reference design | code review + git-secrets style scan | **Implemented** — `LLMProvider.credential_env_var` stores only the variable name; `test_credential_value_is_never_a_field` |
| NFR-004 token visibility, token-first (v1.1/D-008: dollar-cost optional/deferred, must not block M2) | §13 | llm_provider | M2, M8 | `LLMCallLog` (input/cached-input/output/total tokens) + reporting view | manual report check against known fixture calls | Partially implemented — `LLMCallLog` token fields exist and are populated (M2); the per-job/stage/provider *reporting view* is still M8 |
| NFR-005 extensibility: new provider = adapter + registry rows only | §13 | llm_provider | M2 | `ADAPTER_CLASSES` registry | manual test: add a 4th fake provider with no pipeline change | **Implemented** — `FakeAdapter`/`LLMProvider.ProviderType.FAKE` is exactly this 4th provider, added with zero routing-code changes |

## Open questions / deferred (§14)

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| FUT-001 exact resume markdown template | §14 | resume_builder | M6 | docs/RESUME_OUTPUT_STRUCTURE.md, `rendering/markdown.py` | `test_markdown_rendering.py` + manual walkthrough rendered markdown | **Implemented** — see D-007 |
| FUT-002 PDF/DOCX rendering | §14 | (future) | Deferred | n/a | n/a | Not started (deliberately deferred) |
| FUT-003 cassette test suite | §12, §14 | (future) | Deferred | n/a | n/a | Not started (deliberately deferred) |
| FUT-004 multi-candidate support | §14 | (future) | Deferred | n/a | n/a | Not started (deliberately deferred) |
| FUT-005 URL-fetch hardening (headless rendering, retry/backoff) | §14 | job_intake | Deferred | n/a | n/a | Not started (deliberately deferred) |
| FUT-006 streaming UI feedback | §14 | (future) | Deferred | n/a | n/a | Not started (deliberately deferred) |
| FUT-007 (v1.1) dollar-cost calculation for LLM usage | §13, §14 | llm_provider | Deferred | n/a | n/a | Not started (deliberately deferred, D-008) |
| FUT-008 (v1.1) orchestration/agent framework adoption (LangGraph/LangChain/OpenAI Agents SDK) | §11, §14 | project-wide | Post-M7 checkpoint | docs/DECISIONS.md D-001 entry recording the checkpoint outcome | review conducted after M7; decision recorded | Not started (deliberately deferred, D-001) |

## Structured AJ → AC → AB traceability (§16, D-014 — v1.1 addition)

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| TRACE-001 Agent Jobber assigns stable JobRequirement IDs (JR-001, ...) with category, stable within a JRA version | §16 | job_intake | M4 | JobRequirement model | `test_models.py::JobRequirementIdAndOrderingTests` | **Implemented** |
| TRACE-002 Agent Candidate produces one RequirementAssessment (disposition MATCH/PARTIAL/GAP/UNKNOWN) per relevant JobRequirement | §16 | candidate_matching | M5 | `RequirementAssessment` model + `validators/disposition_coverage.py` | `test_fit_assessment.py::test_every_relevant_requirement_gets_exactly_one_assessment` | **Implemented** |
| TRACE-003 Agent Builder produces structured ResumeElements (text, supporting_memory_claim_ids, matched_job_requirement_ids) for every factual output, validated before markdown rendering | §16 | resume_builder | M6 | `ResumeElement` model + `validators/no_fabrication.py` | `test_no_fabrication.py` | **Implemented** |
| TRACE-004 evidence-attachment/eligibility is the truth test, not text/embedding similarity; human review remains responsible for wording fairness | §16 | resume_builder, reviews | M6 | `validators/no_fabrication.py` (ID checks only, no similarity matching) + Gate 2 UI | code review confirms validator checks ID existence/confirmation/eligibility only; `templates/reviews/gate2.html` renders full evidence inspection | **Implemented** |

## Job vacancy / application tracking dashboard (§17 — v1.1 addition)

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| DASH-001 JobApplication aggregate: current_jra/current_fit_assessment/current_resume_draft pointers, pipeline_phase, application_outcome | §17 | job_applications | M4, M5, M6 (app scaffolded empty at M1 — see `docs/CURRENT_STATE.md`) | JobApplication model | `test_models.py::JobApplicationDefaultsTests`, `AdvanceToAnalysisTests`, `test_gate1_transitions.py`, `test_gate2_transitions.py` | **Implemented** — all three current-version pointers now exist |
| DASH-002 pipeline_phase (NEW/ANALYSIS/PREPARATION/READY) advances automatically at defined transition points, no impossible combinations | §17 | job_applications | M4, M5, M6, M7 | `advance_to_analysis`/`approve_gate1`/`approve_gate2` | `test_gate1_transitions.py`, `test_gate2_transitions.py` (incl. rejecting an out-of-order/double transition) | **Partially implemented** — NEW→ANALYSIS→PREPARATION→READY all implemented and tested; the M7 dashboard list/detail view itself does not exist yet |
| DASH-003 application_outcome (NOT_APPLIED/APPLIED/INTERVIEWING/REJECTED) set explicitly by operator, independent of pipeline_phase | §17 | job_applications | M7 | dashboard action view | unit test: setting APPLIED then regenerating a resume does not revert application_outcome | Not implemented |
| DASH-004 dashboard list/detail view: company, title, dashboard status, pipeline_phase, application_outcome, dates, AJ/AC/draft existence-and-currency, review-required, staleness | §17 | job_applications | M7 | dashboard templates/views | manual walkthrough with fixture applications in different states | Not implemented |
| DASH-005 dashboard is durable DB state, not inferred from an in-memory session; server-rendered Django, no SPA | §17 | job_applications | M7 | dashboard views | code review + manual restart-and-reload check | Not implemented |

## Operator-approved Candidate Memory bootstrap, classification, and reference export (D-015 — M0.1 addition)

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| MEM-007 the three named markdown files are operator-approved bootstrap evidence, with a stated source precedence (primary profile > English corpus > German corpus) | §4 (M0.1) | candidate_memory | M3 | MemorySourceDocument.source_role/trust_status/precedence | unit test: bootstrap run tags each file with correct role/precedence | **Implemented** — `bootstrap_candidate_memory` command's `_PRECEDENCE` mapping + a fourth, higher-precedence `AC-OPERATOR_FACT_RESOLUTIONS.md` source (see `docs/CURRENT_STATE.md`); mechanics verified by `test_bootstrap.py`; the real four docs/AC files have since been bootstrapped for real against live NVIDIA Nemotron, producing revision 1 (id=6, preserved as audit evidence, never activated) and, after recovery, revision 2 (id=7) — **activated 2026-09-03** as the sole `ACTIVE` CandidateMemory (see `docs/CURRENT_STATE.md`) |
| MEM-008 English is the canonical language for stored MemoryClaim facts; German evidence supports the same canonical claim; full-memory translation is not required on every build | §4 (M0.1) | candidate_memory | M3 | MemoryClaim.canonical_text + MemoryClaimSupport.source_language | unit test: claim built from German-only support still has English canonical_text | **Implemented** — `test_storage.py` (EN/DE grouping tests) |
| MEM-009 content classified into evidence / constraint / positioning planes; constraint and positioning content never becomes resume-eligible factual claims | §4 (M0.1) | candidate_memory | M3 | classify.py service + MemoryClaim.resume_eligible + CandidateRule | unit test: fixture constraint/positioning sentences become CandidateRule, not MemoryClaim | **Implemented** — `services/classification.py`; `test_classification.py`, `test_storage.py` |
| MEM-010 a canonical claim may have multiple exact provenance supports (English + German, primary + corroborating) | §4 (M0.1) | candidate_memory | M3 | MemoryClaimSupport | unit test: multi-support fixture (see DATA-011) | **Implemented** — `test_storage.py` |
| MEM-011 contradictions are detected and explicitly represented; an unresolved conflict makes affected claims ineligible until operator resolution | §4 (M0.1) | candidate_memory | M3 | MemoryConflict (see DATA-013) | unit test: OPEN conflict blocks eligibility; RESOLVED restores it | **Implemented, verified end-to-end after audit repair** — `services/conflicts.py` + `services/comparable_values.py`; `test_conflicts.py` (detector unit tests) and `test_conflict_pipeline.py` (the named Ford/Continental/Maruti/German-B1-B2 operator-resolution scenarios driven through the real `build_revision`/`store_extracted_item` path with scripted `FakeAdapter` responses, not hand-built claims — see `docs/CURRENT_STATE.md`'s "M3 post-audit repair" section for the gap this closed) |
| MEM-012 initial bootstrap is an explicit, repeatable management command, never automatic on startup/migration/deployment | §4 (M0.1) | candidate_memory | M3 | management/commands/bootstrap_candidate_memory.py | code review confirms no auto-invocation path; manual command run | **Implemented** — code review confirms no hook into `migrate`/`runserver`/`manage.py test`; command run manually against synthetic fixtures (`test_bootstrap.py`) |
| MEM-013 ongoing additions/corrections go through the Candidate Memory UI, creating a new OPERATOR_UPDATE source and a new CandidateMemory revision; direct DB editing is not the normal workflow | §4 (M0.1) | candidate_memory | M3 | UI "add/update profile" workflow | manual walkthrough: operator update creates a new revision | **Implemented** — `views.update_profile_form` + `services/bootstrap.build_revision_from_operator_text`; `test_bootstrap.py::BootstrapOperatorTextUpdateTests`, `test_views.py::UpdateProfileFormTests` |
| MEM-014 a concise human-readable snapshot is generated from the activated DB revision; it is not an evidence source, not authoritative runtime state, and must never be re-ingested | §4 (M0.1) | candidate_memory | M3 | docs/CANDIDATE_MEMORY_SNAPSHOT.md + snapshot_export.py | code review: bootstrap command rejects/ignores the snapshot file as an input; manual regeneration check | **Implemented** — `services/snapshot_export.py`; `test_snapshot_export.py` (includes snapshot-import-refusal via `test_bootstrap.py::test_snapshot_file_refused_as_import_source` and byte-identical regeneration) |
| MEM-015 runtime retrieval is bounded and explainable; no full source-document or snapshot dump is ever placed in a pipeline LLM call | §4, new §9 (ARCHITECTURE.md, M0.1) | candidate_matching | M5 | `services/bounded_retrieval.py::build_bounded_context` (eligibility -> dedup -> per-requirement lexical candidate generation -> AC_RANK relevance ranking -> capped selection), `services/retrieval_limits.py` | `test_bounded_retrieval.py` incl. a real-scale (~1,200-claim) corpus test proving the request stays within budget; `test_dedup.py`, `test_candidate_generation.py`, `test_rank.py`, `test_rule_selection.py` | **Implemented** -- corrected 2026-09-03: an independent audit found the milestone's original `retrieve_context()` sent the *entire* eligible pool (measured at ~50,785 estimated tokens against the real revision-2 corpus) with no relevance filtering or count/token bound; that row was marked Implemented on a 4-claim fixture test that did not exercise scale. Fixed with the actual bounded pipeline this row now cites. |
| MEM-016 no vector database/embedding-based memory required for v1; PostgreSQL structured retrieval + bounded LLM ranking is sufficient; pgvector is a possible future optimization only | §4 (M0.1) | candidate_memory, candidate_matching | M3, M5 | `services/candidate_generation.py` (deterministic lexical scoring, not embeddings) + `services/rank.py` (the bounded LLM ranking step, AC_RANK) | `test_candidate_generation.py`, `test_rank.py`; code review confirms no pgvector/embedding dependency added | **Implemented** -- the "bounded LLM ranking" half of this requirement is now a real stage (AC_RANK), not merely deferred; no vector database was added, consistent with the original "not started (deliberately deferred)" note for that part. |
| MEM-017 actual job titles (evidence) and suggested target titles (positioning) are never conflated; a suggested title is never presented as historical fact | §4 (M0.1) | candidate_memory | M3 | MemoryClaim.claim_type vs. CandidateRule.rule_type=POSITIONING | unit test: fixture with both an actual and a suggested title keeps them as distinct record types | **Implemented** — `test_classification.py::test_positioning_title_never_stored_as_evidence`, `test_storage.py` routing tests |
| MEM-018 (M0.1 audit fix) a revision's claims/supports/rules/conflict-resolutions are mutable only while status is BUILDING or NEEDS_REVIEW; once ACTIVE (or SUPERSEDED), all such content is frozen and cannot be edited in place | §4 (ARCHITECTURE.md, M0.1) | candidate_memory | M3 | CandidateMemory.status + service-layer edit guard | unit test: attempting to confirm/correct/retire/restore a claim or resolve a conflict on an ACTIVE/SUPERSEDED revision is rejected | **Implemented, with an honestly-scoped trust boundary (audit repair)** — enforced at the *model* layer (`_RevisionScopedModel`'s instance `save()`/`delete()`), covering everything services, views, forms, and Django admin's edit/delete forms actually do; this does **not** cover `QuerySet.update()`/`bulk_update()` or raw SQL, which bypass instance hooks and are not guarded against — no database trigger/constraint backs this, only the ORM's normal instance API. `test_models_lifecycle.py`, `test_lifecycle_service.py` |
| MEM-019 (M0.1 audit fix) activation is an explicit operator action; exactly one revision is ACTIVE at a time; activating a revision atomically supersedes the prior ACTIVE one; activation is blocked if a validation failure could let unsupported/misclassified content become eligible; a revision may still activate with an unresolved MemoryConflict only if every affected claim remains BLOCKED_CONFLICT, and the activation UI must warn the operator that those conflicts/claims are being excluded | §4 (ARCHITECTURE.md, M0.1) | candidate_memory | M3 | activation service + activation UI warning | unit test: single-ACTIVE-revision invariant; atomic ACTIVE→SUPERSEDED transition; activation blocked on failing provenance/classification validation; activation permitted with an OPEN conflict only when affected claims are BLOCKED_CONFLICT, with a warning surfaced | **Implemented** — `services/lifecycle.py` (`activate_revision` transactional with `select_for_update()` + DB partial-unique-index backstop); `views.activate_confirm` surfaces warnings; `test_lifecycle_service.py` |
| MEM-020 (M0.1 audit fix) "add/update profile" always creates a new working revision from the current ACTIVE one; resolving a conflict left open at activation, or making any other correction after activation, requires a new revision, never an edit to the ACTIVE one; snapshot regeneration against the ACTIVE revision is deterministic and does not mutate it | §4 (ARCHITECTURE.md, M0.1) | candidate_memory | M3 | ongoing-update workflow + snapshot_export.py | unit test: operator update creates a new BUILDING revision with base_revision set to the current ACTIVE one; post-activation conflict resolution is rejected in place and only succeeds via a new revision; regenerating the snapshot twice against the same ACTIVE revision produces byte-identical output | **Implemented** — `test_bootstrap.py::BootstrapOperatorTextUpdateTests`, `test_lifecycle_service.py::ConflictResolutionActionTests::test_conflict_actions_refused_on_active_revision`, `test_snapshot_export.py::test_regenerating_twice_against_same_revision_is_byte_identical` |

## Deterministic static-profile boundary (D-019 — 2026-09-03 addition)

| Requirement | Source | Component | Milestone | Artifact | Verification | Status |
|---|---|---|---|---|---|---|
| CE-001 employment identity, client organisation, titles, locations, and dates are operator-owned structured facts, never generated or rewritten by an LLM | §8 (D-019) | candidate_memory | M3 | CareerEngagement model | unit test: model fields hold exactly these facts; no service path derives them from an LLM call | **Implemented** — `models.py::CareerEngagement`; `test_career_engagement.py` |
| CE-002 a CareerEngagement is an admin-editable registry independent of any CandidateMemory revision's own lifecycle, gated by its own approval_status | §8 (D-019) | candidate_memory | M3 | CareerEngagement.approval_status + admin.py | unit test: not a `_RevisionScopedModel`; code review confirms no dependency on CandidateMemory.status | **Implemented** — `models.py::CareerEngagement` (plain `models.Model`); `admin.py::CareerEngagementAdmin` |
| CE-003 MemoryClaims may reference a CareerEngagement via a reviewable proposed/approved/rejected mapping workflow; deterministic mappings may be proposed, ambiguous mappings remain unresolved, no source re-extraction is required | §8 (D-019) | candidate_memory | M3 | ClaimEngagementMapping model + services/engagement_mapping.py | unit test: exact match proposes; 0 or 2+ matches leave it unresolved/ambiguous, never guessed; rerun is idempotent | **Implemented** — `test_engagement_mapping.py` |
| CE-004 a mapping can be proposed and approved for a claim belonging to an already-ACTIVE CandidateMemory revision, without violating that revision's frozen-content invariant | §8 (D-019), extends MEM-018 | candidate_memory | M3 | ClaimEngagementMapping (separate table, never a MemoryClaim field) | unit test: propose + approve against a claim on a revision frozen to ACTIVE; claim row itself is asserted unchanged | **Implemented** — `test_engagement_mapping.py::test_mapping_can_be_proposed_and_approved_for_a_claim_on_an_active_revision` |
| CE-005 deterministic calculations exist for engagement duration, total non-overlapping experience (no double-counting), current/past status, and stored organisation presentation | §8 (D-019) | candidate_memory | M3 | CareerEngagement.duration_months/is_current/displayed_organization + services/career_engagement.py | unit test: known/present/unknown-end duration cases; overlapping vs. non-overlapping totals; all three presentation_mode selections | **Implemented** — `test_career_engagement.py` |
| CE-006 the planned M5 RequirementAssessment evidence-attachment shape may reference supporting_memory_claim_ids and supporting_engagement_ids; static requirements (tenure, dates, location, employment relationship) are assessed locally, without an LLM call | §8/§16 (D-019) | candidate_matching | M5 | `candidate_matching/services/static_requirements.py` (classify/assess for TOTAL_EXPERIENCE/TENURE/CURRENT_PAST_STATUS/LOCATION/EMPLOYER_CLIENT_RELATIONSHIP) | `test_static_profile_boundary.py`, `candidate_matching/tests/test_static_requirements.py` (16 tests), `test_fit_assessment.py::test_static_requirement_is_never_sent_to_the_llm` | **Implemented** |
| CE-007 the planned M6 Agent Builder input/output schema excludes employer/title/location/dates entirely; the deterministic renderer retrieves those fields from an approved CareerEngagement record; an unknown or unapproved engagement_id fails validation | §7/§16 (D-019) | resume_builder | M6 | `resume_builder/schemas.py` (extra="forbid", no static fields) + `rendering/markdown.py` (calls `render_engagement_header`) | `test_no_fabrication.py`, `test_markdown_rendering.py`, `test_static_profile_boundary.py` | **Implemented** |

## Maintenance rule

Update this file whenever a milestone changes implementation status. A row may only move to
"Implemented" alongside a real merged artifact, and only to "Passing" alongside a real test run —
never in anticipation of work not yet done.
