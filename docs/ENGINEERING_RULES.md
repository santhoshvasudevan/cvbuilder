# CVBuilder V2 Engineering Rules

**Status:** Canonical, tool-neutral
**Date:** 2026-09-08

This is the canonical engineering agreement for CVBuilder V2. It applies to every coding agent working on this repository — Claude Code, Codex, or any future tool. Tool-specific files (e.g. `CLAUDE.md`) point here; they do not duplicate this content, and if they ever conflict with it, this document governs.

See `AGENTS.md` for the short entry-point checklist. This document is the detail behind it.

## A. Sources of Truth and Precedence

When documents, code, or instructions disagree, resolve the conflict using this precedence, highest first:

1. **Explicit current product-owner instruction** — what you were just told to do, in this session, by the product owner.
2. **`requirements.md`** — the product requirements baseline.
3. **`docs/DECISIONS.md`**, approved/current decisions — architectural decisions that have been explicitly closed. A decision here overrides conflicting text anywhere else, including in this document.
4. **`docs/ARCHITECTURE.md`** — the system design that implements the requirements and decisions.
5. **`docs/IMPLEMENTATION_PLAN.md`** — how the architecture is delivered milestone by milestone.
6. **`docs/TEST_STRATEGY.md`** / **`docs/REQUIREMENT_TRACEABILITY.md`** — how requirements are verified and traced.
7. **`docs/CURRENT_STATE.md`** — the last-recorded operational snapshot.
8. **Existing implementation/tests** — what the code actually does today.
9. **Agent assumptions** — anything not written down anywhere above. Lowest precedence; if you find yourself relying on this level, stop and either find the answer in a higher-precedence source or ask.

Two important qualifications:

- **`docs/CURRENT_STATE.md` describes intended/last-known state, not verified fact.** It must be checked against the actual repository (git log, git status, running tests) before being trusted. If it disagrees with what you observe, the observation wins and `CURRENT_STATE.md` needs correcting.
- **Git history, the actual implementation, and passing deterministic tests establish the actual repository state.** Documentation describes intent; code and tests describe reality. When they diverge, say so explicitly rather than silently trusting one.
- **Audit/history documents — `docs/V2_REUSE_AUDIT.md`, `docs/QUALITY_BENCHMARK.md` — are evidence and history, not canonical architecture truth.** They record what was found or measured at a point in time. Where they and the canonical documents above disagree, the canonical documents govern; the audit/history document is corrected only to add a forward pointer ("resolved by V2-D0XX"), never silently rewritten to pretend the ambiguity was already decided when it was recorded.

## B. Architectural Invariants

These are the current V2 load-bearing rules. This list is a summary for orientation — `docs/ARCHITECTURE.md` and `docs/DECISIONS.md` are the canonical, detailed source; when in doubt, read them, not just this summary.

- Positioning-first product objective: the system optimizes for expert-quality candidate positioning, not just evidence retrieval.
- The operator owns final factual approval; the system does not self-certify factual correctness.
- Deterministic factual checks split into `HARD_INTEGRITY` (fails closed: malformed output, dangling object references, static-metadata corruption, wrong `ExperienceSlot` cardinality) and `SOFT_REVIEW_WARNING` (never blocks: weak evidence, overstatement, inferred positioning) — only soft warnings are advisory (V2-D026).
- AJ produces a `RecruiterDecisionModel`, not just a flat requirement list.
- `CandidateContextSnapshot` has five buckets: direct match, differentiators, career narrative, foundations, gaps/constraints — owned by `candidate_context`, a distinct app from `candidate_memory` (V2-D030).
- `AC_ASSESS` is one strong LLM assessment call by default. Do not recreate V1's `AC_NORMALIZE`/`AC_RANK`/`AC_MATCH` chain, or fold `AB_BUILD` back into AC, without fresh measured evidence (V2-D005/D029).
- APS (Agent Positioning Strategy) is mandatory and first-class, sitting between AC and Human Gate 1. It has explicit quality acceptance criteria that must pass before AB is implemented (V2-D027).
- AB is multi-pass: `PLAN → DRAFT → (deterministic warnings) → CRITIQUE → REFINE`, with bounded automatic refinement (one critique/refinement cycle by default; further loops require explicit operator action).
- Exactly three active primary `ExperienceSlot`s are required for the current V2 release — enforced as a `HARD_INTEGRITY` check, not a fixed database column count. `ExperienceSlot` selection is explicitly operator-controlled, never automatic (V2-D024/D031).
- Experience metadata (company, title, location, dates) is static/operator-owned. The LLM generates experience bullets only.
- Certifications/languages are not LLM-generated initially; `selected_projects[]` is deferred.
- `StageRun` and `JobApplicationStageState` belong to the workflow layer (`job_applications`), not `llm_provider` (V2-D022).
- `llm_provider` owns `LLMProvider`, `LLMModel`, `StageModelAssignment`, `LLMCallLog`, and provider adapters — nothing else. `LLMCallLog` references the initiating `StageRun`.
- One canonical stage vocabulary (owned by `job_applications`) is shared by `StageRun`, `JobApplicationStageState`, and `StageModelAssignment`; `StageModelAssignment` only applies to LLM-capable stages.
- `LLMModel.supported_reasoning_levels` is the canonical source of a model's reasoning capability; `supports_reasoning` is a derived helper only, never independently stored (V2-D034).
- Provider SDKs never leak into pipeline/domain apps — only `llm_provider`'s adapters import them.
- PostgreSQL is durable workflow truth. No workflow correctness depends on an in-memory or session-local agent state.
- No LangGraph/LangChain/agent-framework dependency for orchestration unless a later measured need is explicitly approved (V2-D016).
- Selective reuse from `main`: never bulk-merge. Reuse is file-by-file/commit-by-commit with tests, classified `REUSE_AS_IS` / `REUSE_WITH_ADAPTATION` / `DO_NOT_REUSE` — see `docs/V2_REUSE_AUDIT.md`.
- The repository must support safe continuation by a different coding agent without access to the previous agent's conversational context (V2-D035) — this is why this document, `AGENTS.md`, and `docs/HANDOVER_PROTOCOL.md` exist.

## C. Git Working Agreement

- **Verify branch before working.** Run `git branch --show-current` and `git log -5 --oneline --decorate` at the start of every session before making any change.
- **Never start work on an unclean or not-understood working tree.** Run `git status --short` and `git diff --stat` first. If there are uncommitted changes you did not just make, inspect them (`git diff`) and understand what they are before touching anything nearby.
- **Never discard another agent's uncommitted work blindly.** Uncommitted changes are not assumed to be disposable — see `docs/HANDOVER_PROTOCOL.md` §B. If a change genuinely needs to be discarded, say so explicitly and either get authorization or prove the change is incorrect.
- **Never use destructive operations** (`git reset --hard`, `git clean -f`, `git checkout -- <path>` over unreviewed changes, force-push, history rewrite) **without explicit authorization** for that specific action, in that specific instance.
- **Small, coherent commits.** One milestone/concern per commit where practical. Do not mix unrelated documentation changes, migrations, and feature work in a single commit.
- **Review `git diff` before every commit.** Confirm only the intended files are staged and no secret or local-runtime file is included.
- **Meaningful commit messages** that describe what changed and why, not just "wip" or "fix".
- **No force-push or rebase of shared history** (i.e. anything already pushed/shared) without explicit authorization.
- **Do not commit unless explicitly asked**, or unless the current task's instructions explicitly authorize committing as part of the task (as some milestone-closure tasks do). When in doubt, stop before the commit step and report readiness instead of committing.

## D. Database and Migration Agreement

- All schema changes are represented by Django migrations. No undocumented manual database schema modification.
- Migrations are committed together with the corresponding model changes and tests — not as an afterthought in a later commit.
- Migration ordering must be reproducible from a fresh database (a clean `migrate` from zero must produce the current schema, with no manual out-of-band steps).
- Agents must not fake or force migration state (e.g. `--fake`) merely to make a test suite appear to pass. If a migration is genuinely broken, fix the migration.
- PostgreSQL remains the expected application database — no SQLite fallback for anything but a documented, explicitly-scoped test convenience if one is ever introduced (none exists as of this writing; see `requirements.md` Non-Goals).

## E. Testing Agreement

- Tests establish **verification**, not implementation intent. A test passing means the described behavior is currently true; it does not by itself justify the behavior being correct — check it against the requirement/decision it claims to verify.
- Run focused tests relevant to the code you changed during development, not just at the end.
- Run the milestone's acceptance tests (see `docs/IMPLEMENTATION_PLAN.md`'s per-milestone Acceptance section and `docs/MILESTONE_COMPLETION_CHECKLIST.md`) before claiming that milestone complete.
- Run the broader deterministic suite before handover or commit where reasonable (i.e., where the change could plausibly have broken something outside the immediate area).
- Never weaken a legitimate test merely to get it green. If a test is genuinely obsolete because a requirement or decision changed, document the requirement/decision change first (in `docs/DECISIONS.md` and/or `requirements.md`), then update the test intentionally, and say in your report that you did so and why.
- Live provider/network tests remain explicit opt-in only — never run automatically, never required for a deterministic test suite to pass. See `docs/TEST_STRATEGY.md` §2.
- Report exact test commands and their results in `docs/CURRENT_STATE.md` and in any handover record — not just "tests pass," but which command was run and what it reported.

## F. Documentation Agreement

Update the relevant canonical document whenever you make a decision or change that affects it — do this as part of the same unit of work, not as a follow-up "someday":

- **`requirements.md`** — when a product requirement changes, is added, or is clarified.
- **`docs/DECISIONS.md`** — when an architectural ambiguity is resolved or a prior decision is superseded. Every closed decision gets a `V2-D0XX` entry here, even if it is also reflected elsewhere.
- **`docs/ARCHITECTURE.md`** — when the system design changes to reflect a decision.
- **`docs/IMPLEMENTATION_PLAN.md`** — when milestone scope, order, or acceptance criteria change.
- **`docs/TEST_STRATEGY.md`** / **`docs/REQUIREMENT_TRACEABILITY.md`** — when verification approach or requirement-to-component mapping changes.
- **`docs/CURRENT_STATE.md`** — at every meaningful milestone boundary (see `docs/MILESTONE_COMPLETION_CHECKLIST.md`), and whenever you hand off mid-task (see `docs/HANDOVER_PROTOCOL.md`).

Explicit rules:

- Do not put important project decisions only in chat. If it matters beyond this conversation, it belongs in a canonical document.
- Do not create competing canonical documents. There is exactly one canonical architecture document (`docs/ARCHITECTURE.md`), one canonical decision log (`docs/DECISIONS.md`), etc. If you need a new document, make clear whether it is canonical or an audit/history/methodology record, and do not let a new document silently start contradicting an existing canonical one.
- Audit/history documents must be labeled as such at the top of the file (see `docs/V2_REUSE_AUDIT.md` for the pattern) so a future agent does not mistake them for canonical architecture.
- `docs/CURRENT_STATE.md` must remain concise and operational — a snapshot to verify against, not a full history. Full history belongs in `docs/DECISIONS.md` (decisions) and Git history (implementation).

## G. Secret / Local-State Agreement

The following must never be committed to version control:

- `.env` (actual environment values/credentials)
- `.venv/` (or any local virtual environment directory)
- Credentials, API keys, tokens, or any other secret material, in any file
- Server PID files and log files
- Caches (e.g. `.ruff_cache/`, `__pycache__/`, build artifacts)
- Local temporary files
- OS metadata (`.DS_Store` and similar)

`.env.example` (once established in M1) must document every required environment key using safe placeholder values only — never a real credential, even an expired or low-privilege one.

Before staging or committing, review `git status`/`git diff` for exactly this class of file. If you see anything suspicious that might reveal a secret — even in a filename that looks innocuous — double-check the file's actual contents before proceeding.
