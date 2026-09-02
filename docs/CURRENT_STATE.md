# Current State

Last updated: 2026-09-02 (M0.1 — documentation-only Candidate Memory refinement completed; still
pre-implementation).

## Summary

This repository is **pre-implementation**. The M0 planning baseline (requirements analysis,
architecture, domain model, milestones, decisions log, test strategy, traceability matrix)
underwent product-owner review, and a follow-up **M0.1 documentation-only refinement** of the
Candidate Memory prerequisite has now also been completed. Across both reviews: several decisions
were approved (with modification, in most cases), two new decisions were added and approved
(D-014, structured AJ→AC→AB traceability; D-015, operator-approved Candidate Memory bootstrap/
classification/reference export), and `requirements.md` was explicitly amended by the product
owner both times to capture the new/clarified requirements (see its own Amendment Log). Three
candidate-profile markdown files now exist in the repository as **committed, operator-approved
bootstrap evidence sources** (`docs/AC/AC-MEMORY_PROFILE.md`, `docs/AC/AC-profile_english.md`,
`docs/AC/AC-profile_german.md`); they have not been modified by this review, only read and
referenced. The Candidate Memory bootstrap command and its UI are **designed, not implemented** —
there is still no Django project, no database, no dependencies installed, no migrations, no
models, no tests, and no application code of any kind. No LLM provider has been called.

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
  token-first observability, and now the Candidate Memory bootstrap/classification/reference-export
  rules from D-015).
- `docs/AC/AC-MEMORY_PROFILE.md`, `docs/AC/AC-profile_english.md`, `docs/AC/AC-profile_german.md`
  — **new**: committed, operator-approved candidate-profile source files that will serve as the
  initial Candidate Memory bootstrap evidence in Milestone M3. Read in full during this review;
  not modified.
- `docs/CANDIDATE_MEMORY_SNAPSHOT.md` — **new**: a concise, pre-M3, human-readable reference
  synthesized from the three source files above. Explicitly marked as not an evidence source, not
  authoritative runtime state, not default LLM context, and never to be re-ingested — it will
  later be regenerated from the activated PostgreSQL Candidate Memory revision once M3 exists.

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
- **D-015** Operator-approved Candidate Memory bootstrap, content classification, and reference
  export — new decision (M0.1), APPROVED. Names the three bootstrap files and their precedence,
  requires English-canonical claims with multi-language provenance support, requires evidence/
  constraint/positioning content classification, requires explicit contradiction detection and
  blocking, requires an explicit (never automatic) bootstrap command, and confirms no vector
  database is required for v1.

No decision remains blocking for Milestone M1. Milestones M2–M7 have no unresolved blocking
decisions either — D-004's library choice is deferred to M4 by design, not blocked. Milestone M3
now has a substantially more detailed, D-015-driven design (bootstrap command, classification,
conflict handling, multi-support provenance, ongoing-update UI) but is likewise not blocked.

## Next action

Milestone M1 (Django/PostgreSQL application foundation) remains the next implementation milestone,
to begin only after this M0.1 review/commit. Follow `docs/IMPLEMENTATION_PLAN.md`'s corrected
dependency graph and milestone-by-milestone acceptance criteria. Milestone M3's expanded scope
(D-015) is ready to implement against once M1/M2 are done — no further product-owner input is
required to begin M3, since the three bootstrap source files already exist in the repository.

## Maintenance rule for this file

Update this file after any milestone completes or any meaningful implementation step lands.
Describe the repository as it truly is — never mark something present, tested, or working that
has not actually been built and verified.
