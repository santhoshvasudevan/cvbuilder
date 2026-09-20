# AGENTS.md — CVBuilder V2 Agent Entry Point

This file is the tool-neutral entry point for **any** coding agent working on this repository — Claude Code, Codex, or any future agent. It contains no tool-specific behavior. If you are looking for tool-specific notes (e.g. `CLAUDE.md`), those files only add local operating notes on top of what is written here; they never override it.

The repository — Git history, canonical documentation, tests, and `docs/CURRENT_STATE.md` — is the project's source of truth. A prior chat/session's conversational context is not available to you and must never be assumed. Work as if you are the first agent to ever open this repository, and let the repository tell you what has actually happened.

## Before doing anything

1. Confirm branch, HEAD, and git status before making any change:
   ```
   git branch --show-current
   git log -5 --oneline --decorate
   git status --short
   ```
2. Load repository context according to the invocation mode. This controls how much to read, not
   precedence; the canonical precedence chain remains exactly as defined in
   `docs/ENGINEERING_RULES.md` §A.
   - **Controller-invoked role with a phase contract:** only when the agent was invoked by
     `tools/dev_orchestrator` and received a phase contract that names the applicable requirement,
     architecture, and decision sections, load context in these tiers:
     - **Every session:** read `AGENTS.md`; confirm branch, HEAD, and status; inspect the applicable
       durable run state (`.orchestration/runs/<run-id>/state.json`); read the worktree-local
       `docs/CURRENT_STATE.md`; read the current milestone section in
       `docs/IMPLEMENTATION_PLAN.md`; and read the sections of `docs/ENGINEERING_RULES.md` relevant
       to the task.
     - **Task-dependent:** read only the requirement, architecture, and decision sections explicitly
       named by the phase contract. Follow references needed to understand those named sections, but
       do not preload the entire canonical set by default.
     - **Milestone boundary:** perform one full canonical reconciliation across `requirements.md`,
       `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md`,
       `docs/TEST_STRATEGY.md`, `docs/REQUIREMENT_TRACEABILITY.md`, `docs/CURRENT_STATE.md`,
       `docs/DEVELOPMENT_ORCHESTRATION.md`, `docs/DEVELOPMENT_RUNBOOK.md`,
       `docs/RESUME_OUTPUT_STRUCTURE.md`, `docs/V2_REUSE_AUDIT.md`, and
       `docs/QUALITY_BENCHMARK.md`.
     - **Escape hatch:** if `docs/CURRENT_STATE.md` contradicts Git or durable run state, or the task
       touches anything not covered by the phase contract's named sections, read and reconcile the
       full canonical set above before proceeding. Never weaken or skip this escalation.
   - **Every other session:** for an interactive or manual session, or any invocation without such a
     phase contract, read the full canonical repository set before proceeding, in the precedence
     order defined by `docs/ENGINEERING_RULES.md` §A:
     - `requirements.md`
     - `docs/DECISIONS.md`
     - `docs/ARCHITECTURE.md`
     - `docs/IMPLEMENTATION_PLAN.md`
     - `docs/TEST_STRATEGY.md`
     - `docs/REQUIREMENT_TRACEABILITY.md`
     - `docs/CURRENT_STATE.md`
     - `docs/DEVELOPMENT_ORCHESTRATION.md`
     - `docs/DEVELOPMENT_RUNBOOK.md`
     - `docs/RESUME_OUTPUT_STRUCTURE.md`
     - `docs/V2_REUSE_AUDIT.md` and `docs/QUALITY_BENCHMARK.md` (audit/history and methodology
       records — see below)
   - **Controller-tooling override:** regardless of tier, any task that invokes, modifies, or reasons
     about controller tooling must read `docs/DEVELOPMENT_ORCHESTRATION.md` and
     `docs/DEVELOPMENT_RUNBOOK.md`.

## Standing rules

3. Treat repository state — Git history, the canonical docs above, and passing deterministic tests — as stronger evidence than any assumption carried over from a prior chat/session context. If something in a document conflicts with what the code and tests actually show, the code/tests describe *actual* state and the docs describe *intended* state; reconcile explicitly rather than picking one silently.
4. Never assume a previous agent finished a task just because a document says it did. `docs/CURRENT_STATE.md` describes intended/last-known state — it must be verified, not trusted blindly.
5. Verify claimed functionality through code inspection and tests, not by re-reading the document that claims it.
6. Work only inside the currently authorized milestone/task. Do not start a later milestone while an earlier dependency is incomplete (see `docs/IMPLEMENTATION_PLAN.md`'s dependency graph), and do not silently expand scope beyond what was asked.
7. Do not silently change architecture, requirements, invariants, or milestone scope. If something needs to change, record it as a new or superseding entry in `docs/DECISIONS.md` and propagate it to the other canonical docs it affects — never leave a change implicit in code or in chat only.
8. Record architecture/product changes in the canonical docs (`requirements.md`, `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/TEST_STRATEGY.md`, `docs/REQUIREMENT_TRACEABILITY.md`, `docs/CURRENT_STATE.md`) — never only in a chat reply or commit message.
9. Never weaken or remove a legitimate test merely to make it pass. If a test is genuinely obsolete because a requirement changed, document the requirement/decision change first, then update the test intentionally and say so.
10. Never commit secrets or local runtime state (`.env`, `.venv/`, credentials, PID/log files, caches, OS metadata). See `docs/ENGINEERING_RULES.md` §G.
11. Keep database changes reproducible through Django migrations. No undocumented manual schema changes; see `docs/ENGINEERING_RULES.md` §D.
12. Keep provider-specific logic isolated in `llm_provider`. Pipeline/domain apps never import a provider SDK directly.
13. Preserve PostgreSQL as the durable application/workflow source of truth. No workflow correctness may depend on an in-memory or session-local state.
14. Update `docs/CURRENT_STATE.md` and `docs/REQUIREMENT_TRACEABILITY.md` after meaningful milestone completion — see `docs/MILESTONE_COMPLETION_CHECKLIST.md`.
15. Prefer small, coherent commits: one milestone/concern per commit where practical. Do not mix unrelated documentation, migrations, and feature work in a single commit.
16. Support safe handover to another agent at any point — see `docs/HANDOVER_PROTOCOL.md`. Assume the next agent to touch this repository may be a different tool with no memory of this session.
17. Before continuing partially completed work, inspect `git diff` and run the relevant tests first. Do not assume uncommitted changes are correct, complete, or safe to build on top of — see `docs/HANDOVER_PROTOCOL.md` §B.
18. Stop and report contradictions instead of guessing. If canonical docs disagree with each other, with the code, or with what you were just asked to do, say so explicitly and ask, rather than silently picking an interpretation.

## Where the detailed rules live

- **Engineering agreement** (precedence rules, architectural invariants, Git/database/testing/documentation/secrets rules): `docs/ENGINEERING_RULES.md`
- **Handover protocol** (clean milestone handover and emergency/mid-task handover): `docs/HANDOVER_PROTOCOL.md`
- **Milestone completion checklist**: `docs/MILESTONE_COMPLETION_CHECKLIST.md`
- **Current operational state**: `docs/CURRENT_STATE.md`
- **Reuse audit** (what was found reusable on `main`, and why): `docs/V2_REUSE_AUDIT.md` — an audit/history record, not canonical architecture
- **Quality benchmark methodology**: `docs/QUALITY_BENCHMARK.md`

## Local development commands

M1 established the Django project, `.env.example`, and root `Makefile`; read the Makefile directly
for the canonical application commands. The external development-agent controller is documented in
`docs/DEVELOPMENT_ORCHESTRATION.md` and operated through `python -m tools.dev_orchestrator` as
described in `docs/DEVELOPMENT_RUNBOOK.md`. Its local `.orchestration/runs/` and worktree directories
are never product workflow state and must not be committed. Within each run directory, `handoffs/`
holds only controller pipeline artifacts that conform to the controller schemas; independently
commissioned supplemental audits (for example Claude reviews with a different JSON schema) belong
under the sibling `supplemental-audits/` directory via
`python -m tools.dev_orchestrator record-supplemental-audit --run-id <run-id> --file <json-file>`
and are evidence only until translated through an authorized, schema-valid controller path. Agent
prompts carry a worktree-local startup identity
(branch or `DETACHED`, HEAD, durable run ID/state loaded from that run's `state.json`, and the exact
`docs/CURRENT_STATE.md` path/hash constrained inside the request worktree); never substitute the root
checkout copy.
