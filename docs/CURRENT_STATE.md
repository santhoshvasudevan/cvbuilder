# Current State

Last updated: 2026-09-02 (M0 — planning baseline reviewed by product owner; still pre-
implementation).

## Summary

This repository is **pre-implementation**. The M0 planning baseline (requirements analysis,
architecture, domain model, milestones, decisions log, test strategy, traceability matrix) has now
undergone product-owner review. Several decisions were approved (with modification, in most
cases), one new decision was added and approved (D-014, structured AJ→AC→AB traceability), and
`requirements.md` was explicitly amended by the product owner to capture the new/clarified
requirements (see its own Amendment Log). There is still no Django project, no database, no
dependencies installed, no migrations, no tests, and no application code of any kind. No LLM
provider has been called.

## What exists

- `requirements.md` — the authoritative product requirements, now **v1.1** (amended 2026-09-02
  with explicit product-owner authorization; see its Amendment Log for the exact diff summary).
- `docs/ARCHITECTURE.md` — proposed architecture, component boundaries, domain model, provider-
  abstraction design; updated for all approved decisions.
- `docs/IMPLEMENTATION_PLAN.md` — milestone sequence M0–M8, corrected dependency graph (M3/M4 both
  depend only on M2), a post-M7 architecture/orchestration review checkpoint, and the dashboard
  requirement folded into M4/M7.
- `docs/DECISIONS.md` — D-001 through D-014; see the "Decisions" section below for exact statuses.
- `docs/TEST_STRATEGY.md` — phased testing strategy, now including an explicit opt-in manual
  provider-smoke-test process (M2) kept separate from the deterministic automated suite.
- `docs/REQUIREMENT_TRACEABILITY.md` — every requirement ID (including the new v1.1 ones) mapped
  to a component/milestone/artifact/verification/status (all "Not implemented"/"Not started").
- `docs/RESUME_OUTPUT_STRUCTURE.md` — **new**: the v1 structured resume representation and
  deterministic markdown rendering contract, resolving the former M6 template blocker (D-007).
  Contains no candidate-specific factual content — structural/content-design reference only.
- `CLAUDE.md` — durable repository instructions, updated for the newly approved invariants
  (`JobApplication` aggregate, structured traceability, Django admin auth clarification,
  token-first observability).

## What does not exist

- No Django project/settings, no `manage.py`, no apps (including no `job_applications` app yet,
  even though it's now approved and scaffolded-early per D-012's sequencing note).
- No `docker-compose.yml`, no PostgreSQL instance.
- No dependency manifest (`requirements.txt`/`pyproject.toml`).
- No `.env`/`.env.example`.
- No migrations, no models, no admin registrations.
- No LLM provider adapters, no registry data, no `LLMCallLog` rows.
- No tests.
- No CI configuration (and per the M1 correction, a remote CI "green link" is not required to
  start — local repeatable quality commands are the hard M1 requirement).

## Decisions (see `docs/DECISIONS.md` for full detail)

- **D-001** Orchestration — APPROVED WITH FUTURE RE-EVALUATION (plain Django orchestration for v1;
  no LangGraph/LangChain/Agents SDK until a post-M7 checkpoint finds concrete need).
- **D-002** CandidateMemory revisioning — APPROVED WITH MODIFICATION (snapshot + incremental,
  content-hash-based, not full reprocess).
- **D-003** MemoryClaim provenance — APPROVED WITH MODIFICATION (hash + quote + start/end line,
  not quote-only).
- **D-004** URL fetch strategy — APPROVED IN PRINCIPLE (extraction library choice deferred to M4).
- **D-005** Structured-output representation — APPROVED (Pydantic canonical, provider translation
  isolated in `llm_provider`).
- **D-006** Freshness mechanism — APPROVED WITH MODIFICATION (immutable version-identity
  comparison via `JobApplication` current pointers, not timestamps; block-on-stale confirmed).
- **D-007** Resume template — RESOLVED FOR V1 via `docs/RESUME_OUTPUT_STRUCTURE.md`; M6 unblocked.
- **D-008** Token/cost visibility — APPROVED WITH REPRIORITIZATION (token consumption is the v1
  requirement; dollar cost optional/deferred, not a blocker).
- **D-009** Capability flags — APPROVED (as originally proposed).
- **D-010** Stage-artifact versioning — APPROVED (append-only versioned JRA/FitAssessment/
  ResumeDraft; consolidates the former D-013).
- **D-011** Application lifecycle/abandonment — APPROVED, folded into the dashboard's
  `pipeline_phase`/`application_outcome` model rather than a standalone flag.
- **D-012** `JobApplication` aggregate — APPROVED.
- **D-013** — SUPERSEDED by D-010 (merged).
- **D-014** Structured AJ→AC→AB traceability — new decision, APPROVED.

No decision remains blocking for Milestone M1. Milestones M2–M7 have no unresolved blocking
decisions either — D-004's library choice is deferred to M4 by design, not blocked.

## Next action

Milestone M1 (Django/PostgreSQL application foundation) is the next implementation milestone, to
begin only after this M0 review/commit. Follow `docs/IMPLEMENTATION_PLAN.md`'s corrected
dependency graph and milestone-by-milestone acceptance criteria.

## Maintenance rule for this file

Update this file after any milestone completes or any meaningful implementation step lands.
Describe the repository as it truly is — never mark something present, tested, or working that
has not actually been built and verified.
