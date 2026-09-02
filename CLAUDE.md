# Repository Instructions

This is a from-scratch, standalone agentic resume-generation app (Django + PostgreSQL, local-
first, single operator). It is **not** the `career-intelligence` project — do not inspect or copy
its implementation code. References to it in `requirements.md` are architectural lessons and
rationale, cited to explain *why*, not permission to create a code dependency on it.

## Before doing anything

1. Read `requirements.md` in full. It is the **authoritative product requirement source** — never
   edit it to make an ambiguity go away, and never treat a recommendation (yours or a prior
   session's) as if it were a requirement.
2. Inspect actual repository state (`git status`, `git log`, the filesystem) before acting. Don't
   assume anything is implemented, tested, or configured without checking.
3. Read `docs/CURRENT_STATE.md` before any substantial implementation work — it describes the
   repository's true current state and what's blocking or unblocked.

## Durable invariants (do not weaken these, ever)

- **No-fabrication**: every claim that ends up in a generated resume must trace back to a
  `confirmed` `MemoryClaim`. Enforce this with a real validator after generation, not prompting
  alone.
- **No-concealment**: Agent Candidate's gap analysis must never hide or soften a genuine mismatch
  to make the fit look better.
- **Provenance**: every `MemoryClaim` keeps a pointer back to its source document/text. Never lose
  this when building or refactoring the memory model.
- **Human approval as a DB precondition**: both review gates are plain application state (a status
  field, checked fresh at the start of the next step) — never a paused agent/orchestration-graph
  execution waiting to be resumed. Do not introduce a framework-level "interrupt and resume"
  mechanism for these gates.
- **Provider independence**: pipeline code (memory build, Agent Jobber, Agent Candidate, Agent
  Builder) must call the `llm_provider` adapter interface only, never an OpenAI/NVIDIA/Gemini SDK
  directly. Adding a provider must never require touching pipeline logic.
- **Secrets**: provider credentials live in environment variables/`.env`, never in the database or
  version control. The DB registry stores credential *references* (env var names), never values.
- **Avoid unnecessary infrastructure**: no Redis, no Celery, no message broker, no SPA, no cloud
  deployment, no LangGraph/LangChain/OpenAI Agents SDK — a post-M7 architecture checkpoint
  (D-001) is the only point where adopting one of these is even reconsidered, and only if a
  concrete need has appeared (dynamic routing, parallel branches, tool-calling loops,
  resumability-after-crash, agent delegation) — never for observability alone. Record any such
  decision in `docs/DECISIONS.md` before acting on it.
- **`JobApplication` is the aggregate root**: it groups the versioned chain of
  `JobRequirementAnalysis` → `FitAssessment` → `ResumeDraft` via current-version pointers, and owns
  the dashboard lifecycle (`pipeline_phase`/`application_outcome`). Freshness/staleness is
  determined by comparing a downstream artifact's stored upstream-version pointer against
  `JobApplication`'s current pointer — an identity comparison, never a timestamp comparison.
- **Structured AJ → AC → AB traceability is load-bearing, not decorative**: Agent Jobber assigns
  stable `JobRequirement` IDs; Agent Candidate must produce an explicit disposition
  (`MATCH`/`PARTIAL`/`GAP`/`UNKNOWN`) for every relevant requirement, never silently defaulting
  absence-of-evidence to `MATCH`; Agent Builder's structured output elements must each carry
  `supporting_memory_claim_ids` validated for existence/confirmation/eligibility before any
  markdown is rendered. These are validators to write and enforce, not just prompts to word
  carefully. The validators check evidence *attachment*, never text/embedding similarity as a
  fabrication test — human review at the gates remains responsible for whether wording fairly
  represents the cited evidence.
- **Django admin auth is allowed and expected**: the "no multi-tenant auth" non-goal is about
  product-level accounts/tenants/roles, not about `django.contrib.admin`/`auth`/`sessions`/
  `contenttypes`, which are required for provider/model registry administration. Don't remove or
  avoid them in the name of "no auth."
- **Token consumption is the v1 observability priority, not dollar cost.** `LLMCallLog` must
  record input/cached-input/output/total tokens per call; dollar-cost calculation is optional and
  deferred, and must never block a milestone. If pricing is added later, historical cost uses
  pricing snapshotted at call time, not recalculated against a changed future price.
- **Candidate Memory bootstrap sources are named and precedence-ordered**: `docs/AC/
  AC-MEMORY_PROFILE.md` (highest precedence, safe-wording/policy authority), `docs/AC/
  AC-profile_english.md`, and `docs/AC/AC-profile_german.md` are operator-approved evidence — never
  edit these three files. English is the canonical language for stored `MemoryClaim` facts; German
  evidence supports the same canonical claim rather than creating a separate one.
- **Classify before storing as a claim**: source content splits into an evidence plane (may become
  a resume-eligible `MemoryClaim`), a constraint plane (cautions/limitations — becomes a
  `CandidateRule`, never resume evidence), and a positioning plane (suggested titles, tailoring
  guidance — also a `CandidateRule`, never resume evidence). Don't flatten every source bullet into
  a factual claim, and never let a suggested target title be stored or presented as an actual
  historical job title.
- **Provenance is multi-support, and contradictions block, not guess**: a claim can have more than
  one exact supporting quotation (`MemoryClaimSupport`); an unresolved contradiction
  (`MemoryConflict`, `OPEN`) makes every claim it involves ineligible until the operator resolves
  it — never silently prefer one source over another.
- **Bootstrap is explicit, never automatic**: Candidate Memory bootstrap runs only via a
  deliberate, operator-invoked management command — never on `migrate`, `runserver`, app startup,
  or `manage.py test`. Ongoing corrections go through the Candidate Memory UI (a new source + a
  new revision), never direct database edits.
- **A `CandidateMemory` revision's mutability is a function of its `status`, not of whether it has
  been "created" yet**: while `status` is `BUILDING` or `NEEDS_REVIEW`, its claims, supports,
  rules, and conflicts may be freely confirmed, corrected, retired, restored, or resolved — that is
  the normal review workflow, not an exception to immutability. The moment a revision becomes
  `ACTIVE`, all of that content freezes permanently; the UI/service layer must refuse further edits
  to it. Activation is always an explicit operator action, with exactly one `ACTIVE` revision at a
  time; it is blocked if a validation failure could let unsupported content become eligible, but an
  unresolved conflict does **not** have to block activation as long as every claim it affects stays
  `BLOCKED_CONFLICT` — the activation UI must warn the operator when that's happening. Any
  correction after activation — including resolving a conflict left open at activation time —
  happens by creating a **new** revision through the ongoing-update workflow, never by mutating the
  `ACTIVE` one. Treat any design or code that edits an `ACTIVE` revision's factual content in place
  as a bug, not a shortcut.
- **`docs/CANDIDATE_MEMORY_SNAPSHOT.md` is a reference export, not evidence**: do not load it by
  default, treat it as Candidate Memory evidence, re-ingest it, or pass it to a runtime pipeline
  prompt unless the product owner explicitly requests that for a specific task. Runtime retrieval
  (Agent Candidate/Agent Builder) must stay bounded and explainable — never the complete source
  documents, never a full memory dump, never the snapshot, and no vector database/embedding
  infrastructure for v1 (PostgreSQL structured retrieval + bounded LLM ranking is sufficient).

## Working process

- Work milestone-by-milestone per `docs/IMPLEMENTATION_PLAN.md` and its dependency graph (M3 and
  M4 both depend only on M2, not on each other — don't assume a dependency that isn't there, and
  don't parallelize coding just because the graph allows it unless asked). Don't skip ahead past
  an unmet dependency or an unresolved blocking decision (check `docs/DECISIONS.md` — anything
  `PROPOSED` and flagged "needs approval" is not yet authorized to implement; only the product
  owner sets a decision to `APPROVED`).
- `requirements.md` may be amended, but only with the product owner's explicit authorization in
  that session — never inferred from context. When authorized, preserve the rest of the document
  and record what changed in its own Amendment Log rather than silently rewriting history.
- Run real verification (tests, migrations, a manual walkthrough) before claiming a milestone or
  requirement is done. Never mark something "Implemented" or "Passing" in
  `docs/REQUIREMENT_TRACEABILITY.md` without a real artifact and a real check behind it.
- After meaningful implementation work: update `docs/CURRENT_STATE.md` to reflect what's actually
  true, update `docs/REQUIREMENT_TRACEABILITY.md` for any requirement whose status genuinely
  changed, and record any new architectural decision in `docs/DECISIONS.md` (as `PROPOSED` unless
  the product owner has explicitly approved it in that same session).
- Never weaken a test to obtain a green result. If a test fails, fix the underlying issue or report
  it — don't loosen the assertion to make the failure disappear.
- Report unresolved failures and deviations from the plan explicitly and honestly, in the same
  turn you discover them — don't let a red test or a skipped requirement pass silently.
- Testing strategy is phased (`docs/TEST_STRATEGY.md`): ordinary deterministic tests for non-LLM
  logic from day one; no recorded/golden/cassette LLM-response testing until outputs stabilize —
  don't build that infrastructure early just because it seems achievable.
